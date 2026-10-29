"""Evaluate field extraction against a hand-labelled set (test plan §4).

    python scripts/eval_extraction.py sample              # 50 stratified postings -> eval/work/labelling.yaml
    # label them by hand (eval/labelling_guide.md), set checked: true on each
    python scripts/eval_extraction.py freeze              # -> eval/labelled_set.jsonl (labels only, committed)
    python scripts/eval_extraction.py score [--config MODEL@PROMPT ...]   # -> eval/work/report-<ts>.md

The labelled set stores refnr + content_hash + labels, never ad text: `score` reads the text of exactly that posting
version from curated.postings, so no BA content is committed (security doc §9).
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from curate.athena import DB_CURATED, Athena  # noqa: E402
from enrich.extract import Extractor  # noqa: E402
from enrich.validate import errors, normalise_skill  # noqa: E402

WORK = ROOT / "eval" / "work"
LABELLING = WORK / "labelling.yaml"
LABELLED_SET = ROOT / "eval" / "labelled_set.jsonl"
SET_SIZE = 50
HOLDOUT = 15
SEED = 20261029

FIELDS = (
    "skills", "cloud_platforms", "seniority", "salary_min_eur", "salary_max_eur",
    "german_level_required", "english_only", "visa_sponsorship_mentioned", "remote_policy",
)  # fmt: skip
SET_FIELDS = ("skills", "cloud_platforms")
SALARY_FIELDS = ("salary_min_eur", "salary_max_eur")
SET_F1_CORRECT = 0.8
SALARY_TOLERANCE = 0.05

DEFAULT_CONFIGS = (
    "eu.amazon.nova-lite-v1:0@v1",
    "eu.amazon.nova-lite-v1:0@v2",
    "eu.amazon.nova-2-lite-v1:0@v2",
    "eu.amazon.nova-pro-v1:0@v2",
)
# USD per 1M input / output tokens. Approximate list prices – check the Bedrock pricing page for the EU profiles.
PRICES = {
    "amazon.nova-lite-v1:0": (0.06, 0.24),
    "amazon.nova-2-lite-v1:0": (0.30, 2.50),
    "amazon.nova-pro-v1:0": (0.80, 3.20),
}

GATE = {"overall": 0.90, "holdout": 0.85, "field": 0.75, "salary_hallucination": 0.02, "dead_letters": 0}

CANDIDATES_SQL = """
SELECT p.refnr, p.content_hash, p.title, p.employer, p.location, p.description,
       e.seniority, e.german_level_required, e.english_only, e.remote_policy, e.salary_min_eur, e.salary_max_eur,
       e.visa_sponsorship_mentioned, array_join(e.skills, '|') AS skills, array_join(e.cloud_platforms, '|') AS clouds
FROM {db}.postings p
LEFT JOIN {db}.posting_enrichment e
  ON e.refnr = p.refnr AND e.content_hash = p.content_hash AND e.prompt_version = 'v1'
