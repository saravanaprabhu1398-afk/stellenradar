import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import athena_sql  # noqa: E402

from curate.athena import CHECKS  # noqa: E402


def _checks(**overrides):
    base = {name: 0 for name in CHECKS}
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
