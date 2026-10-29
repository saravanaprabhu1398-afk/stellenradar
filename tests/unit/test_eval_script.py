import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import eval_extraction as ev  # noqa: E402

LABELS = {
    "skills": ["Python", "PySpark", "sql"],
    "cloud_platforms": ["aws"],
    "seniority": "senior",
    "salary_min_eur": 70000,
    "salary_max_eur": 80000,
    "german_level_required": "fluent",
    "english_only": False,
    "visa_sponsorship_mentioned": False,
    "remote_policy": "hybrid",
}


def candidate(i, **kw):
    base = {"refnr": f"R{i:03d}", "content_hash": f"h{i}", "title": "Data Engineer", "employer": "ACME",
            "location": "Berlin", "description": "Wir suchen dich und die Daten für das Team mit Python.",
            "seniority": None, "german_level_required": "fluent", "english_only": "false", "remote_policy": None,
            "salary_min_eur": None, "salary_max_eur": None, "visa_sponsorship_mentioned": "false",
            "skills": "python|sql", "clouds": ""}  # fmt: skip
    base.update(kw)
    return base


def test_tags_language_salary_and_strata():
    english = candidate(1, description="We are looking for a data engineer with Python. Salary 70,000 EUR.")
    assert ev.tags(english) >= {"english_ad", "salary_hint", "seniority_unknown"}
    german = candidate(2, seniority="lead", remote_policy="remote")
    assert ev.tags(german) >= {"german_ad", "lead_plus", "remote"}
    assert "not_enriched" in ev.tags(candidate(3, german_level_required=None, skills=""))


def test_stratified_sample_is_deterministic_and_fills_quotas():
    pool = [candidate(i) for i in range(200)]
    pool += [candidate(300 + i, description="English only role. We use Python and SQL every day.") for i in range(15)]
    pool += [candidate(400 + i, seniority="senior") for i in range(10)]
    first = ev.stratified_sample(pool)
    assert len(first) == ev.SET_SIZE
    assert [c["refnr"] for c in first] == [c["refnr"] for c in ev.stratified_sample(list(reversed(pool)))]
    assert sum("english_ad" in ev.tags(c) for c in first) >= 10
    assert sum("senior" in ev.tags(c) for c in first) >= 5


def test_prefill_converts_athena_strings():
    labels = ev.prefill(candidate(1, salary_min_eur="60000", english_only="true", clouds="aws|gcp"))
    assert labels["salary_min_eur"] == 60000
    assert labels["salary_max_eur"] is None
    assert labels["english_only"] is True
    assert labels["cloud_platforms"] == ["aws", "gcp"]
    assert labels["skills"] == ["python", "sql"]


def test_labelling_file_round_trip(tmp_path):
    path = tmp_path / "labelling.yaml"
    ev.write_labelling(ev.labelling_items([candidate(1, description="Zeile 1\nZeile 2")]), path)
    text = path.read_text()
    assert "description: |" in text
    yaml, _ = ev._yaml()
    item = yaml.safe_load(text)[0]
    assert item["checked"] is False
    assert item["url"].endswith("/R001")
    assert item["description"] == "Zeile 1\nZeile 2"


def test_freeze_validates_and_splits():
    items = [{"refnr": f"R{i}", "content_hash": "h", "checked": True, "labels": dict(LABELS)} for i in range(20)]
    items.append({"refnr": "SKIP", "content_hash": "h", "skip": True, "labels": {}})
    rows, problems = ev.freeze(items)
    assert problems == []
    assert len(rows) == 20
    assert sum(r["split"] == "holdout" for r in rows) == ev.HOLDOUT

    contradiction = {**LABELS, "english_only": True}
    bad = [
        {"refnr": "A", "content_hash": "h", "checked": False, "labels": LABELS},
        {"refnr": "B", "content_hash": "h", "checked": True, "labels": contradiction},
    ]
    _, problems = ev.freeze(bad)
    assert problems[0] == "A: not checked yet"
    assert "english_only cannot be true" in problems[1]


def test_scoring_rules():
    expected = ev.canonical(LABELS)
    assert expected["skills"] == ["python", "spark", "sql"]
    assert all(ev.judge(expected, expected).values())
    near = {**expected, "salary_min_eur": 72000, "skills": ["python", "spark", "sql", "airflow"]}
    assert all(ev.judge(near, expected).values())  # within 5 % and skills F1 0.86
    off = {**expected, "salary_max_eur": 960000, "seniority": None, "skills": ["excel"]}
    judged = ev.judge(off, expected)
    assert not judged["salary_max_eur"]
    assert not judged["seniority"]
    assert not judged["skills"]
    assert not any(ev.judge(None, expected).values())
    assert ev.salary_ok(None, None)
    assert not ev.salary_ok(50000, None)
    assert ev.set_f1([], [])[0] == 1.0


