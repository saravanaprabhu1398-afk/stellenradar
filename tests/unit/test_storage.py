import gzip
import io
import json

from common.storage import S3Store


class FakeS3:
    class exceptions:  # noqa: N801 – mirrors the boto3 client attribute
        class NoSuchKey(Exception):
            pass

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kwargs):  # noqa: N803 – boto3 argument names
        self.objects[(Bucket, Key)] = (Body, kwargs)

    def get_object(self, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey()
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}


def test_json_gz_round_trip_and_missing_key():
    s3 = FakeS3()
    store = S3Store("bucket", client=s3)
    store.put_json_gz("k.json.gz", {"ä": 1})
    assert store.get_json_gz("k.json.gz") == {"ä": 1}
    assert store.get_json_gz("missing") is None
    assert s3.objects[("bucket", "k.json.gz")][1]["ContentEncoding"] == "gzip"


def test_jsonl_gz_writes_one_line_per_record():
    s3 = FakeS3()
    S3Store("bucket", client=s3).put_jsonl_gz("k.jsonl.gz", [{"a": 1}, {"b": 2}])
    lines = gzip.decompress(s3.objects[("bucket", "k.jsonl.gz")][0]).decode().splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"b": 2}]


def test_put_json_is_plain_json():
    s3 = FakeS3()
    S3Store("bucket", client=s3).put_json("m.json", {"x": 1})
    assert json.loads(s3.objects[("bucket", "m.json")][0]) == {"x": 1}
