"""How much a taxonomy change moves Skill Match scores, before anyone merges it.

Scores every screened job against its track's resume twice — once with the
taxonomy at --base-ref (default HEAD), once with the working tree — using the
real analysis/skill_match.skill_match_score, so the formula can't drift from
production. Nothing is written to the database.

Fast path: one corpus-wide tagging pass with the base taxonomy (all CPU
cores); the working-tree tags are derived from it by re-running only the
skills whose patterns changed, were added, or were removed.

    ./venv/bin/python .claude/skills/taxonomy-refresh/score_churn.py
    ./venv/bin/python .claude/skills/taxonomy-refresh/score_churn.py --base-ref main --track ml_ai
"""

import argparse
from collections import Counter
from contextlib import contextmanager

from _common import load_taxonomy_module, tag_texts, today_run_dir, write_json

import analysis.skill_match as skill_match  # noqa: E402  (sys.path set by _common)


@contextmanager
def taxonomy(tags_by_text: dict[str, list[str]], module):
    """Point skill_match at precomputed tags and a module's groups for the duration."""
    saved = (skill_match.extract_skills, skill_match.SKILL_GROUPS, skill_match.skill_group_of)
    skill_match.extract_skills = lambda text: tags_by_text[text]
    skill_match.SKILL_GROUPS = module.SKILL_GROUPS
    skill_match.skill_group_of = module.skill_group_of
    try:
        yield
    finally:
        skill_match.extract_skills, skill_match.SKILL_GROUPS, skill_match.skill_group_of = saved


def changed_skills(base, current) -> set[str]:
    names = set(base.SKILL_TAXONOMY) | set(current.SKILL_TAXONOMY)
    return {n for n in names if base.SKILL_TAXONOMY.get(n) != current.SKILL_TAXONOMY.get(n)}


def load_jobs_and_resumes(track):
    from db.models import Job, Resume, ScreeningResult
    from db.session import SessionLocal

    session = SessionLocal()
    try:
        resumes = {r.track: r.content for r in session.query(Resume).all()}
        query = (session.query(Job.id, Job.track, Job.title, Job.raw_text)
                 .join(ScreeningResult, ScreeningResult.job_id == Job.id)
                 .filter(Job.raw_text.isnot(None)))
        if track:
            query = query.filter(Job.track == track)
        return query.all(), resumes
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--track", choices=["ml_ai", "pm"])
    parser.add_argument("--out", help="default: runs/<today>/churn.json")
    args = parser.parse_args()

    jobs, resumes = load_jobs_and_resumes(args.track)
    base, current = load_taxonomy_module(args.base_ref), load_taxonomy_module()
    changed = changed_skills(base, current)
    groups_changed = base.SKILL_GROUPS != current.SKILL_GROUPS

    texts = list({j.raw_text for j in jobs} | set(resumes.values()))
    base_tags = dict(zip(texts, tag_texts(texts, ref=args.base_ref)))
    order = {name: i for i, name in enumerate(current.SKILL_TAXONOMY)}
    current_tags = {}
    for text, tags in base_tags.items():
        kept = [s for s in tags if s not in changed]
        added = [s for s in changed if s in current._COMPILED_PATTERNS and current._COMPILED_PATTERNS[s].search(text)]
        current_tags[text] = sorted(kept + added, key=lambda s: order.get(s, len(order)))

    def score_all(tags_by_text, module):
        with taxonomy(tags_by_text, module):
            out = {}
            for job in jobs:
                resume = resumes.get(job.track) or resumes.get(None)
                if resume:
                    out[job.id] = skill_match.skill_match_score(resume, job.raw_text)
            return out

    if not changed and not groups_changed:
        print(f"taxonomy identical to {args.base_ref} — no churn possible")
    before = score_all(base_tags, base)
    after = score_all(current_tags, current)

    def job_skill_set(r):
        return set(r["matched_skills"] + r["group_matched_skills"] + r["missing_skills"])

    per_track, biggest = {}, []
    titles = {j.id: j.title for j in jobs}
    for track in sorted({j.track for j in jobs}):
        ids = [j.id for j in jobs if j.track == track and j.id in before]
        deltas = [after[i]["score"] - before[i]["score"] for i in ids]
        per_track[track] = {
            "jobs": len(ids),
            "pct_changed": round(100 * sum(1 for d in deltas if d) / len(ids), 2) if ids else 0,
            "mean_delta": round(sum(deltas) / len(ids), 3) if ids else 0,
            "delta_histogram": dict(sorted(Counter(deltas).items())),
        }
    for job_id in before:
        delta = after[job_id]["score"] - before[job_id]["score"]
        if delta:
            biggest.append({
                "job_id": job_id, "title": titles[job_id], "delta": delta,
                "score": f"{before[job_id]['score']} → {after[job_id]['score']}",
                "jd_skills_added": sorted(job_skill_set(after[job_id]) - job_skill_set(before[job_id])),
                "jd_skills_removed": sorted(job_skill_set(before[job_id]) - job_skill_set(after[job_id])),
            })
    biggest.sort(key=lambda r: -abs(r["delta"]))

    out = args.out or today_run_dir() / "churn.json"
    write_json(out, {"base_ref": args.base_ref, "changed_skills": sorted(changed), "groups_changed": groups_changed,
                     "per_track": per_track, "biggest_changes": biggest[:50]})

    print(f"base {args.base_ref} → working tree · changed skills: {sorted(changed) or 'none'} · groups changed: {groups_changed}")
    for track, s in per_track.items():
        print(f"  {track}: {s['jobs']} jobs · {s['pct_changed']}% changed · mean Δ {s['mean_delta']:+} · Δ histogram {s['delta_histogram']}")
    for r in biggest[:10]:
        print(f"    job {r['job_id']} {r['score']}  +{r['jd_skills_added']} -{r['jd_skills_removed']}  {r['title']}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