WHERE p.is_current AND p.description IS NOT NULL
"""

TEXT_SQL = """
SELECT refnr, content_hash, title, employer, location, description
FROM {db}.postings
WHERE refnr IN ({refnrs})
"""

SALARY_HINT = re.compile(r"(€|eur\b|euro|gehalt|salary|vergütung.{0,40}\d)", re.IGNORECASE)
GERMAN_WORDS = re.compile(r"\b(und|wir|sie|für|mit|der|die|das|ihre|bei)\b", re.IGNORECASE)


# ---------- sample ----------


def is_english(text: str) -> bool:
    words = max(len(text.split()), 1)
    return len(GERMAN_WORDS.findall(text)) / words < 0.02


def tags(c: dict) -> set[str]:
    """Strata used for quota sampling; based on the text and the (unverified) v1 prediction."""
    text = c.get("description") or ""
    t = {"english_ad" if is_english(text) else "german_ad"}
    if SALARY_HINT.search(text):
        t.add("salary_hint")
    if c.get("german_level_required") is None and c.get("seniority") is None and not c.get("skills"):
        t.add("not_enriched")
    t.add({"junior": "junior_mid", "mid": "junior_mid", "senior": "senior", "lead": "lead_plus",
           "principal": "lead_plus"}.get(c.get("seniority") or "", "seniority_unknown"))  # fmt: skip
    if c.get("remote_policy"):
        t.add(c["remote_policy"])
    return t


QUOTAS = {
    "german_ad": 20, "english_ad": 10, "salary_hint": 10, "junior_mid": 5, "senior": 5, "lead_plus": 5,
    "remote": 5, "hybrid": 5, "onsite": 5, "not_enriched": 3,
}  # fmt: skip


def stratified_sample(candidates: list[dict], size: int = SET_SIZE, seed: int = SEED) -> list[dict]:
    """Greedy quota fill: repeatedly take the candidate covering the most unmet quotas, then fill at random."""
    pool = sorted(candidates, key=lambda c: c["refnr"])
    random.Random(seed).shuffle(pool)  # noqa: S311 – reproducible sampling, not security
    need = dict(QUOTAS)
    picked: list[dict] = []
    while pool and len(picked) < size:
        best = max(range(len(pool)), key=lambda i: sum(1 for t in tags(pool[i]) if need.get(t, 0) > 0))
        chosen = pool.pop(best)
        for t in tags(chosen):
            if need.get(t, 0) > 0:
                need[t] -= 1
        picked.append(chosen)
    return picked


def prefill(c: dict) -> dict:
    def as_int(v):
        return int(v) if v not in (None, "") else None

    def as_bool(v):
        return None if v is None else v == "true"

    return {
        "skills": [s for s in (c.get("skills") or "").split("|") if s],
        "cloud_platforms": [s for s in (c.get("clouds") or "").split("|") if s],
        "seniority": c.get("seniority"),
        "salary_min_eur": as_int(c.get("salary_min_eur")),
        "salary_max_eur": as_int(c.get("salary_max_eur")),
        "german_level_required": c.get("german_level_required"),
        "english_only": as_bool(c.get("english_only")),
        "visa_sponsorship_mentioned": as_bool(c.get("visa_sponsorship_mentioned")),
        "remote_policy": c.get("remote_policy"),
    }


def labelling_items(sample: list[dict]) -> list[dict]:
    return [
        {
            "refnr": c["refnr"],
            "content_hash": c["content_hash"],
            "url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{c['refnr']}",
            "checked": False,
            "skip": False,
            "labels": prefill(c),
            "title": c.get("title"),
            "employer": c.get("employer"),
            "location": c.get("location"),
            "description": c.get("description"),
        }
        for c in sample
    ]


def _yaml():
    import yaml

    class Dumper(yaml.SafeDumper):
        pass

    def str_presenter(dumper, data):
        style = "|" if "\n" in data else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)

    Dumper.add_representer(str, str_presenter)
    return yaml, Dumper


def write_labelling(items: list[dict], path: Path | None = None) -> None:
    yaml, dumper = _yaml()
    path = path or LABELLING
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Label each posting with eval/labelling_guide.md. Labels are pre-filled from prompt v1 – check every\n"
        "# field against the ad, fix it, then set checked: true. Set skip: true only for unreadable ads.\n"
    )
    path.write_text(header + yaml.dump(items, Dumper=dumper, allow_unicode=True, sort_keys=False, width=120))


# ---------- freeze ----------


def split_for(refnr: str) -> int:
    return int(hashlib.sha256(refnr.encode()).hexdigest(), 16)


def freeze(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Checked, non-skipped items -> labelled set rows with a deterministic dev/holdout split; returns problems."""
    problems, rows = [], []
    for item in items:
        if item.get("skip"):
            continue
        if not item.get("checked"):
            problems.append(f"{item['refnr']}: not checked yet")
            continue
        labels = item["labels"]
        for p in errors(labels):
            problems.append(f"{item['refnr']}: {p}")
        rows.append({"refnr": item["refnr"], "content_hash": item["content_hash"], "labels": labels})
    for i, row in enumerate(sorted(rows, key=lambda r: split_for(r["refnr"]))):
        row["split"] = "holdout" if i < HOLDOUT else "dev"
    return sorted(rows, key=lambda r: r["refnr"]), problems


# ---------- score ----------


def set_f1(predicted: list[str], expected: list[str]) -> tuple[float, int, int, int]:
    p, e = set(predicted), set(expected)
    tp = len(p & e)
    if not p and not e:
        return 1.0, 0, 0, 0
    f1 = 2 * tp / (len(p) + len(e))
    return f1, tp, len(p) - tp, len(e) - tp


def salary_ok(predicted, expected) -> bool:
    if expected is None or predicted is None:
        return predicted is None and expected is None
    return abs(predicted - expected) <= SALARY_TOLERANCE * expected


def canonical(labels: dict) -> dict:
    skills = [s for s in (normalise_skill(x) for x in labels.get("skills") or []) if s]
    return {
        **labels,
        "skills": sorted(set(skills)),
        "cloud_platforms": sorted(set(labels.get("cloud_platforms") or [])),
    }


def judge(predicted: dict | None, expected: dict) -> dict[str, bool]:
    if predicted is None:
        return dict.fromkeys(FIELDS, False)
    out = {}
    for f in FIELDS:
        if f in SET_FIELDS:
            out[f] = set_f1(predicted[f], expected[f])[0] >= SET_F1_CORRECT
        elif f in SALARY_FIELDS:
            out[f] = salary_ok(predicted[f], expected[f])
        else:
            out[f] = predicted[f] == expected[f]
    return out


