#!/usr/bin/env python
"""Classify __list2() output. Reads 'idx|Title ~ Company ~ Location' lines on stdin.
Applies ONLY the two standing filters. Everything else must be clicked.
Usage:  ./venv/bin/python .claude/skills/linkedin-manual-screen/classify.py <<'EOF'
        0|AI Engineer ~ Acme ~ United States (Remote)
        EOF
"""
import sys, os, re
sys.path.insert(0, os.getcwd())
from judge.agency_blocklist import _matches_agency_substring
from db.session import SessionLocal
from db.models import Company

AGENCIES_CACHE = os.environ.get("AGENCIES_CACHE", "/tmp/agencies.txt")
s = SessionLocal()
if os.path.exists(AGENCIES_CACHE):
    agencies = {n.strip() for n in open(AGENCIES_CACHE) if n.strip()}
else:
    agencies = {c.name for c in s.query(Company).filter(Company.industry == "Staffing and Recruiting")}

clicks, skipped = [], []
for line in sys.stdin.read().strip().split("\n"):
    if not line.strip():
        continue
    idx, rest = line.split("|", 1)
    parts = [p.strip() for p in rest.split("~")]
    title = parts[0]
    co = parts[1] if len(parts) > 1 else "?"
    loc = parts[2] if len(parts) > 2 else ""
    r = []
    if _matches_agency_substring(co) or co in agencies:
        r.append("AGENCY")
    if re.search(r"\b(staff|principal)\b", title, re.I):
        r.append("TITLE")
    # On-site is deliberately NOT a skip (2026-09-08). Searches no longer pin
    # f_WT=2, so on-site cards are now a wanted result rather than a leak.
    (skipped if r else clicks).append((idx.strip(), title, co, ",".join(r)))

for i, t, c, r in skipped:
    print(f"SKIP  {i:>2}  {c[:26]:<26} {t[:42]:<42} [{r}]")
print(f"\n== CLICK ALL {len(clicks)} | skipped {len(skipped)} | total {len(clicks)+len(skipped)} ==")
for i, t, c, _ in clicks:
    print(f"  {i:>2}\t{t[:48]:<48}\t{c[:26]}")
