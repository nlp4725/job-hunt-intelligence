"""
Screening Agent — Stage 1 screen, the fast/cheap pass over the whole
corpus, deliberately excluding Company research (too slow/expensive to run
on every job; see CONTEXT.md "Job Judge"). Combines three
independently-scored signals:

  - skill_score:      deterministic, analysis/skill_match.py — no LLM call
  - seniority_score:  judge/seniority_fit.py — DeepSeek V4 Pro, thinking off
  - expertise_score:  judge/expertise_match.py — DeepSeek V4 Pro, thinking off

This is a plain orchestrator function, not an agent — nothing here decides
what to do next or picks tools; it's three independent calls (one local
computation, two fixed-prompt LLM calls) combined in code. seniority_fit
and expertise_match run concurrently since they're independent of each
other, to cut wall-clock latency to roughly the slower of the two instead
of their sum. No blended total score — the three dimensions are persisted
and surfaced separately (db.models.ScreeningResult) rather than forced
into one number.

Deliberately NOT combined into a single LLM call: tests_and_eval/
test_seniority_n_expertise showed combining seniority_fit + expertise_match
into one call (nested or flat schema) measurably hurts accuracy on both
labels versus scoring them separately — see that eval's results.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from analysis.skill_match import skill_match_score
from db.models import Job, Resume, ScreeningResult
from judge.expertise_match import ExpertiseMatch, format_posting as format_posting_expertise, score_expertise_match
from judge.seniority_fit import SeniorityFit, format_posting as format_posting_seniority, score_seniority_fit


def screen_job(job: Job, resume: Resume, session) -> ScreeningResult:
    skill = skill_match_score(resume.content, job.raw_text)

    with ThreadPoolExecutor(max_workers=2) as executor:
        seniority_future = executor.submit(score_seniority_fit, format_posting_seniority(job))
        expertise_future = executor.submit(score_expertise_match, format_posting_expertise(job))
        seniority: SeniorityFit = seniority_future.result()
        expertise: ExpertiseMatch = expertise_future.result()

    result = job.screening_result or ScreeningResult(job_id=job.id)

    result.skill_score = skill["score"]
    result.skill_ratio = skill["ratio"]
    result.skill_matched = skill["matched_skills"]
    result.skill_group_matched = skill["group_matched_skills"]
    result.skill_missing = skill["missing_skills"]

    result.seniority_score = seniority.score
    result.seniority_evidence = seniority.evidence
    result.seniority_years_required = seniority.years_required
    result.seniority_inferred = seniority.inferred
    result.seniority_confidence = seniority.confidence
    result.seniority_note = seniority.note

    result.expertise_score = expertise.score
    result.expertise_evidence = expertise.evidence
    result.expertise_matched_domains = expertise.matched_domains
    result.expertise_matched_capabilities = expertise.matched_capabilities
    result.expertise_matched_weaknesses = expertise.matched_weaknesses
    result.expertise_confidence = expertise.confidence
    result.expertise_note = expertise.note

    result.total_score = result.skill_score + result.seniority_score + result.expertise_score
    result.screened_at = datetime.now(timezone.utc)

    session.add(result)
    session.commit()
    return result


if __name__ == "__main__":
    from db.session import get_session

    session = get_session()
    try:
        resume = session.query(Resume).first()
        job = session.get(Job, 188)
        result = screen_job(job, resume, session)
        print(f"skill={result.skill_score} seniority={result.seniority_score} expertise={result.expertise_score}")
    finally:
        session.close()
