"""Spike S1 analysis: reads ./out/ only – makes no API calls."""
import json
from collections import Counter
from pathlib import Path

OUT = Path(__file__).parent / "out"
summary = json.loads((OUT / "summary.json").read_text())

print("Pagination:", {k: v for k, v in summary["pagination"].items() if not k.startswith("page_")})
secs = [v["seconds"] for v in summary["search"].values() if v["status"] == 200]
secs += [v["seconds"] for v in summary["pagination"].values() if isinstance(v, dict)]
print(f"Search call latency: min {min(secs)} s, max {max(secs)} s")

search = json.loads((OUT / "search_pc_v6_jobs.json").read_text())
items = next(v for v in search.values() if isinstance(v, list))
print("\nv6 top-level keys:", sorted(search.keys()))
print("v6 posting keys:", sorted(items[0].keys()))
print("External URL share (first 25):", sum(1 for i in items if i.get("externeUrl")), "/", len(items))

print("\nFirst 25 titles for 'Data Engineer' (relevance check):")
for i in items:
    print("  -", i.get("titel") or i.get("stellenangebotsTitel") or i.get("beruf"))

print("\nOccupation ('beruf') distribution:")
for beruf, n in Counter(i.get("beruf") for i in items).most_common(10):
    print(f"  {n:>3}  {beruf}")

detail_file = next(OUT.glob("detail_pc_v4_jobdetails_*.json"))
detail = json.loads(detail_file.read_text())
print("\nDetail keys:", sorted(detail.keys()))