"""After a taxonomy PR is MERGED: bring the job_skills table in line with the
current taxonomy.

db/job_writer.py only ever adds skill rows at capture time, so without this a
removed skill or a tightened pattern leaves stale tags behind forever, and a
new skill never appears on jobs captured before it existed.

Dry run by default — prints what would change per skill. --apply writes.
Covers every job with JD text (duplicates included), matching what
job_writer tags at capture.

    ./venv/bin/python .claude/skills/taxonomy-refresh/reextract_job_skills.py
    ./venv/bin/python .claude/skills/taxonomy-refresh/reextract_job_skills.py --apply
"""

import argparse
from collections import Counter, defaultdict

from _common import load_corpus, tag_texts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()

    from db.models import JobSkill
    from db.session import SessionLocal

    corpus = load_corpus(include_all=True)
    tagged = tag_texts([r.raw_text for r in corpus])

    session = SessionLocal()
    try:
        current = defaultdict(set)
        for job_id, skill in session.query(JobSkill.job_id, JobSkill.skill_name).all():
            current[job_id].add(skill)

        adds, removes = [], []
        for row, row_tags in zip(corpus, tagged):
            wanted = set(row_tags)
            adds += [(row.id, s) for s in wanted - current[row.id]]
            removes += [(row.id, s) for s in current[row.id] - wanted]

        added_by_skill = Counter(s for _, s in adds)
        removed_by_skill = Counter(s for _, s in removes)
        print(f"{len(corpus)} jobs · +{len(adds)} rows · -{len(removes)} rows")
        for skill in sorted(set(added_by_skill) | set(removed_by_skill), key=lambda s: -(added_by_skill[s] + removed_by_skill[s])):
            print(f"  {skill:32s} +{added_by_skill[skill]:<6d} -{removed_by_skill[skill]}")

        if not args.apply:
            print("\ndry run — nothing written. Re-run with --apply after the taxonomy change is merged.")
            return

        for job_id, skill in removes:
            session.query(JobSkill).filter(JobSkill.job_id == job_id, JobSkill.skill_name == skill).delete(synchronize_session=False)
        session.add_all(JobSkill(job_id=job_id, skill_name=skill) for job_id, skill in adds)
        session.commit()
        print("\napplied.")
        print("Skill Match scores in screening_results are NOT recomputed by this script.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