def model_price_key(model_id: str) -> str:
    return model_id.split(".", 1)[1] if model_id.split(".", 1)[0] in ("eu", "us", "global", "apac") else model_id


def summarise(config: str, model_id: str, results: list[dict]) -> dict:
    n = len(results)

    def accuracy(rows):
        return sum(sum(r["judged"].values()) for r in rows) / (len(FIELDS) * len(rows)) if rows else 0.0

    per_field = {f: sum(r["judged"][f] for r in results) / n for f in FIELDS}
    tp = fp = fn = 0
    for r in results:
        if r["predicted"] is not None:
            _, a, b, c = set_f1(r["predicted"]["skills"], r["expected"]["skills"])
            tp, fp, fn = tp + a, fp + b, fn + c
        else:
            fn += len(r["expected"]["skills"])
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0

    def has_salary(fields):
        return fields is not None and any(fields[f] is not None for f in SALARY_FIELDS)

    null_salary = [r for r in results if not has_salary(r["expected"])]
    hallucinated = [r for r in null_salary if has_salary(r["predicted"])]
    tokens_in = sum(r["input_tokens"] for r in results)
    tokens_out = sum(r["output_tokens"] for r in results)
    price = PRICES.get(model_price_key(model_id))
    cost_1k = (tokens_in * price[0] + tokens_out * price[1]) / 1e6 / n * 1000 if price else None
    summary = {
        "config": config,
        "n": n,
        "overall": accuracy(results),
        "dev": accuracy([r for r in results if r["split"] == "dev"]),
        "holdout": accuracy([r for r in results if r["split"] == "holdout"]),
        "per_field": per_field,
        "skills_precision": precision,
        "skills_recall": recall,
        "skills_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "salary_hallucination": len(hallucinated) / len(null_salary) if null_salary else 0.0,
        "dead_letters": sum(1 for r in results if r["predicted"] is None),
        "avg_input_tokens": tokens_in / n,
        "avg_output_tokens": tokens_out / n,
        "p50_seconds": sorted(r["seconds"] for r in results)[n // 2],
        "cost_per_1k_usd": cost_1k,
    }
    summary["gate_failures"] = gate_failures(summary)
    return summary


def gate_failures(s: dict) -> list[str]:
    failures = []
    if s["overall"] < GATE["overall"]:
        failures.append(f"overall {s['overall']:.1%} < {GATE['overall']:.0%}")
    if s["holdout"] < GATE["holdout"]:
        failures.append(f"holdout {s['holdout']:.1%} < {GATE['holdout']:.0%}")
    failures += [f"{f} {v:.0%} < {GATE['field']:.0%}" for f, v in s["per_field"].items() if v < GATE["field"]]
    if s["salary_hallucination"] > GATE["salary_hallucination"]:
        failures.append(f"salary hallucination {s['salary_hallucination']:.1%} > {GATE['salary_hallucination']:.0%}")
    if s["dead_letters"] > GATE["dead_letters"]:
        failures.append(f"{s['dead_letters']} invalid after retry")
    return failures


def evaluate(extractor: Extractor, labelled: list[dict], texts: dict, workers: int = 4) -> list[dict]:
    def one(row):
        posting = {**texts[(row["refnr"], row["content_hash"])], "refnr": row["refnr"],
                   "content_hash": row["content_hash"]}  # fmt: skip
        start = time.monotonic()
        x = extractor.extract(posting)
        predicted = canonical(x.fields) if x.ok else None
        expected = canonical(row["labels"])
        return {
            "refnr": row["refnr"], "split": row["split"], "predicted": predicted, "expected": expected,
            "judged": judge(predicted, expected), "problems": x.problems, "last_input": x.last_input,
            "input_tokens": x.input_tokens, "output_tokens": x.output_tokens, "seconds": time.monotonic() - start,
        }  # fmt: skip

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, labelled))


def pct(v):
    return "–" if v is None else f"{v:.0%}"


