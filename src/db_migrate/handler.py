"""Database migrations and tracker maintenance inside the VPC (SDD §4.6).

Event modes:
  {"mode": "bootstrap", "master_password": "..."}  one-time setup as the RDS master user (operator only)
  {"mode": "migrate"}                               apply pending migrations as `migrator` (IAM auth)
  {"mode": "status"}                                applied migrations, pgvector version, row counts
  {"mode": "tracker_upsert", "application": {...}}  add or update one tracker row
  {"mode": "tracker_list"}                          all tracker rows

The event is never logged: in bootstrap mode it carries the master password (documented trade-off, SDD §4.6).
"""

import hashlib
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from common.logging import get_logger
from db.connect import connect

log = get_logger("db_migrate")

HERE = Path(__file__).parent
MIGRATIONS = HERE / "migrations"
STATUSES = ("applied", "screening", "interview", "offer", "rejected", "withdrawn", "ghosted")
TRACKER_FIELDS = ("refnr", "company", "role_title", "applied_date", "status", "source", "notes")
TRACKER_COLUMNS = ("application_id",) + TRACKER_FIELDS + ("updated_at",)


class MigrationChanged(Exception):
    """An applied migration file was edited – migrations are immutable; add a new file instead."""


class InvalidRequest(ValueError):
    pass


# ---------- SQL scripts ----------

_TOKEN = re.compile(r"--[^\n]*|'(?:[^']|'')*'|\$(\w*)\$.*?\$\1\$|;", re.DOTALL)


def split_statements(sql: str) -> list[str]:
    """Split a script on top-level semicolons, respecting comments, quoted strings and $$-bodies (DO blocks)."""
    statements, start = [], 0
    for match in _TOKEN.finditer(sql):
        if match.group(0) == ";":
            statements.append(sql[start : match.start()])
            start = match.end()
    statements.append(sql[start:])
    return [s.strip() for s in statements if re.sub(r"--[^\n]*", "", s).strip()]


def run_script(conn: Any, sql: str) -> int:
    cursor = conn.cursor()
    statements = split_statements(sql)
    for statement in statements:
        cursor.execute(statement)
    return len(statements)


def checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


# ---------- migrations ----------


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))


def apply_migrations(conn: Any, files: list[Path] | None = None) -> list[str]:
    """Apply each pending file in its own transaction; refuse if an applied file has changed."""
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS meta.schema_migrations ("
        "name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    cursor.execute("SELECT name, checksum FROM meta.schema_migrations")
    applied = dict(cursor.fetchall())

    newly_applied = []
    for path in migration_files() if files is None else files:
        sql = path.read_text()
        if path.name in applied:
            if applied[path.name] != checksum(sql):
                raise MigrationChanged(f"{path.name} was changed after it was applied")
            continue
        try:
            run_script(conn, sql)
            cursor.execute(
                "INSERT INTO meta.schema_migrations (name, checksum) VALUES (%s, %s)", (path.name, checksum(sql))
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        newly_applied.append(path.name)
        log.info("migration applied", extra={"fields": {"migration": path.name}})
    return newly_applied


def status(conn: Any) -> dict:
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM meta.schema_migrations ORDER BY name")
    migrations = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    vector = cursor.fetchone()
    counts = {}
    for table in ("tracker.applications", "vec.posting_vectors"):
        cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 – fixed table names
        counts[table] = cursor.fetchone()[0]
    pending = [p.name for p in migration_files() if p.name not in migrations]
    return {"migrations": migrations, "pending": pending, "pgvector": vector[0] if vector else None, "rows": counts}


# ---------- tracker ----------


def _validated(application: dict, update: bool) -> dict:
    unknown = set(application) - set(TRACKER_FIELDS) - {"application_id"}
    if unknown:
        raise InvalidRequest(f"unknown fields: {sorted(unknown)}")
    fields = {k: application[k] for k in TRACKER_FIELDS if k in application}
    if not update:
        missing = [k for k in ("company", "role_title", "applied_date") if not fields.get(k)]
        if missing:
            raise InvalidRequest(f"missing required fields: {missing}")
        fields.setdefault("status", "applied")
    if "status" in fields and fields["status"] not in STATUSES:
        raise InvalidRequest(f"status must be one of {list(STATUSES)}")
    if "applied_date" in fields:
        try:
            fields["applied_date"] = date.fromisoformat(str(fields["applied_date"]))
        except ValueError as exc:
            raise InvalidRequest("applied_date must be YYYY-MM-DD") from exc
    if update and not fields:
        raise InvalidRequest("nothing to update")
    return fields


def _row(columns: tuple[str, ...], values: tuple) -> dict:
    return {c: (v.isoformat() if hasattr(v, "isoformat") else v) for c, v in zip(columns, values, strict=True)}


def tracker_upsert(conn: Any, application: dict) -> dict:
    application_id = application.get("application_id")
    fields = _validated(application, update=application_id is not None)
    cursor = conn.cursor()
    returning = ", ".join(TRACKER_COLUMNS)
    if application_id is None:
        columns = ", ".join(fields)
        placeholders = ", ".join(["%s"] * len(fields))
        cursor.execute(
            f"INSERT INTO tracker.applications ({columns}) VALUES ({placeholders}) RETURNING {returning}",  # noqa: S608 – whitelisted columns
            tuple(fields.values()),
        )
    else:
        assignments = ", ".join(f"{k} = %s" for k in fields)
        cursor.execute(
            f"UPDATE tracker.applications SET {assignments}, updated_at = now() "  # noqa: S608 – whitelisted columns
            f"WHERE application_id = %s RETURNING {returning}",
            (*fields.values(), int(application_id)),
        )
    row = cursor.fetchone()
    if row is None:
        conn.rollback()
        raise InvalidRequest(f"application_id {application_id} does not exist")
    conn.commit()
    return _row(TRACKER_COLUMNS, row)


def tracker_list(conn: Any) -> list[dict]:
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT {', '.join(TRACKER_COLUMNS)} FROM tracker.applications "  # noqa: S608 – fixed columns
        "ORDER BY applied_date DESC, application_id DESC"
    )
    return [_row(TRACKER_COLUMNS, row) for row in cursor.fetchall()]


# ---------- entry point ----------


def handler(event: dict, context: Any, connect_fn=connect) -> Any:
    mode = event.get("mode", "migrate")
    log.info("db_migrate invoked", extra={"fields": {"mode": mode}})  # never the event itself

    if mode == "bootstrap":
        conn = connect_fn(os.environ["MASTER_USER"], event["master_password"])
        try:
            count = run_script(conn, (HERE / "bootstrap.sql").read_text())
            conn.commit()
        finally:
            conn.close()
        return {"mode": mode, "statements": count}

    conn = connect_fn("migrator")
    try:
        if mode == "migrate":
            return {"mode": mode, "applied": apply_migrations(conn)}
        if mode == "status":
            return {"mode": mode, **status(conn)}
        if mode == "tracker_upsert":
            return {"mode": mode, "application": tracker_upsert(conn, event["application"])}
        if mode == "tracker_list":
            return {"mode": mode, "applications": tracker_list(conn)}
        raise InvalidRequest(f"unknown mode {mode!r}")
    finally:
        conn.close()
