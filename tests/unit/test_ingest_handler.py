import pytest

from ingest.ba_client import BAApiError, BANotFound
from ingest.handler import LIST_INDEX_KEY, IngestFailed, list_hash, run_ingest


class FakeStore:
    def __init__(self, initial=None):
        self.objects = dict(initial or {})

    def put_json(self, key, obj):
        self.objects[key] = obj

    put_json_gz = put_json

    def put_jsonl_gz(self, key, records):
        self.objects[key] = list(records)

    def get_json_gz(self, key):
        return self.objects.get(key)

    def keys(self, part):
        return sorted(k for k in self.objects if part in k)


class FakeClient:
    """search_results: {term: [page1_items, page2_items, ...] or an Exception}; details: {refnr: dict or Exception}."""

    def __init__(self, search_results, details=None):
        self.search_results = search_results
        self.details = details or {}
        self.detail_calls = []

    def search(self, term, page, size):
        result = self.search_results[term]
        if isinstance(result, Exception):
            raise result
        total = sum(len(p) for p in result)
        items = result[page - 1] if page <= len(result) else []
        return {"ergebnisliste": items, "maxErgebnisse": total, "page": page, "size": size}

    def detail(self, refnr):
        self.detail_calls.append(refnr)
        value = self.details.get(refnr, {"referenznummer": refnr, "stellenangebotsBeschreibung": "text"})
        if isinstance(value, Exception):
            raise value
        return value


def posting(refnr, changed="2026-10-20"):
    return {"referenznummer": refnr, "stellenangebotsTitel": f"Data Engineer {refnr}", "aenderungsdatum": changed}


def run(client, store, terms=("Data Engineer",), max_details=500, max_pages=50, seconds_left=lambda: 900, full=True):
    return run_ingest(
        client, store, list(terms), "run-1", "2026-10-24", max_details, max_pages, seconds_left, full_term_list=full
    )


def test_first_run_fetches_all_details_and_writes_raw_files():
    client = FakeClient({"Data Engineer": [[posting("A"), posting("B")]]})
    store = FakeStore()
    manifest = run(client, store)

    assert client.detail_calls == ["A", "B"]
    assert manifest["complete"] is True
    assert manifest["postings_listed"] == 2
    assert manifest["details_fetched"] == 2
    prefix = "raw/postings/ingest_date=2026-10-24/run_id=run-1/"
    search_lines = store.objects[prefix + "search_data-engineer_001.jsonl.gz"]
    assert search_lines[0]["posting"] == posting("A")  # raw record is stored unchanged
    assert search_lines[0]["meta"]["search_term"] == "Data Engineer"
    assert len(store.objects[prefix + "details_001.jsonl.gz"]) == 2
    assert store.objects["raw/manifests/run_id=run-1.json"] == manifest
    assert set(store.objects[LIST_INDEX_KEY]) == {"A", "B"}


def test_unchanged_postings_are_skipped_and_changed_ones_refetched():
    index = {"A": list_hash(posting("A")), "B": list_hash(posting("B", changed="2026-10-01"))}
    client = FakeClient({"Data Engineer": [[posting("A"), posting("B"), posting("C")]]})
    manifest = run(client, FakeStore({LIST_INDEX_KEY: index}))

    assert client.detail_calls == ["B", "C"]  # A unchanged, B modified at source, C new
    assert manifest["postings_changed"] == 2


def test_max_details_defers_the_rest_without_marking_them_seen():
    client = FakeClient({"Data Engineer": [[posting(r) for r in "ABCDE"]]})
    store = FakeStore()
    manifest = run(client, store, max_details=2)

    assert client.detail_calls == ["A", "B"]
    assert manifest["details_deferred"] == 3
    assert set(store.objects[LIST_INDEX_KEY]) == {"A", "B"}  # C–E are fetched on the next run


def test_time_budget_stops_detail_fetching():
    remaining = iter([900, 900, 30, 30])
    client = FakeClient({"Data Engineer": [[posting(r) for r in "ABCD"]]})
    manifest = run(client, FakeStore(), seconds_left=lambda: next(remaining))

    assert client.detail_calls == ["A", "B"]
    assert manifest["details_deferred"] == 2


