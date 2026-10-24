"""Run the Athena SQL in sql/athena/ against the dev environment.

    python scripts/athena_sql.py ddl
    python scripts/athena_sql.py curate --run-id <id> --run-date <YYYY-MM-DD>
    python scripts/athena_sql.py check
    python scripts/athena_sql.py s4 --run-id <id> --run-date <YYYY-MM-DD>   # spike S4: curate twice, prove idempotency

Step Functions will run the same statement files in production (SDD §4.2); this script is for setup, manual
runs and the spike.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "athena"
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


def render(template: str, params: dict[str, str]) -> str:
    """Fill {placeholders}; fragments ({details}, {listed}) are rendered first so they may use placeholders too."""
    fragments = {
        name: (SQL_DIR / "curate" / f"_{name}.sql").read_text().format(**params) for name in ("details", "listed")
    }
    return template.format(**params, **fragments)


def statements(folder: str) -> list[tuple[str, str]]:
    return [(p.name, p.read_text()) for p in sorted((SQL_DIR / folder).glob("[0-9]*.sql"))]


class Athena:
    def __init__(self, client: Any = None, sleep=time.sleep) -> None:
        if client is None:
            import boto3

            client = boto3.client("athena", region_name="eu-central-1")
        self.client = client
        self.sleep = sleep

    def run(self, sql: str, label: str) -> dict:
        qid = self.client.start_query_execution(QueryString=sql, WorkGroup=WORKGROUP)["QueryExecutionId"]
        while True:
            execution = self.client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
            state = execution["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            self.sleep(1)
        if state != "SUCCEEDED":
            reason = execution["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"{label}: {state} – {reason}\n--- SQL ---\n{sql}")
        scanned = execution.get("Statistics", {}).get("DataScannedInBytes", 0)
        print(f"  ✓ {label}  ({scanned / 1e6:.2f} MB scanned)")
        return execution

    def scalar(self, sql: str) -> int:
        qid = self.client.start_query_execution(QueryString=sql, WorkGroup=WORKGROUP)["QueryExecutionId"]
        while self.client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"] in (
            "QUEUED",
            "RUNNING",
        ):
            self.sleep(1)
        rows = self.client.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
        return int(rows[1]["Data"][0]["VarCharValue"])


def base_params(data_bucket: str) -> dict[str, str]:
    return {"db_raw": DB_RAW, "db_curated": DB_CURATED, "data_bucket": data_bucket}


def run_ddl(athena: Athena, data_bucket: str) -> None:
    print("DDL")
    for name, sql in statements("ddl"):
        athena.run(sql.format(**base_params(data_bucket)), name)


def run_curate(athena: Athena, data_bucket: str, run_id: str, run_date: str, complete: bool, ingested_at: str) -> None:
    params = {**base_params(data_bucket), "run_id": run_id, "run_date": run_date, "ingested_at": ingested_at}
    print(f"Curate run_id={run_id} complete={complete} ingested_at={ingested_at}")
    for name, sql in statements("curate"):
        if name.startswith("020_") and not complete:
            print(f"  – {name} skipped (manifest not complete)")
            continue
        athena.run(render(sql, params), name)


def run_checks(athena: Athena) -> dict[str, int]:
    params = {"db_curated": DB_CURATED}
    results = {name: athena.scalar(sql.format(**params)) for name, sql in CHECKS.items()}
    for name, value in results.items():
        print(f"  {name:32s} {value}")
    return results


def manifest_complete(data_bucket: str, run_id: str) -> bool:
    import boto3

    body = boto3.client("s3").get_object(Bucket=data_bucket, Key=f"raw/manifests/run_id={run_id}.json")["Body"]
    return bool(json.loads(body.read()).get("complete"))


def s4_verdict(first: dict[str, int], second: dict[str, int]) -> list[str]:
    problems = []
    if first != second:
        problems.append(f"second run changed the table: {first} -> {second}")
    if second["refnrs_with_multiple_current"]:
        problems.append("a reference has more than one current row")
    if second["current_flag_mismatch"]:
        problems.append("is_current and valid_to disagree")
    if not second["current_rows"]:
        problems.append("no current rows – nothing was curated")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["ddl", "curate", "check", "s4"])
    parser.add_argument("--run-id")
    parser.add_argument("--run-date")
    parser.add_argument("--data-bucket", help="defaults to stellenradar-dev-data-<account>")
    args = parser.parse_args(argv)

    if args.command in ("curate", "s4") and not (args.run_id and args.run_date):
        parser.error("--run-id and --run-date are required")

    data_bucket = args.data_bucket
    if not data_bucket:
        import boto3

        account = boto3.client("sts").get_caller_identity()["Account"]
        data_bucket = f"stellenradar-dev-data-{account}"

    athena = Athena()
    ingested_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    if args.command == "ddl":
        run_ddl(athena, data_bucket)
    elif args.command == "check":
        run_checks(athena)
    elif args.command == "curate":
        complete = manifest_complete(data_bucket, args.run_id)
        run_curate(athena, data_bucket, args.run_id, args.run_date, complete, ingested_at)
        run_checks(athena)
    elif args.command == "s4":
        complete = manifest_complete(data_bucket, args.run_id)
        run_ddl(athena, data_bucket)
        run_curate(athena, data_bucket, args.run_id, args.run_date, complete, ingested_at)
        print("After first run:")
        first = run_checks(athena)
        run_curate(athena, data_bucket, args.run_id, args.run_date, complete, ingested_at)
        print("After second run (must be identical):")
        second = run_checks(athena)
        problems = s4_verdict(first, second)
        print("\nSpike S4: " + ("PASS" if not problems else "FAIL\n  - " + "\n  - ".join(problems)))
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
