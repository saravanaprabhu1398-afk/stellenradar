"""Ingest Lambda: BA job search → raw S3 (SDD §4.1).

Writes, under raw/ in the data bucket:
  postings/ingest_date=<d>/run_id=<r>/search_<term>_<page>.jsonl.gz   one line per listed posting
  postings/ingest_date=<d>/run_id=<r>/details_<batch>.jsonl.gz        one line per fetched detail
  manifests/run_id=<r>.json                                           run summary (also returned)
  state/list_index.json.gz                                            {refnr: list_hash} for change detection

Each line is {"meta": {...}, "posting"|"detail": <BA record exactly as returned>} so raw stays unchanged.
"""

import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.logging import get_logger
from common.storage import S3Store
from ingest.ba_client import BAApiError, BAClient, BANotFound

PAGE_SIZE = 100
DETAIL_BATCH_SIZE = 100
LIST_INDEX_KEY = "raw/state/list_index.json.gz"
TIME_RESERVE_S = 60  # stop fetching details when less than this remains
USER_AGENT = "stellenradar/0.1 (+https://github.com/saravanaprabhu1398-afk/stellenradar)"

log = get_logger("ingest")


class IngestFailed(Exception):
    """More than half of the search terms failed – the run must fail and alert."""


def list_hash(posting: dict) -> str:
    canonical = json.dumps(posting, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def refnr_of(record: dict) -> str | None:
    return record.get("referenznummer") or record.get("refnr")


def slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")


def load_search_terms() -> list[str]:
    return json.loads((Path(__file__).parent / "search_terms.json").read_text())


def run_ingest(
    client: Any,
    store: Any,
    terms: list[str],
    run_id: str,
    run_date: str,
    max_details: int,
    max_pages: int,
    seconds_left: Callable[[], float],
    full_term_list: bool = True,
) -> dict:
    prefix = f"raw/postings/ingest_date={run_date}/run_id={run_id}/"
    now = lambda: datetime.now(UTC).isoformat()  # noqa: E731

    # 1) Search: page through every term, collect today's listing.
    listed: dict[str, str] = {}  # refnr -> list_hash, in first-seen order
    failed_terms: list[str] = []
    capped_terms: list[str] = []
    search_pages = 0
    for term in terms:
        page = 1
        try:
            while True:
                response = client.search(term, page, PAGE_SIZE)
                search_pages += 1
                items = response.get("ergebnisliste") or []
                lines = []
                for item in items:
                    refnr = refnr_of(item)
                    if not refnr:
                        continue
                    h = list_hash(item)
                    listed.setdefault(refnr, h)
                    lines.append(
                        {
                            "meta": {"search_term": term, "page": page, "list_hash": h, "fetched_at": now()},
                            "posting": item,
                        }
                    )
                if lines:
                    store.put_jsonl_gz(f"{prefix}search_{slug(term)}_{page:03d}.jsonl.gz", lines)
                total = int(response.get("maxErgebnisse") or 0)
                if len(items) < PAGE_SIZE or page * PAGE_SIZE >= total:
                    break
                if page >= max_pages:
                    capped_terms.append(term)
                    break
                page += 1
        except BAApiError as e:
            failed_terms.append(term)
            log.warning("search term failed", extra={"fields": {"run_id": run_id, "term": term, "error": str(e)}})

    # Only a listing of *all* configured terms may be used to close postings (or prune the index):
    # a smoke run with a subset of terms would otherwise "close" everything it did not search for.
    complete = full_term_list and not failed_terms and not capped_terms

    # 2) Change detection against the previous run's index.
    previous: dict[str, str] = store.get_json_gz(LIST_INDEX_KEY) or {}
    changed = [r for r, h in listed.items() if previous.get(r) != h]
    to_fetch = changed[:max_details]
    deferred = len(changed) - len(to_fetch)

    # 3) Details, within the Lambda's time budget.
    index = dict(previous)
    batch: list[dict] = []
    batch_no = 0
    fetched = failed = 0

    def flush() -> None:
        nonlocal batch, batch_no
        if batch:
            batch_no += 1
            store.put_jsonl_gz(f"{prefix}details_{batch_no:03d}.jsonl.gz", batch)
            batch = []

    for i, refnr in enumerate(to_fetch):
        if seconds_left() < TIME_RESERVE_S:
            deferred += len(to_fetch) - i
            log.warning("time budget reached", extra={"fields": {"run_id": run_id, "remaining": len(to_fetch) - i}})
            break
        try:
            detail = client.detail(refnr)
        except BAApiError as e:  # includes BANotFound: retried tomorrow if still listed
            failed += 1
            log.warning(
                "detail failed",
                extra={
                    "fields": {
                        "run_id": run_id,
                        "refnr": refnr,
                        "error": str(e),
                        "not_found": isinstance(e, BANotFound),
                    }
                },
            )
            continue
        batch.append({"meta": {"refnr": refnr, "list_hash": listed[refnr], "fetched_at": now()}, "detail": detail})
        index[refnr] = listed[refnr]  # only fetched postings are marked as seen, so skipped ones retry next run
        fetched += 1
        if len(batch) >= DETAIL_BATCH_SIZE:
            flush()
    flush()

    # A complete listing lets us drop references that are no longer listed, keeping the index small.
    if complete:
        index = {r: h for r, h in index.items() if r in listed}
    store.put_json_gz(LIST_INDEX_KEY, index)

    manifest = {
        "run_id": run_id,
        "run_date": run_date,
        "complete": complete,
        "full_term_list": full_term_list,
        "terms": len(terms),
        "terms_failed": failed_terms,
        "terms_capped": capped_terms,
        "search_pages": search_pages,
        "postings_listed": len(listed),
        "postings_changed": len(changed),
        "details_requested": len(to_fetch),
        "details_fetched": fetched,
        "details_failed": failed,
        "details_deferred": deferred,
        "raw_prefix": prefix,
    }
    store.put_json(f"raw/manifests/run_id={run_id}.json", manifest)
    log.info("ingest finished", extra={"fields": manifest})

    if len(failed_terms) * 2 > len(terms):
        raise IngestFailed(f"{len(failed_terms)} of {len(terms)} search terms failed: {failed_terms}")
    return manifest


def handler(event: dict, context: Any) -> dict:
    """Event: {"run_id", "run_date"}; optional overrides for smoke tests: "search_terms", "max_details"."""
    run_id = event["run_id"]
    run_date = event["run_date"]
    override = event.get("search_terms")
    return run_ingest(
        client=BAClient(USER_AGENT),
        store=S3Store(os.environ["DATA_BUCKET"]),
        terms=override or load_search_terms(),
        run_id=run_id,
        run_date=run_date,
        max_details=int(event.get("max_details", os.environ.get("MAX_DETAILS", "500"))),
        max_pages=int(os.environ.get("MAX_PAGES", "50")),
        seconds_left=lambda: context.get_remaining_time_in_millis() / 1000,
        full_term_list=not override,
    )
