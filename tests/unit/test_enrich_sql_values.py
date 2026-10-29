from enrich.sql_values import COLUMNS, insert_statement, literal


def test_literals():
    assert literal(None) == "NULL"
    assert literal(True) == "true"
    assert literal(70000) == "70000"
    assert literal("O'Reilly") == "'O''Reilly'"
    assert literal([]) == "CAST(ARRAY[] AS array(varchar))"
    assert literal(["a", "b'c"]) == "ARRAY['a', 'b''c']"


def row(**overrides):
    base = {c: None for c in COLUMNS}
    base.update(refnr="R1", content_hash="h", prompt_version="v1", skills=["python"], cloud_platforms=[],
                english_only=False, enriched_at="2026-10-28 06:10:00", input_tokens=10)  # fmt: skip
    base.update(overrides)
    return base


def test_insert_statement_is_idempotent_and_typed():
    sql = insert_statement("db", [row(), row(refnr="R2", employer_note="ignored")])
    assert sql.startswith("INSERT INTO db.posting_enrichment (refnr, content_hash, prompt_version")
    assert "TIMESTAMP '2026-10-28 06:10:00'" in sql
    assert "'R2'" in sql
    assert "ignored" not in sql
    assert "WHERE NOT EXISTS" in sql
    assert "e.prompt_version = s.prompt_version" in sql


def test_null_timestamp_is_plain_null():
    assert "TIMESTAMP NULL" not in insert_statement("db", [row(enriched_at=None)])
