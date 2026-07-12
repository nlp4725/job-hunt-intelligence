"""
Deterministic skill-match score: how much of a job's mentioned skills the
candidate's resume already covers, banded onto the SAME 0-5 scale as
Seniority Fit and Expertise Match (see CONTEXT.md "Job Judge") so all three
Judge dimensions combine on one common scale without needing separate unit
conversions. No LLM call — pure set overlap over
analysis/skills_extractor.py's existing taxonomy.
"""

from analysis.skills_extractor import SKILL_GROUPS, extract_skills, skill_group_of


def skill_match_score(resume_text: str, job_text: str) -> dict:
    """Every 20% of the job's mentioned skills the resume also covers is
    worth 1 point (floor, not round-to-nearest — matches the other two
    rubrics' banded-threshold style rather than smooth rounding). A JD that
    mentions no taxonomy skill at all scores 0, not excluded — unlike the
    Company Research Missing-Dimension Rule (ADR 0001), this is a fast
    first-pass screen, not a scored-and-averaged rubric dimension, so
    treating "nothing to match" as the worst case is the right bias here.

    A JD skill not directly on the resume can still count as matched if the
    resume has a DIFFERENT skill from the same SKILL_GROUPS group (e.g. JD
    wants GCP, resume has AWS) — binary credit, not fractional: group
    membership either counts or it doesn't. No fractional weight (e.g. 0.5)
    is used here since no such number is better-justified than any other
    without real calibration data (see CONTEXT.md discussion). group_matched
    is reported separately from matched_skills so it stays visible which
    skills were a direct hit vs. a group substitution."""
    job_skills = set(extract_skills(job_text))
    resume_skills = set(extract_skills(resume_text))

    if not job_skills:
        return {
            "criterion": "skill_match",
            "matched_skills": [],
            "group_matched_skills": [],
            "missing_skills": [],
            "ratio": 0.0,
            "score": 0,
            "note": "JD doesn't mention any skill from the taxonomy",
        }

    direct_matched = job_skills & resume_skills
    remaining = job_skills - direct_matched

    group_matched = set()
    for skill in remaining:
        group = skill_group_of(skill)
        if group and any(sibling in resume_skills for sibling in SKILL_GROUPS[group] if sibling != skill):
            group_matched.add(skill)

    missing = remaining - group_matched
    total_matched = direct_matched | group_matched
    score = min(5, (len(total_matched) * 5) // len(job_skills))

    return {
        "criterion": "skill_match",
        "matched_skills": sorted(direct_matched),
        "group_matched_skills": sorted(group_matched),
        "missing_skills": sorted(missing),
        "ratio": len(total_matched) / len(job_skills),
        "score": score,
        "note": None,
    }


if __name__ == "__main__":
    import json

    from db.models import Job, Resume
    from db.session import get_session

    session = get_session()
    try:
        resume = session.query(Resume).first()
        job = session.get(Job, 10)
        print(json.dumps(skill_match_score(resume.content, job.raw_text), indent=2))
    finally:
        session.close()
