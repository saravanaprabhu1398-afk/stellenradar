"""Enrich Lambda: extract structured fields from current postings with the extraction model (SDD §4.3).

Event (from the daily state machine, after Curate):
  {"run_id": ..., "run_date": "YYYY-MM-DD"}

One Lambda with a small thread pool instead of a Distributed Map: at most MAX_ENRICH_PER_RUN postings per run,
each a short Bedrock call, fits comfortably in one invocation (SDD §4.3).
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.logging import get_logger
from common.storage import S3Store
from curate.athena import DB_CURATED, Athena
from enrich.extract import Extraction, Extractor
from enrich.sql_values import insert_statement

log = get_logger("enrich")

WORK_LIST_SQL = (Path(__file__).parent / "sql" / "work_list.sql").read_text()
WORKERS = 4
INSERT_BATCH = 100
MAX_DEAD_LETTER_RATE = 0.10
MIN_DEAD_LETTERS_TO_FAIL = 3  # a single bad posting in a small run is not an incident
STOP_SUBMITTING_MS = 120_000  # leave time to write results before the Lambda times out


class TooManyDeadLetters(Exception):
    """Too many postings failed extraction – likely a prompt, model or permission problem, not bad input."""


def bedrock_client() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime", config=Config(retries={"mode": "adaptive", "max_attempts": 8}, read_timeout=120)
    )


def safe_extract(extractor: Extractor, posting: dict, context: Any) -> Extraction | None:
    """None means skipped (out of time); exceptions become dead letters instead of failing the whole run."""
    if context is not None and context.get_remaining_time_in_millis() < STOP_SUBMITTING_MS:
        return None
    try:
        return extractor.extract(posting)
    except Exception as exc:  # noqa: BLE001 – recorded per posting, the rate check decides whether the run fails
        return Extraction(posting["refnr"], posting["content_hash"], problems=[f"{type(exc).__name__}: {exc}"])


def to_row(x: Extraction, prompt_version: str, model_id: str, run_id: str, enriched_at: str) -> dict:
    return {
        **x.fields,
        "refnr": x.refnr,
        "content_hash": x.content_hash,
        "prompt_version": prompt_version,
        "model_id": model_id,
        "input_tokens": x.input_tokens,
        "output_tokens": x.output_tokens,
        "enriched_at": enriched_at,
        "run_id": run_id,
    }


def handler(event: dict, context: Any, athena: Athena | None = None, bedrock: Any = None, store: Any = None) -> dict:
    run_id = event["run_id"]
    data_bucket = os.environ["DATA_BUCKET"]
    model_id = os.environ.get("EXTRACT_MODEL_ID", "eu.amazon.nova-lite-v1:0")
    prompt_version = os.environ.get("PROMPT_VERSION", "v1")
    limit = int(os.environ.get("MAX_ENRICH_PER_RUN", "200"))
    athena = athena or Athena(
        log=lambda msg: log.info(msg, extra={"fields": {"run_id": run_id}}),
        workgroup=os.environ.get("ATHENA_WORKGROUP", "stellenradar-dev"),
    )

    work = athena.rows(
        WORK_LIST_SQL.format(db_curated=DB_CURATED, prompt_version=prompt_version, limit=limit), "work_list"
    )
    stats = {"run_id": run_id, "candidates": len(work), "enriched": 0, "dead_letters": 0, "skipped": 0,
             "input_tokens": 0, "output_tokens": 0}  # fmt: skip
    if not work:
        log.info("nothing to enrich", extra={"fields": stats})
        return stats

    extractor = Extractor(bedrock or bedrock_client(), model_id, prompt_version)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda p: safe_extract(extractor, p, context), work))

    done = [x for x in results if x is not None]
    good = [x for x in done if x.ok]
    dead = [x for x in done if not x.ok]
    enriched_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    rows = [to_row(x, prompt_version, model_id, run_id, enriched_at) for x in good]
    for i in range(0, len(rows), INSERT_BATCH):
        athena.run(insert_statement(DB_CURATED, rows[i : i + INSERT_BATCH]), f"insert_enrichment_{i // INSERT_BATCH}")

    store = store or S3Store(data_bucket)
    if rows:
        store.put_jsonl_gz(f"staging/enrichment/run_id={run_id}/results.jsonl.gz", rows)
    if dead:
        store.put_jsonl_gz(
            f"staging/dead_letter/run_id={run_id}/enrich.jsonl.gz",
            [{"refnr": x.refnr, "content_hash": x.content_hash, "attempts": x.attempts, "problems": x.problems,
              "last_input": x.last_input, "prompt_version": prompt_version, "model_id": model_id} for x in dead],
        )  # fmt: skip

    stats.update(
        enriched=len(good),
        dead_letters=len(dead),
        skipped=len(results) - len(done),
        input_tokens=sum(x.input_tokens for x in done),
        output_tokens=sum(x.output_tokens for x in done),
    )
    log.info("enrich finished", extra={"fields": stats})
    if len(dead) >= MIN_DEAD_LETTERS_TO_FAIL and len(dead) > MAX_DEAD_LETTER_RATE * len(done):
        raise TooManyDeadLetters(f"{len(dead)} of {len(done)} postings failed extraction: {dead[0].problems}")
    return stats
