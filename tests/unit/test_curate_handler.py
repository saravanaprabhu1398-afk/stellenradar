import pytest

from curate import handler as h
from curate.athena import InvariantViolation


class RecordingAthena:
    def __init__(self, checks=None):
        self.labels = []
        self.sql = []
        self.checks = checks or {}
        self.log = lambda msg: None

    def run(self, sql, label):
        self.labels.append(label)
        self.sql.append(sql)

    def scalar(self, sql, label="check"):
        return self.checks.get(label, 0)


EVENT = {"run_id": "exec-1", "run_date": "2026-10-27", "start_time": "2026-10-27T05:00:01.123Z", "complete": True}


def test_ingested_at_from_execution_start_time():
    assert h.ingested_at_from("2026-10-27T05:00:01.123Z") == "2026-10-27 05:00:01"
    assert h.ingested_at_from("2026-10-27T05:00:01+00:00") == "2026-10-27 05:00:01"


def test_handler_runs_ddl_then_all_scd2_steps(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", "bucket")
    athena = RecordingAthena({"current_rows": 5})
    result = h.handler(EVENT, None, athena=athena)

    assert athena.labels == [
        "010_curated_postings.sql",
        "020_posting_enrichment.sql",
        "010_close_changed.sql",
        "020_close_disappeared.sql",
        "030_insert_new_versions.sql",
    ]
    assert "TIMESTAMP '2026-10-27 05:00:01'" in athena.sql[2]
    assert result["current_rows"] == 5


def test_handler_skips_close_disappeared_when_incomplete(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", "bucket")
    athena = RecordingAthena()
    h.handler({**EVENT, "complete": False}, None, athena=athena)
    assert "020_close_disappeared.sql" not in athena.labels


def test_handler_fails_on_broken_invariant(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", "bucket")
    with pytest.raises(InvariantViolation):
        h.handler(EVENT, None, athena=RecordingAthena({"refnrs_with_multiple_current": 1}))


def test_handler_builds_default_athena_client(monkeypatch):
    monkeypatch.setenv("DATA_BUCKET", "bucket")
    monkeypatch.setenv("ATHENA_WORKGROUP", "wg")
    created = {}

    def fake_athena(**kwargs):
        created.update(kwargs)
        return RecordingAthena()

    monkeypatch.setattr(h, "Athena", fake_athena)
    h.handler(EVENT, None)
    assert created["workgroup"] == "wg"
    created["log"]("message is logged without error")
