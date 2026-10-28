"""Build the INSERT for curated.posting_enrichment from validated extractions.

Values come from the model, so every value is either validated against a fixed set (enums, booleans, ints) or a
string escaped as a SQL literal; skills additionally match a strict character pattern (validate.SKILL_PATTERN).
"""

from collections.abc import Iterable

COLUMNS = (
    "refnr", "content_hash", "prompt_version", "model_id", "skills", "cloud_platforms", "seniority",
    "salary_min_eur", "salary_max_eur", "german_level_required", "english_only", "visa_sponsorship_mentioned",
    "remote_policy", "input_tokens", "output_tokens", "enriched_at", "run_id",
)  # fmt: skip


def literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        if not value:
            return "CAST(ARRAY[] AS array(varchar))"
        return "ARRAY[" + ", ".join(literal(v) for v in value) + "]"
    return "'" + str(value).replace("'", "''") + "'"


def insert_statement(db_curated: str, rows: Iterable[dict]) -> str:
    """INSERT … SELECT FROM (VALUES …) skipping rows that already exist (safe to re-run)."""
    values = ",\n  ".join(
        "(" + ", ".join(
            f"TIMESTAMP {literal(row[c])}" if c == "enriched_at" and row[c] is not None else literal(row[c])
            for c in COLUMNS
        ) + ")"
        for row in rows
    )  # fmt: skip
    cols = ", ".join(COLUMNS)
    return (
        f"INSERT INTO {db_curated}.posting_enrichment ({cols})\n"  # noqa: S608 – values escaped/validated above
        f"SELECT {cols} FROM (VALUES\n  {values}\n) AS s ({cols})\n"
        f"WHERE NOT EXISTS (SELECT 1 FROM {db_curated}.posting_enrichment e\n"
        "  WHERE e.refnr = s.refnr AND e.content_hash = s.content_hash AND e.prompt_version = s.prompt_version)"
    )
