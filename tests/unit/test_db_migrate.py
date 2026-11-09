import ssl
from datetime import date

import pytest

from db import connect as dbc
from db_migrate import handler as h


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = []

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("syntax error")
        self.result = self.conn.answer(sql, params)

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None


class FakeConn:
    def __init__(self, applied=None, fail_on=None, returning=None):
        self.applied = applied or {}
        self.fail_on = fail_on
        self.returning = returning
        self.executed, self.commits, self.rollbacks, self.closed = [], 0, 0, False

    def answer(self, sql, params):
        if sql.startswith("SELECT name, checksum"):
            return list(self.applied.items())
        if sql.startswith("SELECT name FROM meta"):
            return [(n,) for n in self.applied]
        if "extversion" in sql:
            return [("0.8.0",)]
        if sql.startswith("SELECT count(*)"):
            return [(3,)]
        if "RETURNING" in sql:
            return self.returning
        if sql.startswith("SELECT application_id"):
            return [self.returning[0]] if self.returning else []
        return []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_split_statements_respects_do_blocks_strings_and_comments():
    sql = """-- header; with a semicolon
CREATE EXTENSION IF NOT EXISTS vector;
DO $$ BEGIN IF true THEN RAISE NOTICE 'a;b'; END IF; END $$;
SELECT 'it''s; fine';
-- trailing comment only
"""
    statements = h.split_statements(sql)
    assert len(statements) == 3
    assert statements[1].startswith("DO $$")
    assert statements[1].endswith("END $$")
    assert statements[2] == "SELECT 'it''s; fine'"


def test_bootstrap_script_and_migrations_parse():
    assert len(h.split_statements((h.HERE / "bootstrap.sql").read_text())) >= 8
    assert [p.name for p in h.migration_files()][:2] == ["001_tracker.sql", "002_vectors.sql"]


def test_apply_migrations_records_each_file(tmp_path):
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (x int);")
    (tmp_path / "002_b.sql").write_text("CREATE TABLE b (x int); CREATE TABLE c (x int);")
    conn = FakeConn()
    assert h.apply_migrations(conn, sorted(tmp_path.glob("*.sql"))) == ["001_a.sql", "002_b.sql"]
    inserts = [p for s, p in conn.executed if s.startswith("INSERT INTO meta.schema_migrations")]
    assert [p[0] for p in inserts] == ["001_a.sql", "002_b.sql"]
    assert conn.commits == 3  # table creation + one per file


def test_apply_migrations_skips_applied_and_refuses_edits(tmp_path):
    path = tmp_path / "001_a.sql"
    path.write_text("CREATE TABLE a (x int);")
    conn = FakeConn(applied={"001_a.sql": h.checksum(path.read_text())})
    assert h.apply_migrations(conn, [path]) == []
    path.write_text("CREATE TABLE a (x bigint);")
    with pytest.raises(h.MigrationChanged):
        h.apply_migrations(FakeConn(applied={"001_a.sql": "old"}), [path])


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path):
    path = tmp_path / "001_a.sql"
    path.write_text("CREATE TABLE broken (;")
    conn = FakeConn(fail_on="broken")
    with pytest.raises(RuntimeError):
        h.apply_migrations(conn, [path])
    assert conn.rollbacks == 1
    assert not any(s.startswith("INSERT INTO meta") for s, _ in conn.executed)


ROW = (7, "R1", "ACME", "Data Engineer", date(2026, 11, 9), "applied", None, None, date(2026, 11, 9))


def test_tracker_insert_defaults_status_and_serialises_dates():
    conn = FakeConn(returning=[ROW])
    row = h.tracker_upsert(conn, {"company": "ACME", "role_title": "Data Engineer", "applied_date": "2026-11-09"})
    assert row["application_id"] == 7
    assert row["applied_date"] == "2026-11-09"
    sql, params = conn.executed[-1]
    assert sql.startswith("INSERT INTO tracker.applications (company, role_title, applied_date, status)")
    assert params[-1] == "applied"
    assert conn.commits == 1


