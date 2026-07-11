"""
Judge Agent — one Claude call that scores a job against the candidate's
resume and career goals, using the company's research report for the
company_research dimension. See CONTEXT.md ("Judge Agent") and
docs/adr/0003 for why this is a single unified rubric call rather than
separate deterministic/LLM signals.

Orchestration: this module is the orchestrator. It checks Company.research_report
first and only invokes the Company Research Agent on a cache miss — Company
Research Agent has no independent trigger of its own.
"""

from datetime import datetime, timezone

import anthropic

from db.models import CareerGoals, Company, Job, JudgeResult, Resume
from judge.company_research_agent import get_or_create_report

MODEL = "claude-opus-4-8"

# Max points per rubric dimension (sums to the 300-point total). See
# CONTEXT.md "Job Judge" for what each dimension means.
RUBRIC_MAX = {
    "skill": 70,
    "seniority": 70,
    "domain_transferability": 30,
    "career_narrative": 60,
    "company_research": 70,
}
_MAX_WITHOUT_COMPANY_RESEARCH = sum(v for k, v in RUBRIC_MAX.items() if k != "company_research")

_SUBMIT_RUBRIC_TOOL = {
    "name": "submit_rubric",
    "description": (
        "Submit the completed job-fit rubric. Every *_score is an integer from 0 up to that "
        "dimension's max. company_research_score must be null if the company report has no "
        "usable data on any of Reputation/Stability/Momentum — do not invent a mid-point score "
        "to fill the gap; a missing dimension is not a bad one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "skill_score": {
                "type": "integer",
                "description": f"0-{RUBRIC_MAX['skill']}. How well the candidate's demonstrated skills cover what this JD asks for.",
            },
            "skill_analysis": {"type": "string", "description": "1-3 sentences: what matched, what's missing."},
            "seniority_score": {
                "type": "integer",
                "description": f"0-{RUBRIC_MAX['seniority']}. How well the candidate's seniority/experience level matches what this role expects.",
            },
            "seniority_analysis": {"type": "string"},
            "domain_transferability_score": {
                "type": "integer",
                "description": f"0-{RUBRIC_MAX['domain_transferability']}. How well the candidate's domain experience transfers to this job's domain.",
            },
            "domain_transferability_analysis": {"type": "string"},
            "career_narrative_score": {
                "type": "integer",
                "description": f"0-{RUBRIC_MAX['career_narrative']}. Whether this job is a sensible next chapter given the candidate's stated career goals.",
            },
            "career_narrative_analysis": {"type": "string"},
            "company_research_score": {
                "type": ["integer", "null"],
                "description": f"0-{RUBRIC_MAX['company_research']}, or null if the report has no usable data on any dimension. "
                                "Base this only on the Reputation/Stability/Momentum dimensions the report actually found data for.",
            },
            "company_research_analysis": {"type": "string"},
        },
        "required": [
            "skill_score", "skill_analysis",
            "seniority_score", "seniority_analysis",
            "domain_transferability_score", "domain_transferability_analysis",
            "career_narrative_score", "career_narrative_analysis",
            "company_research_score", "company_research_analysis",
        ],
        "additionalProperties": False,
    },
    "strict": True,
}

_SYSTEM_PROMPT = """You are a career-fit judge helping a candidate decide whether a job is worth applying to. \
You'll be given the candidate's resume, their stated career goals, a job description, and a research report \
about the hiring company. Score the rubric via the submit_rubric tool.

Be a discriminating judge, not a generous one — most jobs should not score near the max on every dimension. \
Use the low end of each range when the JD is vague, the fit is weak, or the evidence is thin.
"""


def judge_job(
    job: Job, resume: Resume, career_goals: CareerGoals, company: Company | None, session,
    client: anthropic.Anthropic | None = None,
) -> JudgeResult:
    client = client or anthropic.Anthropic()
    report = (
        get_or_create_report(company, session, client=client)
        if company is not None
        else "No company record on file for this job — nothing to research."
    )

    user_content = (
        f"# Resume\n{resume.content}\n\n"
        f"# Career goals\n{career_goals.content}\n\n"
        f"# Job description ({job.title} at {job.company_name})\n{job.raw_text}\n\n"
        f"# Company research report\n{report}\n"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        tools=[_SUBMIT_RUBRIC_TOOL],
        tool_choice={"type": "tool", "name": "submit_rubric"},
        messages=[{"role": "user", "content": user_content}],
    )
    rubric = next(block.input for block in response.content if block.type == "tool_use")

    non_company_total = (
        rubric["skill_score"] + rubric["seniority_score"]
        + rubric["domain_transferability_score"] + rubric["career_narrative_score"]
    )
    company_research_score = rubric["company_research_score"]
    if company_research_score is None:
        # Missing-Dimension Rule at the total-score level (docs/adr/0001):
        # exclude company_research rather than zero-fill it, renormalized
        # back to the same /300 scale so scores stay comparable across jobs
        # regardless of whether company data existed.
        total_score = non_company_total / _MAX_WITHOUT_COMPANY_RESEARCH * sum(RUBRIC_MAX.values())
    else:
        total_score = non_company_total + company_research_score

    result = job.judge_result or JudgeResult(job_id=job.id)
    result.skill_score = rubric["skill_score"]
    result.skill_analysis = rubric["skill_analysis"]
    result.seniority_score = rubric["seniority_score"]
    result.seniority_analysis = rubric["seniority_analysis"]
    result.domain_transferability_score = rubric["domain_transferability_score"]
    result.domain_transferability_analysis = rubric["domain_transferability_analysis"]
    result.career_narrative_score = rubric["career_narrative_score"]
    result.career_narrative_analysis = rubric["career_narrative_analysis"]
    result.company_research_score = company_research_score
    result.company_research_analysis = rubric["company_research_analysis"]
    result.total_score = total_score
    result.judged_at = datetime.now(timezone.utc)

    session.add(result)
    session.commit()
    return result
