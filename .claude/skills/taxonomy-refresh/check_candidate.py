"""Measure proposed taxonomy patterns against the WHOLE corpus before they go in.

Candidates file (JSON list):
    [{"name": "Distributed Systems",
      "patterns": ["distributed systems?"],
      "should_match": ["experience with distributed systems"],
      "should_not_match": ["we distributed systems of record to partners"]}]

If `name` is already a taxonomy skill, `patterns` are treated as ADDITIONS to
it and the report shows how many JDs the new patterns gain.

Reported per candidate:
  - JDs matched, overall and as % of each track
  - share of matches sitting inside a larger word (plural "s" excluded) — high
    values mean a missing boundary OR glued capture text; read the samples
  - existing skills that co-fire on ≥80% of the candidate's JDs (likely synonym/duplicate)
  - random sampled match contexts to eyeball for false matches
  - should_match / should_not_match failures

    ./venv/bin/python .claude/skills/taxonomy-refresh/check_candidate.py --candidates runs/<date>/candidates.json
    ./venv/bin/python .claude/skills/taxonomy-refresh/check_candidate.py --name "Neo4j" --pattern "neo4j"
"""

import argparse
import random
import re
from collections import Counter
from pathlib import Path

from _common import (CACHE_DIR, load_corpus, load_taxonomy_module, match_context, read_json, tag_texts,
                     taxonomy_fingerprint, write_json)


def corpus_tags(corpus) -> dict[int, set[str]]:
    """Current-taxonomy tags per JD, cached per taxonomy version so repeated
    checks during one run skip the corpus-wide pass."""
    cache = CACHE_DIR / f"tags_{taxonomy_fingerprint()}.json"
    if cache.exists():
        return {int(k): set(v) for k, v in read_json(cache).items()}
    tags = {r.id: set(t) for r, t in zip(corpus, tag_texts([r.raw_text for r in corpus]))}
    write_json(cache, {k: sorted(v) for k, v in tags.items()})
    return tags


def inside_word(text: str, m: re.Match) -> bool:
    before = text[m.start() - 1] if m.start() > 0 else " "
    tail = text[m.end():m.end() + 2]
    after = tail[:1] or " "
    plural = (after.lower() == "s" and not tail[1:2].isalnum()) or tail.startswith("'s")
    return before.isalnum() or (after.isalnum() and not plural)


def check(candidate: dict, corpus, tags, tax, samples: int, rng: random.Random) -> dict:
    name = candidate["name"]
    pattern = re.compile("(?:" + "|".join(candidate["patterns"]) + ")", re.IGNORECASE)
    existing = name in tax.SKILL_TAXONOMY

    track_sizes = Counter(r.track for r in corpus)
    matched, by_track, contexts = [], Counter(), []
    n_matches = n_inside = 0
    for row in corpus:
        first = None
        for m in pattern.finditer(row.raw_text):
            n_matches += 1
            n_inside += inside_word(row.raw_text, m)
            first = first or m
        if first:
            matched.append(row.id)
            by_track[row.track] += 1
            contexts.append((row.id, match_context(row.raw_text, first.start(), first.end())))

    co_fire = Counter(skill for jid in matched for skill in tags.get(jid, ()) if skill != name)
    likely_duplicates = {s: round(c / len(matched), 2) for s, c in co_fire.most_common() if matched and c / len(matched) >= 0.8}

    failures = [f"should match: {t!r}" for t in candidate.get("should_match", []) if not pattern.search(t)]
    failures += [f"should NOT match: {t!r}" for t in candidate.get("should_not_match", []) if pattern.search(t)]

    report = {
        "name": name,
        "existing_skill": existing,
        "patterns": candidate["patterns"],
        "jds_matched": len(matched),
        "by_track": {t: {"jds": by_track[t], "pct_of_track": round(100 * by_track[t] / track_sizes[t], 2)} for t in track_sizes},
        "inside_word_share": round(n_inside / n_matches, 3) if n_matches else None,
        "likely_duplicates": likely_duplicates,
        "example_failures": failures,
        "samples": [{"job_id": j, "context": c} for j, c in rng.sample(contexts, min(samples, len(contexts)))],
    }
    if existing:
        tagged = {jid for jid, t in tags.items() if name in t}
        gained = set(matched) - tagged
        report["jds_gained_over_current_patterns"] = len(gained)
        if candidate.get("replace"):
            # A fix: these patterns REPLACE the skill's current ones. Show what
            # stops matching, with the text the current pattern fired on, so a
            # fix that throws away real mentions is visible.
            lost = sorted(tagged - set(matched))
            raw = {r.id: r.raw_text for r in corpus}
            current = tax._COMPILED_PATTERNS[name]
            report["jds_lost"] = len(lost)
            report["lost_samples"] = []
            for jid in rng.sample(lost, min(samples, len(lost))):
                m = current.search(raw[jid])
                report["lost_samples"].append({"job_id": jid, "context": match_context(raw[jid], m.start(), m.end()) if m else ""})
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", help="JSON list of candidates")
    parser.add_argument("--name", help="single candidate name")
    parser.add_argument("--pattern", action="append", help="single candidate pattern (repeatable)")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", help="default: <candidates>.checked.json, or runs/_adhoc_check.json")
    args = parser.parse_args()

    if args.candidates:
        candidates = read_json(args.candidates)
    elif args.name and args.pattern:
        candidates = [{"name": args.name, "patterns": args.pattern}]
    else:
        parser.error("give --candidates, or --name with at least one --pattern")

    tax = load_taxonomy_module()
    corpus = load_corpus()
    tags = corpus_tags(corpus)
    rng = random.Random(args.seed)
    reports = [check(c, corpus, tags, tax, args.samples, rng) for c in candidates]

    out = args.out or (Path(args.candidates).with_suffix(".checked.json") if args.candidates
                       else CACHE_DIR.parent / "_adhoc_check.json")
    write_json(out, reports)

    print(f"corpus: {len(corpus)} JDs (duplicates and off-track excluded)")
    for r in reports:
        tracks = ", ".join(f"{t} {v['jds']} ({v['pct_of_track']}%)" for t, v in r["by_track"].items())
        print(f"\n■ {r['name']}{' [existing — patterns added]' if r['existing_skill'] else ''}  {r['patterns']}")
        print(f"  JDs matched: {r['jds_matched']}  [{tracks}]")
        if r["existing_skill"]:
            print(f"  JDs gained over current patterns: {r['jds_gained_over_current_patterns']}")
        if "jds_lost" in r:
            print(f"  JDs LOST (replace): {r['jds_lost']}")
            for s in r["lost_samples"][:8]:
                print(f"    lost job {s['job_id']}: {s['context']}")
        print(f"  inside-word share: {r['inside_word_share']}")
        if r["likely_duplicates"]:
            print(f"  co-fires with (≥80% of its JDs): {r['likely_duplicates']}")
        for f in r["example_failures"]:
            print(f"  ✗ {f}")
        for s in r["samples"][:8]:
            print(f"    job {s['job_id']}: {s['context']}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
