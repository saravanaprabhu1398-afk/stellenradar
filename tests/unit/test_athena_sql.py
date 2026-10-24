import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import athena_sql  # noqa: E402

PARAMS = {
    "db_raw": "stellenradar_raw",
    "db_curated": "stellenradar_curated",
    "data_bucket": "bucket",
    "run_id": "run-1",
    "run_date": "2026-10-24",
    "ingested_at": "2026-10-24 06:00:00",
}


def test_every_statement_renders_without_leftover_placeholders():
    for folder in ("ddl", "curate"):
        for name, sql in athena_sql.statements(folder):
            rendered = athena_sql.render(sql, PARAMS)
            assert "{" not in rendered, name
            assert "}" not in rendered, name


def test_curate_statements_run_in_scd2_order():
    names = [name for name, _ in athena_sql.statements("curate")]
    assert names == ["010_close_changed.sql", "020_close_disappeared.sql", "030_insert_new_versions.sql"]


def test_details_fragment_filters_to_run_and_detail_files():
    rendered = athena_sql.render("{details}", PARAMS)
    assert "ingest_date = '2026-10-24'" in rendered
    assert "run_id = 'run-1'" in rendered
    assert "details" in rendered
    assert "stellenradar_raw.postings_lines" in rendered


class FakeAthena:
    def __init__(self, results=None):
        self.sql = []
        self.results = results or {}

    def run(self, sql, label):
        self.sql.append((label, sql))

    def scalar(self, sql):
        return self.results.get(sql, 0)


def test_close_disappeared_is_skipped_for_incomplete_runs():
    fake = FakeAthena()
    athena_sql.run_curate(fake, "bucket", "run-1", "2026-10-24", complete=False, ingested_at="2026-10-24 06:00:00")
    assert [label for label, _ in fake.sql] == ["010_close_changed.sql", "030_insert_new_versions.sql"]


def test_close_disappeared_runs_for_complete_runs():
    fake = FakeAthena()
    athena_sql.run_curate(fake, "bucket", "run-1", "2026-10-24", complete=True, ingested_at="2026-10-24 06:00:00")
    assert len(fake.sql) == 3


def _checks(**overrides):
    base = {name: 0 for name in athena_sql.CHECKS}
    base["current_rows"] = 10
    base["total_versions"] = 10
    return {**base, **overrides}


def test_s4_verdict_passes_for_identical_clean_results():
    assert athena_sql.s4_verdict(_checks(), _checks()) == []


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        (_checks(total_versions=20), "changed the table"),
        (_checks(refnrs_with_multiple_current=1), "more than one current"),
        (_checks(current_flag_mismatch=2), "disagree"),
        (_checks(current_rows=0, total_versions=10), "nothing was curated"),
    ],
)
def test_s4_verdict_reports_problems(second, expected):
    problems = athena_sql.s4_verdict(_checks(), second)
    assert any(expected in p for p in problems)


class FakeClient:
    def __init__(self, states, rows=None):
        self.states = list(states)
        self.rows = rows

    def start_query_execution(self, QueryString, WorkGroup):  # noqa: N803 – boto3 argument names
        assert WorkGroup == "stellenradar-dev"
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId):  # noqa: N803
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {
            "QueryExecution": {
                "Status": {"State": state, "StateChangeReason": "boom"},
                "Statistics": {"DataScannedInBytes": 1_000_000},
            }
        }

    def get_query_results(self, QueryExecutionId):  # noqa: N803
        return {"ResultSet": {"Rows": [{"Data": [{"VarCharValue": "count"}]}, {"Data": [{"VarCharValue": "42"}]}]}}


def test_athena_run_waits_until_succeeded():
    athena = athena_sql.Athena(FakeClient(["QUEUED", "RUNNING", "SUCCEEDED"]), sleep=lambda s: None)
    assert athena.run("SELECT 1", "q")["Status"]["State"] == "SUCCEEDED"


def test_athena_run_raises_with_reason_on_failure():
    athena = athena_sql.Athena(FakeClient(["FAILED"]), sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="boom"):
        athena.run("SELECT 1", "q")


def test_athena_scalar_reads_first_data_row():
    athena = athena_sql.Athena(FakeClient(["RUNNING", "SUCCEEDED"]), sleep=lambda s: None)
    assert athena.scalar("SELECT count(*)") == 42
