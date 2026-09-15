"""Compare an LLM extraction against the taxonomy. Three lists come out:

1. missing       — skills the LLM found that no taxonomy entry covers (vocabulary gaps)
2. pattern_gaps  — the taxonomy HAS the skill, but its patterns didn't fire on the JD
                   (often the LLM inferring loosely — check the evidence before acting)
3. unconfirmed   — taxonomy tags the LLM did not list (false-match or blurb-mention candidates),
                   with the text the pattern actually fired on

Each is ranked by the number of distinct JDs, not raw mentions. Names already
rejected in decisions.json are left out of `missing`.

    ./venv/bin/python .claude/skills/taxonomy-refresh/diff_vocab.py --extraction runs/<date>/extraction.json
"""

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from _common import load_decisions, load_taxonomy_module, match_context, norm_name, read_json, rejected_names, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--extraction", required=True)
    parser.add_argument("--taxonomy-ref", help="git ref to compare against (default: working tree)")
    parser.add_argument("--min-jds", type=int, default=2, help="hide missing skills seen in fewer JDs (default 2)")
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--out", help="default: diff.json next to the extraction")
    args = parser.parse_args()

    extraction = read_json(args.extraction)
    sample = read_json(extraction["sample_path"])
    raw_by_id = {j["id"]: j["raw_text"] for j in sample["jobs"]}
    tax = load_taxonomy_module(args.taxonomy_ref)
    rejected = rejected_names(load_decisions())

    total = covered = 0
    missing = defaultdict(lambda: {"jds": set(), "tracks": Counter(), "kinds": Counter(), "examples": []})
    gaps = defaultdict(lambda: {"jds": set(), "examples": []})
    unconfirmed = defaultdict(lambda: {"jds": set(), "examples": []})

    for job in extraction["jobs"]:
        raw = raw_by_id.get(job["id"])
        if raw is None:
            continue
        tags = set(tax.extract_skills(raw))
        confirmed = set()
        for skill in job["skills"]:
            if skill["kind"] == "credential":
                continue
            total += 1
            hit = set(tax.extract_skills(f"{skill['name']} {skill['evidence']}")) & tags
            if hit:
                covered += 1
                confirmed |= hit
                continue
            known = set(tax.extract_skills(skill["name"])) | set(tax.extract_skills(skill["evidence"]))
            if known:
                for name in known - tags:
                    gaps[name]["jds"].add(job["id"])
                    if len(gaps[name]["examples"]) < 3:
                        gaps[name]["examples"].append(skill["evidence"])
                continue
            key = norm_name(skill["name"])
            if key in rejected:
                continue
            entry = missing[key]
            entry["jds"].add(job["id"])
            entry["tracks"][job["track"]] += 1
            entry["kinds"][skill["kind"]] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append(skill["evidence"])
        for name in tags - confirmed:
            entry = unconfirmed[name]
            entry["jds"].add(job["id"])
            if len(entry["examples"]) < 3:
                m = tax._COMPILED_PATTERNS[name].search(raw)
                entry["examples"].append(match_context(raw, m.start(), m.end()) if m else "")

    def ranked(table, min_jds=1):
        rows = [dict(name=k, n_jds=len(v["jds"]), **{f: (dict(v[f]) if isinstance(v[f], Counter) else v[f])
                                                      for f in v if f != "jds"})
                for k, v in table.items() if len(v["jds"]) >= min_jds]
        return sorted(rows, key=lambda r: -r["n_jds"])

    report = {
        "jobs": len(extraction["jobs"]),
        "llm_skills_excluding_credentials": total,
        "covered": covered,
        "coverage": round(covered / total, 3) if total else None,
        "missing": ranked(missing, args.min_jds),
        "missing_long_tail_names": sum(1 for v in missing.values() if len(v["jds"]) < args.min_jds),
        "pattern_gaps": ranked(gaps),
        "unconfirmed_tags": ranked(unconfirmed),
    }
    out = args.out or Path(args.extraction).with_name("diff.json")
    write_json(out, report)

    print(f"{report['jobs']} JDs · LLM skills (excl. credentials) {total} · covered {covered} ({report['coverage']:.0%})")
    print(f"\nMISSING from taxonomy (≥{args.min_jds} JDs; {report['missing_long_tail_names']} rarer names hidden):")
    for r in report["missing"][: args.top]:
        print(f"  {r['n_jds']:3d} JDs  {r['name']:32s} {r['kinds']}  e.g. \"{r['examples'][0]}\"")
    print("\nPATTERN GAPS (skill exists, patterns didn't fire — verify the evidence):")
    for r in report["pattern_gaps"][:25]:
        print(f"  {r['n_jds']:3d} JDs  {r['name']:28s} e.g. \"{r['examples'][0]}\"")
    print("\nUNCONFIRMED TAGS (tagged by taxonomy, not listed by LLM — false-match candidates):")
    for r in report["unconfirmed_tags"][:25]:
        print(f"  {r['n_jds']:3d} JDs  {r['name']:28s} fired on {r['examples'][0]}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