def test_parse_config_and_price_key():
    assert ev.parse_config("eu.amazon.nova-lite-v1:0@v2") == ("eu.amazon.nova-lite-v1:0", "v2")
    with pytest.raises(ValueError, match="MODEL_ID@PROMPT_VERSION"):
        ev.parse_config("nova")
    assert ev.model_price_key("eu.amazon.nova-pro-v1:0") == "amazon.nova-pro-v1:0"
    assert ev.model_price_key("amazon.nova-pro-v1:0") == "amazon.nova-pro-v1:0"


class FakeAthena:
    def __init__(self, rows):
        self.result = rows
        self.sql = []

    def rows(self, sql, label):
        self.sql.append(sql)
        return self.result


class FakeBedrock:
    """Returns the labels as the tool input; postings whose refnr ends in 9 get an invalid answer."""

    def __init__(self, labelled, fail_models=()):
        self.by_text = {f"Ad {r['refnr']}": r for r in labelled}
        self.fail_models = fail_models

    def converse(self, modelId, messages, **kwargs):  # noqa: N803
        if modelId in self.fail_models:
            raise RuntimeError("AccessDeniedException: model not enabled")
        text = messages[0]["content"][0]["text"]
        row = next(r for key, r in self.by_text.items() if key in text)
        answer = dict(row["labels"])
        if row["refnr"].endswith("9"):
            answer["seniority"] = "rockstar"
        return {"output": {"message": {"content": [{"toolUse": {"toolUseId": "t", "input": answer}}]}},
                "usage": {"inputTokens": 2000, "outputTokens": 150}}  # fmt: skip


def labelled_rows(n=20):
    rows, _ = ev.freeze([{"refnr": f"R{i:02d}", "content_hash": "h", "checked": True, "labels": dict(LABELS)}
                         for i in range(n)])  # fmt: skip
    return rows


def test_score_end_to_end(tmp_path, monkeypatch, capsys):
    rows = labelled_rows()
    monkeypatch.setattr(ev, "WORK", tmp_path / "work")
    monkeypatch.setattr(ev, "LABELLED_SET", tmp_path / "labelled_set.jsonl")
    ev.LABELLED_SET.write_text("".join(json.dumps(r) + "\n" for r in rows))
    texts = [{"refnr": r["refnr"], "content_hash": "h", "title": "t", "employer": "e", "location": "l",
              "description": f"Ad {r['refnr']}"} for r in rows]  # fmt: skip
    athena = FakeAthena(texts)
    bedrock = FakeBedrock(rows, fail_models=("eu.amazon.nova-pro-v1:0",))

    assert ev.main(["score", "--config", "eu.amazon.nova-lite-v1:0@v2", "--config", "eu.amazon.nova-pro-v1:0@v2"],
                   athena=athena, bedrock=bedrock) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "| `eu.amazon.nova-lite-v1:0@v2` | 90% |" in out  # 2 of 20 dead-lettered
    assert "FAIL" in out
    assert "2 invalid after retry" in out
    assert "`eu.amazon.nova-pro-v1:0@v2` not run: RuntimeError" in out
    assert "IN ('R00', 'R01'" in athena.sql[0]
    reports = list((tmp_path / "work").glob("report-*.md"))
    assert len(reports) == 1
    saved = json.loads(next((tmp_path / "work").glob("results-*.json")).read_text())
    summary = saved["summaries"][0]
    assert summary["dead_letters"] == 2
    assert summary["cost_per_1k_usd"] == pytest.approx((2000 * 0.06 + 150 * 0.24) / 1e6 * 1000 * 1.1, rel=0.01)


def test_score_reports_missing_versions(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ev, "LABELLED_SET", tmp_path / "labelled_set.jsonl")
    ev.LABELLED_SET.write_text(json.dumps(labelled_rows(1)[0]) + "\n")
    assert ev.main(["score"], athena=FakeAthena([]), bedrock=object()) == 1
    assert "not found" in capsys.readouterr().out


def test_sample_and_freeze_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ev, "ROOT", tmp_path)
    monkeypatch.setattr(ev, "LABELLING", tmp_path / "eval" / "work" / "labelling.yaml")
    monkeypatch.setattr(ev, "LABELLED_SET", tmp_path / "eval" / "labelled_set.jsonl")
    athena = FakeAthena([candidate(i) for i in range(60)])

    assert ev.main(["sample"], athena=athena) == 0
    assert ev.main(["sample"], athena=athena) == 1  # refuses to overwrite labels
    assert ev.main(["freeze"]) == 1  # nothing checked yet

    yaml, dumper = ev._yaml()
    items = yaml.safe_load(ev.LABELLING.read_text())
    for item in items:
        item["checked"] = True
        item["labels"]["german_level_required"] = "fluent"
    ev.LABELLING.write_text(yaml.dump(items, Dumper=dumper, allow_unicode=True, sort_keys=False))
    assert ev.main(["freeze"]) == 0
    assert len(ev.LABELLED_SET.read_text().splitlines()) == ev.SET_SIZE
    assert "Wrote 50 labelled postings" in capsys.readouterr().out
