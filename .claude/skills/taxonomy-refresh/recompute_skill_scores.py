"""After a taxonomy PR is MERGED: recompute stored Skill Match results with the
current taxonomy.

screening_results holds skill_score / skill_ratio / skill_matched /
skill_group_matched / skill_missing as they were computed at screening time,
and total_score = skill + seniority + expertise (judge/stage1_screen.py). A
taxonomy change leaves all of those stale until this runs. Skill Match is
deterministic, so this is free — no LLM calls; Seniority and Expertise are
left untouched.

Dry run by default — prints how many stored scores would change. --apply writes.

    ./venv/bin/python .claude/skills/taxonomy-refresh/recompute_skill_scores.py
    ./venv/bin/python .claude/skills/taxonomy-refresh/recompute_skill_scores.py --apply
"""

import argparse
from collections import Counter

from _common import tag_texts

import analysis.skill_match as skill_match  # noqa: E402  (sys.path set by _common)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()

    from db.models import Job, Resume, ScreeningResult
    from db.session import SessionLocal

    session = SessionLocal()
    try:
        resumes = {r.track: r.content for r in session.query(Resume).all()}
        rows = (session.query(ScreeningResult, Job.track, Job.raw_text)
                .join(Job, Job.id == ScreeningResult.job_id)
                .filter(Job.raw_text.isnot(None))
                .all())

        texts = list({raw for _, _, raw in rows} | set(resumes.values()))
        tags_by_text = dict(zip(texts, tag_texts(texts)))
        skill_match.extract_skills = lambda text: tags_by_text[text]

        deltas, updates = Counter(), 0
        for result, track, raw in rows:
            resume = resumes.get(track) or resumes.get(None)
            if not resume:
                continue
            skill = skill_match.skill_match_score(resume, raw)
            new = (skill["score"], skill["matched_skills"], skill["group_matched_skills"], skill["missing_skills"])
            old = (result.skill_score, sorted(result.skill_matched or []), sorted(result.skill_group_matched or []),
                   sorted(result.skill_missing or []))
            if new == old:
                continue
            updates += 1
            deltas[skill["score"] - (result.skill_score or 0)] += 1
            if args.apply:
                result.skill_score = skill["score"]
                result.skill_ratio = skill["ratio"]
                result.skill_matched = skill["matched_skills"]
                result.skill_group_matched = skill["group_matched_skills"]
                result.skill_missing = skill["missing_skills"]
                if result.seniority_score is not None and result.expertise_score is not None:
                    result.total_score = result.skill_score + result.seniority_score + result.expertise_score

        print(f"{len(rows)} screening results · {updates} with changed skill fields · score Δ histogram {dict(sorted(deltas.items()))}")
        if args.apply:
            session.commit()
            print("applied.")
        else:
            print("dry run — nothing written. Re-run with --apply after the taxonomy change is merged.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