def test_failed_detail_is_counted_and_retried_next_run():
    client = FakeClient({"Data Engineer": [[posting("A"), posting("B")]]}, details={"A": BANotFound("gone")})
    store = FakeStore()
    manifest = run(client, store)

    assert manifest["details_failed"] == 1
    assert manifest["details_fetched"] == 1
    assert "A" not in store.objects[LIST_INDEX_KEY]


def test_pagination_follows_pages_and_deduplicates_across_terms():
    page1 = [posting(f"P{i}") for i in range(100)]
    page2 = [posting("Q1")]
    client = FakeClient({"Data Engineer": [page1, page2], "Big Data Engineer": [[posting("P1"), posting("Z")]]})
    store = FakeStore()
    manifest = run(client, store, terms=("Data Engineer", "Big Data Engineer"))

    assert manifest["search_pages"] == 3
    assert manifest["postings_listed"] == 102  # P1 counted once
    assert len(store.keys("search_data-engineer_")) == 2


def test_page_cap_marks_run_incomplete():
    pages = [[posting(f"{p}-{i}") for i in range(100)] for p in range(3)]
    manifest = run(FakeClient({"Data Engineer": pages}), FakeStore(), max_pages=2)

    assert manifest["complete"] is False
    assert manifest["terms_capped"] == ["Data Engineer"]


def test_failed_term_marks_incomplete_and_keeps_old_index_entries():
    index = {"OLD": "hash"}
    client = FakeClient({"Data Engineer": [[posting("A")]], "ETL Entwickler": BAApiError("down")})
    store = FakeStore({LIST_INDEX_KEY: index})
    manifest = run(client, store, terms=("Data Engineer", "ETL Entwickler"))

    assert manifest["complete"] is False
    assert manifest["terms_failed"] == ["ETL Entwickler"]
    assert "OLD" in store.objects[LIST_INDEX_KEY]  # not pruned: the listing was partial


def test_complete_run_prunes_postings_no_longer_listed():
    store = FakeStore({LIST_INDEX_KEY: {"GONE": "hash"}})
    run(FakeClient({"Data Engineer": [[posting("A")]]}), store)
    assert set(store.objects[LIST_INDEX_KEY]) == {"A"}


def test_subset_of_terms_is_never_complete_and_does_not_prune_index():
    store = FakeStore({LIST_INDEX_KEY: {"OTHER": "hash"}})
    manifest = run(FakeClient({"ETL Entwickler": [[posting("A")]]}), store, terms=("ETL Entwickler",), full=False)

    assert manifest["complete"] is False
    assert manifest["full_term_list"] is False
    assert "OTHER" in store.objects[LIST_INDEX_KEY]


def test_handler_marks_overridden_terms_as_partial(monkeypatch):
    import ingest.handler as h

    captured = {}
    monkeypatch.setenv("DATA_BUCKET", "bucket")
    monkeypatch.setattr(h, "S3Store", lambda bucket: FakeStore())
    monkeypatch.setattr(h, "run_ingest", lambda **kw: captured.update(kw) or {})

    class Ctx:
        def get_remaining_time_in_millis(self):
            return 900_000

    h.handler({"run_id": "r", "run_date": "2026-10-24", "search_terms": ["ETL Entwickler"]}, Ctx())
    assert captured["full_term_list"] is False
    assert captured["terms"] == ["ETL Entwickler"]
    assert captured["seconds_left"]() == 900

    h.handler({"run_id": "r", "run_date": "2026-10-24"}, Ctx())
    assert captured["full_term_list"] is True
    assert captured["terms"] == h.load_search_terms()


def test_more_than_half_of_terms_failing_raises_after_writing_manifest():
    client = FakeClient({"a": BAApiError("x"), "b": BAApiError("x"), "c": [[posting("A")]]})
    store = FakeStore()
    with pytest.raises(IngestFailed):
        run(client, store, terms=("a", "b", "c"))
    assert store.objects["raw/manifests/run_id=run-1.json"]["terms_failed"] == ["a", "b"]


def test_list_hash_is_independent_of_key_order():
    assert list_hash({"a": 1, "b": 2}) == list_hash({"b": 2, "a": 1})
