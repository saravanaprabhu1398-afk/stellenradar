"""PostgreSQL connections for the in-VPC Lambdas (SDD §4.5–4.6, ADR-007).

Application roles authenticate with IAM tokens signed locally (no Secrets Manager endpoint needed, ADR-003); every
connection uses TLS with full certificate and hostname verification against the RDS CA bundle packaged here.
"""

import os
import ssl
from pathlib import Path
from typing import Any

CA_BUNDLE = Path(__file__).with_name("rds-global-bundle.pem")


def ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(CA_BUNDLE))
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def iam_token(host: str, port: int, user: str, region: str, rds: Any = None) -> str:
    """A 15-minute auth token; generate_db_auth_token signs locally and makes no network call."""
    if rds is None:
        import boto3

        rds = boto3.client("rds", region_name=region)
    return rds.generate_db_auth_token(DBHostname=host, Port=port, DBUsername=user, Region=region)


def connect(user: str, password: str | None = None, *, tls: bool = True, driver: Any = None, rds: Any = None) -> Any:
    """Connect as `user`; without a password an IAM token is used (the role needs rds_iam and rds-db:connect)."""
    host = os.environ["DB_HOST"]
    port = int(os.environ.get("DB_PORT", "5432"))
    region = os.environ.get("AWS_REGION", "eu-central-1")
    if password is None:
        password = iam_token(host, port, user, region, rds)
    if driver is None:
        import pg8000.dbapi as driver
    return driver.connect(
        user=user,
        password=password,
        host=host,
        port=port,
        database=os.environ.get("DB_NAME", "stellenradar"),
        ssl_context=ssl_context() if tls else None,
        timeout=30,
        application_name=os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "stellenradar"),
    )
