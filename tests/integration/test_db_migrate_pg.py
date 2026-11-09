"""Bootstrap, migrations and tracker against a real PostgreSQL with pgvector.

Skipped unless PG_TEST_HOST is set. The database must look like RDS:
- a non-superuser master role `stellenradar_admin` with CREATEROLE;
- a NOLOGIN role `rds_iam`, granted to the master role WITH ADMIN OPTION (as on RDS);
- database `stellenradar` with the `vector` extension available (rds_superuser may create it on RDS; locally,
  create it as superuser first).
Auth is trust/password, no TLS.
"""

import os

import pytest

pg8000 = pytest.importorskip("pg8000.dbapi")
pytestmark = pytest.mark.skipif(not os.environ.get("PG_TEST_HOST"), reason="PG_TEST_HOST not set")

from db_migrate import handler as h  # noqa: E402


def connect(user, password=None):
    return pg8000.connect(
        user=user,
        password=password or "unused-with-trust-auth",
        host=os.environ["PG_TEST_HOST"],
        port=int(os.environ.get("PG_TEST_PORT", "5432")),
        database="stellenradar",
    )


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("MASTER_USER", "stellenradar_admin")


def test_bootstrap_migrate_and_tracker_end_to_end():
    master_password = os.environ.get("PG_TEST_MASTER_PASSWORD", "adminpw")
    for _ in range(2):  # bootstrap is idempotent
        assert h.handler({"mode": "bootstrap", "master_password": master_password}, None, connect)["statements"] >= 8

    first = h.handler({"mode": "migrate"}, None, connect)["applied"]
    assert h.handler({"mode": "migrate"}, None, connect)["applied"] == []
    status = h.handler({"mode": "status"}, None, connect)
    assert status["pending"] == []
    assert {"001_tracker.sql", "002_vectors.sql"} <= set(status["migrations"])
    assert status["pgvector"]
    assert first in ([], ["001_tracker.sql", "002_vectors.sql"])  # [] when the database was already migrated

    added = h.handler(
        {"mode": "tracker_upsert", "application": {"company": "ACME", "role_title": "Data Engineer",
                                                    "applied_date": "2026-11-09", "refnr": "R1"}},
        None, connect,
    )["application"]  # fmt: skip
    assert added["status"] == "applied"
    updated = h.handler(
        {"mode": "tracker_upsert", "application": {"application_id": added["application_id"], "status": "interview"}},
        None,
        connect,
    )["application"]
    assert updated["status"] == "interview"
    assert updated["updated_at"] >= added["updated_at"]
    listed = h.handler({"mode": "tracker_list"}, None, connect)["applications"]
    assert any(a["application_id"] == added["application_id"] for a in listed)

    with pytest.raises(h.InvalidRequest, match="status must be one of"):
        h.handler({"mode": "tracker_upsert", "application": {"application_id": 1, "status": "hired"}}, None, connect)

    # match_app: may write vectors and read the tracker, but not write the tracker
    conn = connect("match_app")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO vec.posting_vectors (refnr, content_hash, model_id, embedding) VALUES (%s, %s, %s, %s::vector) "
        "ON CONFLICT (refnr) DO UPDATE SET content_hash = EXCLUDED.content_hash",
        ("R1", "h", "titan", "[" + ",".join(["0.1"] * 1024) + "]"),
    )
    cur.execute("SELECT count(*) FROM tracker.applications")
    assert cur.fetchone()[0] >= 1
    conn.commit()
    with pytest.raises(pg8000.DatabaseError, match="permission denied"):
        cur.execute("INSERT INTO tracker.applications (company, role_title, applied_date, status) "
                    "VALUES ('x', 'y', '2026-01-01', 'applied')")  # fmt: skip
    conn.rollback()
    cur.execute("DELETE FROM vec.posting_vectors WHERE refnr = 'R1'")
    conn.commit()
    conn.close()

    cur = connect("stellenradar_admin", master_password).cursor()
    cur.execute("SELECT relreplident FROM pg_class WHERE oid = 'tracker.applications'::regclass")
    assert cur.fetchone()[0] == "f"  # REPLICA IDENTITY FULL for DMS
