"""Curate Lambda: SCD2 merge of one ingest run into curated.postings (SDD §4.2).

Event (from the daily state machine):
  {"run_id": ..., "run_date": "YYYY-MM-DD", "start_time": "<execution start, ISO 8601>", "complete": bool}
"""

import os
from datetime import datetime
from typing import Any

from common.logging import get_logger
from curate.athena import Athena, assert_invariants, run_checks, run_curate, run_ddl

log = get_logger("curate")


def ingested_at_from(start_time: str) -> str:
    """'2026-10-27T05:00:01.123Z' -> '2026-10-27 05:00:01' (UTC), the version timestamp for this run."""
    return datetime.fromisoformat(start_time.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")


def handler(event: dict, context: Any, athena: Athena | None = None) -> dict:
    run_id = event["run_id"]
    athena = athena or Athena(
        log=lambda msg: log.info(msg, extra={"fields": {"run_id": run_id}}),
        workgroup=os.environ.get("ATHENA_WORKGROUP", "stellenradar-dev"),
    )
    data_bucket = os.environ["DATA_BUCKET"]

    run_ddl(athena, data_bucket)  # CREATE TABLE IF NOT EXISTS – a no-op once the table exists
    run_curate(
        athena,
        data_bucket,
        run_id=run_id,
        run_date=event["run_date"],
        complete=bool(event.get("complete")),
        ingested_at=ingested_at_from(event["start_time"]),
    )
    checks = run_checks(athena)
    log.info("curate finished", extra={"fields": {"run_id": run_id, **checks}})
    assert_invariants(checks)
    return checks
