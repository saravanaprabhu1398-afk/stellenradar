"""Run the curate SQL (src/curate/sql/) against the dev environment from your machine.

    python scripts/athena_sql.py ddl
    python scripts/athena_sql.py curate --run-id <id> --run-date <YYYY-MM-DD>
    python scripts/athena_sql.py check
    python scripts/athena_sql.py s4 --run-id <id> --run-date <YYYY-MM-DD>   # spike S4: curate twice, prove idempotency

The daily pipeline runs the same code in the curate Lambda; this script is for setup, manual runs and spikes.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from curate.athena import Athena, run_checks, run_curate, run_ddl  # noqa: E402


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

    import boto3

    athena = Athena(boto3.client("athena", region_name="eu-central-1"))
    ingested_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    if args.command == "ddl":
        print("DDL")
        run_ddl(athena, data_bucket)
    elif args.command == "check":
        run_checks(athena)
    elif args.command == "curate":
        complete = manifest_complete(data_bucket, args.run_id)
        run_curate(athena, data_bucket, args.run_id, args.run_date, complete, ingested_at)
        run_checks(athena)
    elif args.command == "s4":
        complete = manifest_complete(data_bucket, args.run_id)
        print("DDL")
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
