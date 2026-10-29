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


def normalise_skill(skill: str) -> str | None:
    s = re.sub(r"\s+", " ", skill.strip().lower())
    s = TAXONOMY.get(s, s)
    return s if SKILL_PATTERN.match(s) else None


def normalise(fields: dict) -> dict:
    """Canonical skills (taxonomy, dedup, max 25) and sorted cloud list; call only on valid fields."""
    skills: list[str] = []
    for raw in fields["skills"]:
        skill = normalise_skill(raw)
        if skill and skill not in skills:
            skills.append(skill)
    return {**fields, "skills": skills[:MAX_SKILLS], "cloud_platforms": sorted(set(fields["cloud_platforms"]))}
