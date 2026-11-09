"""Operate the dev database from your machine without any network path into the VPC (SDD §4.6).

    python scripts/db_admin.py up | down                 start (and wait) / stop the RDS instance
    python scripts/db_admin.py bootstrap                 one-time setup, then migrations
    python scripts/db_admin.py migrate | status
    python scripts/db_admin.py tracker-add --company ACME --role "Data Engineer" [--date 2026-11-09] [--refnr ...]
    python scripts/db_admin.py tracker-update ID --status interview [--notes ...]
    python scripts/db_admin.py tracker-list

Everything except up/down goes through the db-migrate Lambda inside the VPC. For bootstrap, the RDS-managed master
password is read from Secrets Manager here and passed in the invocation payload once (never logged by the function).
"""

import argparse
import json
import sys
from datetime import date

DB_INSTANCE = "stellenradar-dev-db"
FUNCTION = "stellenradar-dev-db-migrate"
REGION = "eu-central-1"


def client(name: str):
    import boto3

    return boto3.client(name, region_name=REGION)


def invoke(payload: dict, lam=None) -> dict:
    lam = lam or client("lambda")
    response = lam.invoke(FunctionName=FUNCTION, Payload=json.dumps(payload).encode())
    body = json.loads(response["Payload"].read() or b"null")
    if response.get("FunctionError"):
        raise SystemExit(f"db-migrate failed: {body.get('errorType')}: {body.get('errorMessage')}")
    return body


def master_password(rds=None, secrets=None) -> str:
    rds, secrets = rds or client("rds"), secrets or client("secretsmanager")
    instance = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE)["DBInstances"][0]
    secret_arn = instance["MasterUserSecret"]["SecretArn"]
    return json.loads(secrets.get_secret_value(SecretId=secret_arn)["SecretString"])["password"]


def up(rds=None) -> str:
    rds = rds or client("rds")
    state = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE)["DBInstances"][0]["DBInstanceStatus"]
    if state == "stopped":
        rds.start_db_instance(DBInstanceIdentifier=DB_INSTANCE)
    if state != "available":
        print(f"{DB_INSTANCE}: {state} → waiting until available (5–10 minutes) …", flush=True)
        rds.get_waiter("db_instance_available").wait(
            DBInstanceIdentifier=DB_INSTANCE, WaiterConfig={"Delay": 30, "MaxAttempts": 40}
        )
    return "available"


def down(rds=None) -> str:
    rds = rds or client("rds")
    state = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE)["DBInstances"][0]["DBInstanceStatus"]
    if state == "available":
        rds.stop_db_instance(DBInstanceIdentifier=DB_INSTANCE)
        return "stopping"
    return state


def main(argv: list[str] | None = None, lam=None, rds=None, secrets=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("up", "down", "bootstrap", "migrate", "status", "tracker-list"):
        sub.add_parser(name)
    add = sub.add_parser("tracker-add")
    add.add_argument("--company", required=True)
    add.add_argument("--role", required=True, dest="role_title")
    add.add_argument("--date", default=date.today().isoformat(), dest="applied_date")
    for optional in ("--status", "--refnr", "--source", "--notes"):
        add.add_argument(optional)
    update = sub.add_parser("tracker-update")
    update.add_argument("application_id", type=int)
    for optional in ("--status", "--refnr", "--source", "--notes", "--company"):
        update.add_argument(optional)
    update.add_argument("--role", dest="role_title")
    update.add_argument("--date", dest="applied_date")
    args = parser.parse_args(argv)

    if args.command == "up":
        print(f"{DB_INSTANCE}: {up(rds)}")
        return 0
    if args.command == "down":
        print(f"{DB_INSTANCE}: {down(rds)}")
        return 0
    if args.command == "bootstrap":
        result = invoke({"mode": "bootstrap", "master_password": master_password(rds, secrets)}, lam)
        print(f"bootstrap: {result['statements']} statements")
        result = invoke({"mode": "migrate"}, lam)
    elif args.command in ("migrate", "status", "tracker-list"):
        result = invoke({"mode": args.command.replace("-", "_")}, lam)
    else:
        application = {k: v for k, v in vars(args).items() if k != "command" and v is not None}
        result = invoke({"mode": "tracker_upsert", "application": application}, lam)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
