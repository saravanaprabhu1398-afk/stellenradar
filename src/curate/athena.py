"""Render and run the Athena statements in curate/sql/ (SDD §4.2, spike S4).

Used by the curate Lambda in the daily pipeline and by scripts/athena_sql.py for setup and manual runs.
"""

import time
from pathlib import Path
from typing import Any

SQL_DIR = Path(__file__).parent / "sql"
WORKGROUP = "stellenradar-dev"
DB_RAW = "stellenradar_raw"
DB_CURATED = "stellenradar_curated"

CHECKS = {
    "current_rows": "SELECT count(*) FROM {db_curated}.postings WHERE is_current",
    "total_versions": "SELECT count(*) FROM {db_curated}.postings",
    "closed_versions": "SELECT count(*) FROM {db_curated}.postings WHERE NOT is_current",
    "refnrs_with_multiple_current": (
        "SELECT count(*) FROM (SELECT refnr FROM {db_curated}.postings WHERE is_current "
        "GROUP BY refnr HAVING count(*) > 1)"
    ),
    "current_flag_mismatch": (
        "SELECT count(*) FROM {db_curated}.postings "
        "WHERE (is_current AND valid_to IS NOT NULL) OR (NOT is_current AND valid_to IS NULL)"
    ),
}
INVARIANTS = ("refnrs_with_multiple_current", "current_flag_mismatch")


class InvariantViolation(Exception):
    """curated.postings broke an SCD2 invariant – the run must fail and alert."""


def render(template: str, params: dict[str, str]) -> str:
    """Fill {placeholders}; fragments ({details}, {listed}) are rendered first so they may use placeholders too."""
    fragments = {
        name: (SQL_DIR / "curate" / f"_{name}.sql").read_text().format(**params) for name in ("details", "listed")
    }
    return template.format(**params, **fragments)


def statements(folder: str) -> list[tuple[str, str]]:
    return [(p.name, p.read_text()) for p in sorted((SQL_DIR / folder).glob("[0-9]*.sql"))]


def base_params(data_bucket: str) -> dict[str, str]:
    return {"db_raw": DB_RAW, "db_curated": DB_CURATED, "data_bucket": data_bucket}


class Athena:
    def __init__(self, client: Any = None, sleep=time.sleep, log=print, workgroup: str = WORKGROUP) -> None:
        if client is None:
            import boto3

            client = boto3.client("athena")
        self.client = client
        self.sleep = sleep
        self.log = log
        self.workgroup = workgroup

    def _wait(self, sql: str, label: str) -> tuple[str, dict]:
        qid = self.client.start_query_execution(QueryString=sql, WorkGroup=self.workgroup)["QueryExecutionId"]
        while True:
            execution = self.client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            state = execution["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            self.sleep(1)
        if state != "SUCCEEDED":
            reason = execution["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"{label}: {state} – {reason}\n--- SQL ---\n{sql}")
        return qid, execution

    def run(self, sql: str, label: str) -> dict:
        _, execution = self._wait(sql, label)
        scanned = execution.get("Statistics", {}).get("DataScannedInBytes", 0)
        self.log(f"  ✓ {label}  ({scanned / 1e6:.2f} MB scanned)")
        return execution

    def scalar(self, sql: str, label: str = "check") -> int:
        qid, _ = self._wait(sql, label)
        rows = self.client.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
        return int(rows[1]["Data"][0]["VarCharValue"])


def run_ddl(athena: Athena, data_bucket: str) -> None:
    for name, sql in statements("ddl"):
        athena.run(sql.format(**base_params(data_bucket)), name)


def run_curate(athena: Athena, data_bucket: str, run_id: str, run_date: str, complete: bool, ingested_at: str) -> None:
    params = {**base_params(data_bucket), "run_id": run_id, "run_date": run_date, "ingested_at": ingested_at}
    athena.log(f"Curate run_id={run_id} complete={complete} ingested_at={ingested_at}")
    for name, sql in statements("curate"):
        if name.startswith("020_") and not complete:
            athena.log(f"  – {name} skipped (ingest listing not complete)")
            continue
        athena.run(render(sql, params), name)


def run_checks(athena: Athena) -> dict[str, int]:
    params = {"db_curated": DB_CURATED}
    results = {name: athena.scalar(sql.format(**params), name) for name, sql in CHECKS.items()}
    for name, value in results.items():
        athena.log(f"  {name:32s} {value}")
    return results


def assert_invariants(checks: dict[str, int]) -> None:
    broken = {name: checks[name] for name in INVARIANTS if checks[name]}
    if broken:
        raise InvariantViolation(f"SCD2 invariants violated: {broken}")
