import pytest

from enrich import handler as h
from enrich.extract import Extraction

EVENT = {"run_id": "exec-1", "run_date": "2026-10-28"}
GOOD = {
    "skills": ["python"], "cloud_platforms": ["aws"], "seniority": "mid", "salary_min_eur": None,
    "salary_max_eur": None, "german_level_required": "none", "english_only": True,
    "visa_sponsorship_mentioned": False, "remote_policy": None,
}  # fmt: skip


class FakeAthena:
    def __init__(self, work):
        self.work = work
        self.selects = []
        self.inserts = []

    def rows(self, sql, label):
        self.selects.append(sql)
        return self.work

    def run(self, sql, label):
        self.inserts.append(sql)


class FakeStore:
    def __init__(self):
        self.objects = {}

    def put_jsonl_gz(self, key, records):
        self.objects[key] = list(records)


class FakeExtractor:
    """Fails postings whose refnr starts with 'bad', raises for 'boom'."""

    def __init__(self, client, model_id, prompt_version):
        self.model_id = model_id

    def extract(self, posting):
        if posting["refnr"].startswith("boom"):
            raise RuntimeError("throttled")
        x = Extraction(posting["refnr"], posting["content_hash"], attempts=1, input_tokens=100, output_tokens=10)
        if posting["refnr"].startswith("bad"):
            x.problems = ["seniority must be one of …"]
        else:
            x.fields = dict(GOOD)
        return x


class Ctx:
    def __init__(self, ms):
        self.ms = ms

    def get_remaining_time_in_millis(self):
        return self.ms


def postings(*refnrs):
    return [{"refnr": r, "content_hash": f"h-{r}", "title": "t", "employer": "e", "location": "l",
             "description": "d"} for r in refnrs]  # fmt: skip


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", "bucket")
    monkeypatch.setenv("MAX_ENRICH_PER_RUN", "50")
    monkeypatch.setattr(h, "Extractor", FakeExtractor)


def test_empty_work_list_returns_early():
    athena = FakeAthena([])
    stats = h.handler(EVENT, None, athena=athena, bedrock=object(), store=FakeStore())
    assert stats["candidates"] == 0
    assert athena.inserts == []
    assert "LIMIT 50" in athena.selects[0]
    assert "prompt_version = 'v1'" in athena.selects[0]


def test_writes_good_rows_and_dead_letters(monkeypatch):
    monkeypatch.setattr(h, "INSERT_BATCH", 2)
    athena, store = FakeAthena(postings("a", "b", "c", "bad1")), FakeStore()
    stats = h.handler(EVENT, Ctx(600_000), athena=athena, bedrock=object(), store=store)

    assert stats["enriched"] == 3
    assert stats["dead_letters"] == 1
    assert stats["input_tokens"] == 400
    assert len(athena.inserts) == 2  # 3 rows in batches of 2
    assert "'eu.amazon.nova-lite-v1:0'" in athena.inserts[0]
    assert "'exec-1'" in athena.inserts[0]
    results = store.objects["staging/enrichment/run_id=exec-1/results.jsonl.gz"]
    assert [r["refnr"] for r in results] == ["a", "b", "c"]
    dead = store.objects["staging/dead_letter/run_id=exec-1/enrich.jsonl.gz"]
    assert dead[0]["refnr"] == "bad1"
    assert "last_input" in dead[0]
    assert dead[0]["problems"]


def test_too_many_dead_letters_fails_after_writing_good_rows():
    athena, store = FakeAthena(postings("a", "bad1", "bad2", "boom1")), FakeStore()
    with pytest.raises(h.TooManyDeadLetters, match="3 of 4"):
        h.handler(EVENT, None, athena=athena, bedrock=object(), store=store)
    assert len(athena.inserts) == 1
    dead = store.objects["staging/dead_letter/run_id=exec-1/enrich.jsonl.gz"]
    assert any("RuntimeError: throttled" in d["problems"][0] for d in dead)


def test_low_on_time_skips_remaining_postings():
    athena, store = FakeAthena(postings("a", "b")), FakeStore()
    stats = h.handler(EVENT, Ctx(1_000), athena=athena, bedrock=object(), store=store)
    assert stats["skipped"] == 2
    assert athena.inserts == []
    assert store.objects == {}


def test_builds_default_clients(monkeypatch):
    created = {}
    monkeypatch.setattr(h, "Athena", lambda **kw: created.update(kw) or FakeAthena([]))
    h.handler(EVENT, None)
    created["log"]("logged without error")
    assert created["workgroup"] == "stellenradar-dev"