def report(summaries: list[dict], errors_by_config: dict[str, str], when: str) -> str:
    lines = [f"## Extraction evaluation {when}", ""]
    ok = summaries
    if ok:
        lines += [
            "| Config | Overall | Dev | Holdout | Skills F1 | Salary halluc. | Invalid | Tokens in/out | p50 s "
            "| $ / 1k | Gate |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in ok:
            cost = "–" if s["cost_per_1k_usd"] is None else f"{s['cost_per_1k_usd']:.2f}"
            gate = "PASS" if not s["gate_failures"] else "FAIL"
            lines.append(
                f"| `{s['config']}` | {pct(s['overall'])} | {pct(s['dev'])} | {pct(s['holdout'])} | "
                f"{s['skills_f1']:.2f} | {pct(s['salary_hallucination'])} | {s['dead_letters']} | "
                f"{s['avg_input_tokens']:.0f} / {s['avg_output_tokens']:.0f} | {s['p50_seconds']:.1f} | "
                f"{cost} | {gate} |"
            )
        lines += ["", "| Field | " + " | ".join(f"`{s['config']}`" for s in ok) + " |",
                  "|---|" + "---|" * len(ok)]  # fmt: skip
        for f in FIELDS:
            lines.append(f"| {f} | " + " | ".join(pct(s["per_field"][f]) for s in ok) + " |")
        lines.append("")
        for s in ok:
            if s["gate_failures"]:
                lines.append(f"- `{s['config']}` gate: " + "; ".join(s["gate_failures"]))
    for config, err in errors_by_config.items():
        lines.append(f"- `{config}` not run: {err}")
    return "\n".join(lines) + "\n"


def parse_config(config: str) -> tuple[str, str]:
    model_id, _, prompt = config.rpartition("@")
    if not model_id or not prompt:
        raise ValueError(f"config must look like MODEL_ID@PROMPT_VERSION, got {config!r}")
    return model_id, prompt


def fetch_texts(athena: Athena, labelled: list[dict]) -> dict:
    refnrs = ", ".join("'" + r["refnr"].replace("'", "''") + "'" for r in labelled)
    rows = athena.rows(TEXT_SQL.format(db=DB_CURATED, refnrs=refnrs), "labelled texts")
    return {(r["refnr"], r["content_hash"]): r for r in rows}


# ---------- main ----------


def main(argv: list[str] | None = None, athena: Athena | None = None, bedrock=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["sample", "freeze", "score"])
    parser.add_argument("--config", action="append", help="MODEL_ID@PROMPT_VERSION (repeatable)")
    parser.add_argument("--force", action="store_true", help="sample: overwrite an existing labelling file")
    args = parser.parse_args(argv)

    if args.command == "freeze":
        yaml, _ = _yaml()
        rows, problems = freeze(yaml.safe_load(LABELLING.read_text()))
        for p in problems:
            print("  ✗", p)
        if problems:
            print(f"{len(problems)} problem(s) – fix them in {LABELLING.relative_to(ROOT)} and run freeze again.")
            return 1
        LABELLED_SET.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        print(f"Wrote {len(rows)} labelled postings to {LABELLED_SET.relative_to(ROOT)}")
        return 0

    if athena is None:
        import boto3

        athena = Athena(boto3.client("athena", region_name="eu-central-1"), log=lambda m: None)

    if args.command == "sample":
        if LABELLING.exists() and not args.force:
            print(f"{LABELLING.relative_to(ROOT)} exists – it may contain your labels. Use --force to overwrite.")
            return 1
        sample = stratified_sample(athena.rows(CANDIDATES_SQL.format(db=DB_CURATED), "candidates"))
        write_labelling(labelling_items(sample))
        counts: dict[str, int] = {}
        for c in sample:
            for t in tags(c):
                counts[t] = counts.get(t, 0) + 1
        print(f"Wrote {len(sample)} postings to {LABELLING.relative_to(ROOT)}  strata: {dict(sorted(counts.items()))}")
        return 0

    labelled = [json.loads(line) for line in LABELLED_SET.read_text().splitlines() if line.strip()]
    texts = fetch_texts(athena, labelled)
    missing = [r["refnr"] for r in labelled if (r["refnr"], r["content_hash"]) not in texts]
    if missing:
        print(f"Posting versions not found in curated.postings: {missing}")
        return 1
    if bedrock is None:
        from enrich.handler import bedrock_client

        bedrock = bedrock_client()

    summaries, failed, details = [], {}, {}
    for config in args.config or DEFAULT_CONFIGS:
        model_id, prompt = parse_config(config)
        print(f"Scoring {config} on {len(labelled)} postings …")
        try:
            results = evaluate(Extractor(bedrock, model_id, prompt), labelled, texts)
        except Exception as exc:  # noqa: BLE001 – e.g. model not enabled; report and continue with the next config
            failed[config] = f"{type(exc).__name__}: {exc}"
            continue
        summaries.append(summarise(config, model_id, results))
        details[config] = results

    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    text = report(summaries, failed, when)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / f"report-{stamp}.md").write_text(text)
    (WORK / f"results-{stamp}.json").write_text(json.dumps({"summaries": summaries, "details": details},
                                                           ensure_ascii=False, indent=1, default=str))  # fmt: skip
    print(text)
    print(f"Saved eval/work/report-{stamp}.md and results-{stamp}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