def test_tracker_update_only_given_fields():
    conn = FakeConn(returning=[ROW])
    h.tracker_upsert(conn, {"application_id": "7", "status": "interview"})
    sql, params = conn.executed[-1]
    assert "SET status = %s, updated_at = now()" in sql
    assert params == ("interview", 7)


@pytest.mark.parametrize(
    ("application", "message"),
    [
        ({"company": "ACME"}, "missing required fields"),
        ({"company": "A", "role_title": "B", "applied_date": "09.11.2026"}, "YYYY-MM-DD"),
        ({"application_id": 1, "status": "hired"}, "status must be one of"),
        ({"application_id": 1}, "nothing to update"),
        ({"company": "A", "salary": 1}, "unknown fields"),
    ],
)
def test_tracker_validation(application, message):
    with pytest.raises(h.InvalidRequest, match=message):
        h.tracker_upsert(FakeConn(returning=[ROW]), application)


def test_tracker_update_of_missing_row():
    conn = FakeConn(returning=[])
    with pytest.raises(h.InvalidRequest, match="does not exist"):
        h.tracker_upsert(conn, {"application_id": 99, "status": "offer"})
    assert conn.rollbacks == 1


def test_handler_modes(monkeypatch):
    monkeypatch.setenv("MASTER_USER", "admin")
    calls = []

    def connect_fn(user, password=None):
        calls.append((user, password))
        return FakeConn(returning=[ROW])

    assert h.handler({"mode": "bootstrap", "master_password": "pw"}, None, connect_fn)["statements"] >= 8
    assert calls[-1] == ("admin", "pw")
    assert h.handler({}, None, connect_fn)["mode"] == "migrate"
    assert calls[-1] == ("migrator", None)
    status = h.handler({"mode": "status"}, None, connect_fn)
    assert status["pgvector"] == "0.8.0"
    assert status["rows"]["tracker.applications"] == 3
    assert status["pending"] == ["001_tracker.sql", "002_vectors.sql"]
    assert h.handler({"mode": "tracker_list"}, None, connect_fn)["applications"][0]["company"] == "ACME"
    new = {"company": "A", "role_title": "B", "applied_date": "2026-11-09"}
    added = h.handler({"mode": "tracker_upsert", "application": new}, None, connect_fn)
    assert added["application"]["application_id"] == 7
    with pytest.raises(h.InvalidRequest, match="unknown mode"):
        h.handler({"mode": "drop_everything"}, None, connect_fn)


def test_handler_never_logs_the_password(monkeypatch, capsys):
    monkeypatch.setenv("MASTER_USER", "admin")
    h.handler({"mode": "bootstrap", "master_password": "s3cret-pw"}, None, lambda u, p=None: FakeConn())
    assert "s3cret-pw" not in capsys.readouterr().out


class FakeRds:
    def generate_db_auth_token(self, **kwargs):
        self.kwargs = kwargs
        return "iam-token"


class FakeDriver:
    def connect(self, **kwargs):
        self.kwargs = kwargs
        return "conn"


def test_connect_uses_iam_token_and_verified_tls(monkeypatch):
    monkeypatch.setenv("DB_HOST", "db.example.eu-central-1.rds.amazonaws.com")
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    rds, driver = FakeRds(), FakeDriver()
    assert dbc.connect("migrator", driver=driver, rds=rds) == "conn"
    assert rds.kwargs == {"DBHostname": "db.example.eu-central-1.rds.amazonaws.com", "Port": 5432,
                          "DBUsername": "migrator", "Region": "eu-central-1"}  # fmt: skip
    assert driver.kwargs["password"] == "iam-token"
    assert driver.kwargs["database"] == "stellenradar"
    context = driver.kwargs["ssl_context"]
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_connect_with_password_skips_iam(monkeypatch):
    monkeypatch.setenv("DB_HOST", "localhost")
    driver = FakeDriver()
    dbc.connect("admin", "pw", tls=False, driver=driver, rds=object())
    assert driver.kwargs["password"] == "pw"
    assert driver.kwargs["ssl_context"] is None


def test_ca_bundle_is_packaged():
    assert dbc.CA_BUNDLE.read_text().count("BEGIN CERTIFICATE") > 50
