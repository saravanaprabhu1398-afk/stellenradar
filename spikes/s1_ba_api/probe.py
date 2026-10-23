"""Spike S1: probe the Bundesagentur für Arbeit job search API.

Answers: which search/detail endpoints work, pagination behaviour,
whether details contain the full description, and rough volumes.
Raw responses go to ./out/ (git-ignored) – they may contain personal data.
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
HEADERS = {
    "X-API-Key": "jobboerse-jobsuche",
    "User-Agent": "stellenradar-spike/0.1 (+https://github.com/saravanaprabhu1398-afk/stellenradar)",
    "Accept": "application/json",
}
TERMS = ["Data Engineer", "Dateningenieur", "Analytics Engineer", "ETL Entwickler", "Big Data Engineer"]
THROTTLE_S = 1.1
OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)
_last_call = 0.0


def get(path: str, params: dict | None = None) -> tuple[int, dict | None, float]:
    global _last_call
    wait = THROTTLE_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers=HEADERS)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, body = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        status, body = e.code, None
    except Exception as e:  # network, JSON
        print(f"  ! {path}: {type(e).__name__}: {e}")
        status, body = -1, None
    _last_call = time.monotonic()
    return status, body, round(_last_call - started, 2)


def postings_list(body: dict) -> list:
    """Return the first list of dicts in the response (field name differs by version)."""
    for key, value in body.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


def ref_of(posting: dict) -> str | None:
    return posting.get("refnr") or posting.get("referenznummer")


summary: dict = {"search": {}, "pagination": {}, "detail": {}, "volume": {}}

# 1) Which search endpoint works?
print("1) Search endpoints")
working_search = None
for path in ["/pc/v4/jobs", "/pc/v6/jobs", "/pc/v4/app/jobs"]:
    status, body, secs = get(path, {"was": "Data Engineer", "angebotsart": 1, "size": 25, "page": 1})
    items = postings_list(body) if body else []
    info = {
        "status": status,
        "seconds": secs,
        "top_level_keys": sorted(body.keys()) if body else None,
        "items_returned": len(items),
        "total_field": {k: body[k] for k in body if "max" in k.lower() or "total" in k.lower()} if body else None,
        "item_keys": sorted(items[0].keys()) if items else None,
    }
    summary["search"][path] = info
    print(f"  {path}: HTTP {status}, {len(items)} items, total={info['total_field']}")
    if body:
        (OUT / f"search_{path.strip('/').replace('/', '_')}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2))
    if items and working_search is None:
        working_search = path

if not working_search:
    print("No search endpoint worked – stop here and paste the output.")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    sys.exit(1)

# 2) Pagination with size=100
print(f"2) Pagination on {working_search}")
refnrs: list[str] = []
for page in (1, 2, 3):
    status, body, secs = get(working_search, {"was": "Data Engineer", "angebotsart": 1, "size": 100, "page": page})
    items = postings_list(body) if body else []
    refnrs += [r for r in (ref_of(i) for i in items) if r]
    summary["pagination"][f"page_{page}"] = {"status": status, "items": len(items), "seconds": secs}
    print(f"  page {page}: HTTP {status}, {len(items)} items")
summary["pagination"]["unique_refnrs"] = len(set(refnrs))
summary["pagination"]["duplicates_across_pages"] = len(refnrs) - len(set(refnrs))

# 3) Detail endpoints for 3 postings
print("3) Detail endpoints")
for refnr in list(dict.fromkeys(refnrs))[:3]:
    encoded = base64.b64encode(refnr.encode()).decode()
    for path in ["/pc/v4/jobdetails/", "/pc/v3/jobdetails/"]:
        status, body, secs = get(path + encoded)
        desc = (body or {}).get("stellenangebotsBeschreibung") or ""
        info = {
            "status": status,
            "seconds": secs,
            "keys": sorted(body.keys()) if body else None,
            "description_chars": len(desc),
            "has_external_url": bool((body or {}).get("externeUrl")),
            "mentions_salary_word": any(w in desc.lower() for w in ("gehalt", "salary", "€", "eur")),
        }
        summary["detail"].setdefault(refnr, {})[path] = info
        print(f"  {refnr} {path}: HTTP {status}, description {len(desc)} chars")
        if body:
            (OUT / f"detail_{path.strip('/').replace('/', '_')}_{refnr}.json").write_text(json.dumps(body, ensure_ascii=False, indent=2))
        if status == 200 and desc:
            break  # no need to try the older version for this posting

# 4) Volume per search term
print("4) Volume per term")
for term in TERMS:
    status, body, _ = get(working_search, {"was": term, "angebotsart": 1, "size": 1, "page": 1})
    total = {k: body[k] for k in body if "max" in k.lower() or "total" in k.lower()} if body else None
    summary["volume"][term] = {"status": status, "total": total}
    print(f"  {term}: HTTP {status}, total={total}")

(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(f"\nDone. Summary written to {OUT / 'summary.json'}")
