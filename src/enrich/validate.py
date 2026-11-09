"""Validate and normalise the fields the model returns (schema.json + business rules, SDD §4.3)."""

import json
import re
from pathlib import Path
from typing import Any

SENIORITY = {"junior", "mid", "senior", "lead", "principal"}
GERMAN_LEVELS = {"none", "basic", "fluent"}
REMOTE = {"onsite", "hybrid", "remote"}
CLOUDS = {"aws", "azure", "gcp"}
SALARY_MIN, SALARY_MAX = 10_000, 400_000
MAX_SKILLS = 25
SKILL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+#./ \-]{0,48}$")
TAXONOMY: dict[str, str] = json.loads((Path(__file__).parent / "skills_taxonomy.json").read_text())


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def errors(fields: Any) -> list[str]:
    """Return human-readable problems; an empty list means the fields are valid."""
    if not isinstance(fields, dict):
        return ["the tool input is not an object"]
    problems = []

    skills = fields.get("skills")
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        problems.append("skills must be a list of strings")

    clouds = fields.get("cloud_platforms")
    if not isinstance(clouds, list) or not set(clouds) <= CLOUDS:
        problems.append(f"cloud_platforms must be a list with values from {sorted(CLOUDS)}")

    if fields.get("seniority") not in SENIORITY | {None}:
        problems.append(f"seniority must be one of {sorted(SENIORITY)} or null")

    low, high = fields.get("salary_min_eur"), fields.get("salary_max_eur")
    for name, value in (("salary_min_eur", low), ("salary_max_eur", high)):
        if value is not None and (not _is_int(value) or not SALARY_MIN <= value <= SALARY_MAX):
            problems.append(f"{name} must be null or a yearly EUR integer between {SALARY_MIN} and {SALARY_MAX}")
    if _is_int(low) and _is_int(high) and low > high:
        problems.append("salary_min_eur must not be greater than salary_max_eur")

    if fields.get("german_level_required") not in GERMAN_LEVELS:
        problems.append(f"german_level_required must be one of {sorted(GERMAN_LEVELS)}")

    for name in ("english_only", "visa_sponsorship_mentioned"):
        if not isinstance(fields.get(name), bool):
            problems.append(f"{name} must be true or false")

    if fields.get("english_only") is True and fields.get("german_level_required") == "fluent":
        problems.append("english_only cannot be true when german_level_required is fluent")

    if fields.get("remote_policy") not in REMOTE | {None}:
        problems.append(f"remote_policy must be one of {sorted(REMOTE)} or null")

    return problems


# "59.000", "70,000", "65 000" (thousands separators), "4.500", "65k", "14,25"
_AMOUNT = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.,\s]\d{3})+|\d+(?:[.,]\d{1,2})?)\s*(k\b|tsd\b|tausend\b)?", re.IGNORECASE
)
SALARY_TOLERANCE = 0.01


def salary_amounts(text: str) -> set[int]:
    """Every yearly amount the text can support: each number as stated, × 12 (monthly) and × 1000 for "65k"."""
    amounts = set()
    for raw, thousands in _AMOUNT.findall(text):
        digits = re.sub(r"[.,\s](?=\d{3}(?!\d))", "", raw).replace(",", ".")
        try:
            value = float(digits)
        except ValueError:
            continue
        if thousands:
            value *= 1000
        amounts.update({round(value), round(value * 12)})
    return amounts


def ungrounded_salaries(fields: dict, text: str) -> list[str]:
    """Salary values that do not come from any amount in the ad – the model invented or mis-derived them."""
    amounts = salary_amounts(text)
    problems = []
    for name in ("salary_min_eur", "salary_max_eur"):
        value = fields.get(name)
        if _is_int(value) and not any(abs(value - a) <= SALARY_TOLERANCE * value for a in amounts):
            problems.append(
                f"{name}={value} does not appear in the advertisement as a yearly amount or a monthly amount × 12; "
                "use null unless the ad states a salary for this job"
            )
    return problems


def normalise_skill(skill: str) -> str | None:
    s = re.sub(r"\s+", " ", skill.strip().lower())
    s = TAXONOMY.get(s, s)
    return s if SKILL_PATTERN.match(s) else None


NULLABLE = ("seniority", "salary_min_eur", "salary_max_eur", "remote_policy")


def normalise(fields: dict) -> dict:
    """Canonical skills (taxonomy, dedup, max 25), sorted cloud list, and null for omitted nullable fields.

    Call only on valid fields. Models sometimes leave out a field instead of sending null; errors() treats a missing
    nullable field as null, so normalise() makes that explicit for everything downstream (SQL insert, scoring).
    """
    skills: list[str] = []
    for raw in fields["skills"]:
        skill = normalise_skill(raw)
        if skill and skill not in skills:
            skills.append(skill)
    return {
        **dict.fromkeys(NULLABLE),
        **fields,
        "skills": skills[:MAX_SKILLS],
        "cloud_platforms": sorted(set(fields["cloud_platforms"])),
    }
