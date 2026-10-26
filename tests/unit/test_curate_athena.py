import pytest

from curate import athena as ca

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
        for name, sql in ca.statements(folder):
            rendered = ca.render(sql, PARAMS)
            assert "{" not in rendered, name
            assert "}" not in rendered, name


def test_curate_statements_run_in_scd2_order():
    names = [name for name, _ in ca.statements("curate")]
    assert names == ["010_close_changed.sql", "020_close_disappeared.sql", "030_insert_new_versions.sql"]


def test_details_fragment_filters_to_run_and_detail_files():
    rendered = ca.render("{details}", PARAMS)
    assert "ingest_date = '2026-10-24'" in rendered
    assert "run_id = 'run-1'" in rendered
    assert "stellenradar_raw.postings_lines" in rendered


class FakeAthena:
    def __init__(self, results=None):
        self.sql = []
        self.results = results or {}
        self.log = lambda msg: None

    def run(self, sql, label):
        self.sql.append((label, sql))

    def scalar(self, sql, label="check"):
        return self.results.get(label, 0)


def test_close_disappeared_is_skipped_for_incomplete_runs():
    fake = FakeAthena()
    ca.run_curate(fake, "bucket", "run-1", "2026-10-24", complete=False, ingested_at="2026-10-24 06:00:00")
    assert [label for label, _ in fake.sql] == ["010_close_changed.sql", "030_insert_new_versions.sql"]


def test_close_disappeared_runs_for_complete_runs():
    fake = FakeAthena()
    ca.run_curate(fake, "bucket", "run-1", "2026-10-24", complete=True, ingested_at="2026-10-24 06:00:00")
    assert len(fake.sql) == 3


def test_run_ddl_fills_bucket_and_database():
    fake = FakeAthena()
    ca.run_ddl(fake, "my-bucket")
    label, sql = fake.sql[0]
    assert label == "010_curated_postings.sql"
    assert "s3://my-bucket/curated/postings/" in sql
    assert "stellenradar_curated.postings" in sql


def test_run_checks_returns_every_check():
    fake = FakeAthena({"current_rows": 7})
    checks = ca.run_checks(fake)
    assert set(checks) == set(ca.CHECKS)
    assert checks["current_rows"] == 7


def test_assert_invariants():
    ca.assert_invariants({"refnrs_with_multiple_current": 0, "current_flag_mismatch": 0})
    with pytest.raises(ca.InvariantViolation):
        ca.assert_invariants({"refnrs_with_multiple_current": 2, "current_flag_mismatch": 0})


class FakeClient:
    def __init__(self, states):
        self.states = list(states)
        self.workgroups = []

    def start_query_execution(self, QueryString, WorkGroup):  # noqa: N803 – boto3 argument names
        self.workgroups.append(WorkGroup)
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


def make_athena(states, **kwargs):
    return ca.Athena(FakeClient(states), sleep=lambda s: None, log=lambda m: None, **kwargs)


def test_athena_run_waits_until_succeeded():
    assert make_athena(["QUEUED", "RUNNING", "SUCCEEDED"]).run("SELECT 1", "q")["Status"]["State"] == "SUCCEEDED"


def test_athena_run_raises_with_reason_on_failure():
    with pytest.raises(RuntimeError, match="boom"):
        make_athena(["FAILED"]).run("SELECT 1", "q")


def test_athena_scalar_reads_first_data_row():
    assert make_athena(["RUNNING", "SUCCEEDED"]).scalar("SELECT count(*)") == 42


def test_athena_scalar_raises_on_failed_query():
    with pytest.raises(RuntimeError):
        make_athena(["FAILED"]).scalar("SELECT count(*)")


def test_athena_uses_configured_workgroup():
    athena = make_athena(["SUCCEEDED"], workgroup="other")
    athena.run("SELECT 1", "q")
    assert athena.client.workgroups == ["other"]
