from enrich import validate as v

VALID = {
    "skills": ["Python", "PySpark", " Apache  Airflow ", "python", "<script>"],
    "cloud_platforms": ["azure", "aws", "aws"],
    "seniority": "senior",
    "salary_min_eur": 70000,
    "salary_max_eur": 90000,
    "german_level_required": "basic",
    "english_only": False,
    "visa_sponsorship_mentioned": True,
    "remote_policy": "hybrid",
}


def test_valid_fields_have_no_errors():
    assert v.errors(VALID) == []
    assert v.errors({**VALID, "seniority": None, "remote_policy": None, "salary_min_eur": None}) == []


def test_not_an_object():
    assert v.errors(["x"]) == ["the tool input is not an object"]


def test_each_rule_reports_a_problem():
    bad = {
        "skills": "python",
        "cloud_platforms": ["ibm"],
        "seniority": "rockstar",
        "salary_min_eur": 5000,
        "salary_max_eur": True,
        "german_level_required": "C1",
        "english_only": "yes",
        "remote_policy": "anywhere",
    }
    problems = " | ".join(v.errors(bad))
    for name in ("skills", "cloud_platforms", "seniority", "salary_min_eur", "salary_max_eur",
                 "german_level_required", "english_only", "visa_sponsorship_mentioned", "remote_policy"):  # fmt: skip
        assert name in problems


def test_salary_min_above_max():
    assert v.errors({**VALID, "salary_min_eur": 95000}) == ["salary_min_eur must not be greater than salary_max_eur"]


def test_normalise_skills_and_clouds():
    out = v.normalise(VALID)
    assert out["skills"] == ["python", "spark", "airflow"]
    assert out["cloud_platforms"] == ["aws", "azure"]


def test_normalise_caps_skills():
    out = v.normalise({**VALID, "skills": [f"tool{i}" for i in range(40)]})
    assert len(out["skills"]) == v.MAX_SKILLS


def test_normalise_skill_rejects_unsafe_text():
    assert v.normalise_skill("C++") == "c++"
    assert v.normalise_skill("x'; DROP TABLE t; --") is None
