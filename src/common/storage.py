"""Small S3 helpers for gzip JSON / JSON Lines objects."""

import gzip
import json
from collections.abc import Iterable
from typing import Any


class S3Store:
    def __init__(self, bucket: str, client: Any = None) -> None:
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.bucket = bucket
        self.client = client

    def put_json(self, key: str, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="application/json")

    def put_json_gz(self, key: str, obj: Any) -> None:
        body = gzip.compress(json.dumps(obj, ensure_ascii=False).encode())
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType="application/json", ContentEncoding="gzip"
        )

    def put_jsonl_gz(self, key: str, records: Iterable[Any]) -> None:
        lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=gzip.compress(lines.encode()),
            ContentType="application/x-ndjson",
            ContentEncoding="gzip",
        )

    def get_json_gz(self, key: str) -> Any | None:
        """Return the decoded object, or None if the key does not exist."""
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            return None
        return json.loads(gzip.decompress(resp["Body"].read()))
