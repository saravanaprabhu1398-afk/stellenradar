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


def test_english_only_contradicts_fluent_german():
    assert v.errors({**VALID, "english_only": True, "german_level_required": "fluent"}) == [
        "english_only cannot be true when german_level_required is fluent"
    ]


def test_normalise_fills_omitted_nullable_fields():
    partial = {
        k: v for k, v in VALID.items() if k not in ("seniority", "salary_min_eur", "salary_max_eur", "remote_policy")
    }
    assert v.errors(partial) == []
    out = v.normalise(partial)
    assert all(out[f] is None for f in ("seniority", "salary_min_eur", "salary_max_eur", "remote_policy"))


def test_salary_amounts_cover_yearly_monthly_and_k():
    amounts = v.salary_amounts("ab 59.000 € p.a., oder 4.500 € monatlich, bis 70,000 EUR, 65k, 14,25 Gehälter")
    assert {59000, 54000, 70000, 65000} <= amounts
    assert 142500 not in amounts


def test_unsupported_salaries_need_an_exact_amount():
    text = "Gehalt: 60.000 - 74.000 € brutto. Deals von €3M–5M. Tel. 0228/4108-3651"
    assert v.unsupported({"salary_min_eur": 60000, "salary_max_eur": 74000}, text) == {}
    invented = v.unsupported({"salary_min_eur": 100000, "salary_max_eur": None}, text)
    assert list(invented) == ["salary_min_eur"]
    # 3651 x 12 = 43812: a tolerance would have let an estimated 44100 pass
    assert list(v.unsupported({"salary_min_eur": 44100, "salary_max_eur": None}, text)) == ["salary_min_eur"]


def test_english_only_needs_an_explicit_statement():
    fields = {"english_only": True}
    assert "english_only" in v.unsupported(fields, "We are an international team. Python, AWS.")
    statements = (
        "German is not required.",
        "Deutschkenntnisse sind nicht erforderlich",
        "Our working language is English",
        "English is sufficient",
        "keine Deutschkenntnisse nötig",
    )
    for text in statements:
        assert v.unsupported(fields, text) == {}, text
    assert v.unsupported({"english_only": False}, "anything") == {}


def test_open_ended_salary_clears_the_copied_maximum():
    both = {"salary_min_eur": 59000, "salary_max_eur": 59000}
    assert v.open_ended(both, "Jahresbruttogehalt mind. 59.000 Euro mit Überzahlung")["salary_max_eur"] is None
    assert v.open_ended(both, "Gehalt ab 59.000 €")["salary_max_eur"] is None
    assert v.open_ended(both, "Festgehalt 59.000 € p.a.")["salary_max_eur"] == 59000
    assert v.open_ended({"salary_min_eur": 50000, "salary_max_eur": 60000}, "ab 50.000")["salary_max_eur"] == 60000
