"""
SQLAlchemy models for the job hunt intelligence DB — one SQLite file, one
schema, with a `track` column distinguishing ml_ai vs pm jobs rather than
physically separate database files. Keeps cross-track aggregation possible
(skills dashboard, shared company records) while still letting every query
filter to a single track when that's what's wanted.
"""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, JSON, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Maps each search keyword to its track. machine learning / AI engineer /
# AI scientist all roll up to "ml_ai"; product manager is its own "pm" track.
KEYWORD_TRACKS: dict[str, str] = {
    "machine learning": "ml_ai",
    "ai engineer": "ml_ai",
    "ai scientist": "ml_ai",
    "product manager": "pm",
}


def track_for_keyword(keyword: str) -> str:
    return KEYWORD_TRACKS.get(keyword.lower(), "unknown")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True, index=True)
    homepage_url: Mapped[str | None]
    industry: Mapped[str | None]
    size: Mapped[str | None]                                   # e.g. "11-50 employees"
    summary: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime | None]

    research_report: Mapped[str | None] = mapped_column(Text)              # Company Research Agent's markdown findings (Reputation/Stability/Momentum) — cached per company, reused across every job there
    research_report_generated_at: Mapped[datetime | None]

    jobs: Mapped[list["Job"]] = relationship(back_populates="company")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(unique=True, index=True)    # LinkedIn's own job ID — the real dedup key
    url: Mapped[str]                                                  # clean canonical URL built from job_id
    title: Mapped[str | None]
    company_name: Mapped[str | None]                                  # raw string as scraped, before company_analyzer runs
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    location: Mapped[str | None]

    keyword_matched: Mapped[str]                                      # which search keyword found this job
    track: Mapped[str]                                                # "ml_ai" or "pm", derived from keyword_matched

    raw_text: Mapped[str | None] = mapped_column(Text)
    salary_text: Mapped[str | None]                                   # raw text as scraped, e.g. "$119K/yr - $173K/yr"
    salary_min: Mapped[float | None]                                  # annualized, parsed from salary_text (see analysis/salary_parser.py)
    salary_max: Mapped[float | None]

    posted_date: Mapped[str | None]                                   # raw text, e.g. "1 week ago" — not parsed to a real date yet
    applicant_stats: Mapped[str | None]                               # e.g. "Over 100 people clicked apply"

    match_score: Mapped[float | None]                                 # placeholder for future resume-match scoring

    applied: Mapped[bool] = mapped_column(default=False)
    expired: Mapped[bool] = mapped_column(default=False)              # user-marked: listing is dead/filled, not scrape-detected staleness (see `status` below)
    not_interested: Mapped[bool] = mapped_column(default=False)       # user-marked: decided not to apply
    not_interested_note: Mapped[str | None] = mapped_column(Text)     # why, only meaningful when not_interested is True
    status: Mapped[str] = mapped_column(default="active")            # active/stale — set stale if a scrape stops seeing it
    detail_fetched: Mapped[bool] = mapped_column(default=False)       # False = placeholder row (id only, saved during phase-1 id collection) still awaiting phase-2 detail fetch
    is_relevant: Mapped[bool] = mapped_column(default=True)           # False = title didn't match its track's curated terms (see analysis/title_filter.py) — LinkedIn's keyword search matches full JD text, not just title, so noisy off-track matches slip through

    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    company: Mapped[Company | None] = relationship(back_populates="jobs")
    skills: Mapped[list["JobSkill"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    screening_result: Mapped["ScreeningResult"] = relationship(back_populates="job", uselist=False, cascade="all, delete-orphan")


class JobSkill(Base):
    __tablename__ = "job_skills"
    __table_args__ = (UniqueConstraint("job_id", "skill_name", name="uq_job_skill"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    skill_name: Mapped[str] = mapped_column(index=True)               # canonical name from skills_extractor.SKILL_TAXONOMY

    job: Mapped[Job] = relationship(back_populates="skills")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_at: Mapped[datetime] = mapped_column(default=utcnow)
    keyword: Mapped[str]
    track: Mapped[str]
    num_found: Mapped[int] = mapped_column(default=0)                 # total job_ids seen across all pages this run
    num_new: Mapped[int] = mapped_column(default=0)                   # of those, how many were new (got full detail fetched)
    status: Mapped[str]                                                # "success" or "error"
    error_message: Mapped[str | None] = mapped_column(Text)


class Resume(Base):
    __tablename__ = "resume"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    original_filename: Mapped[str | None]
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class CareerGoals(Base):
    """Stated career preferences (e.g. "goal: technical PM, want full
    product-cycle exposure, prefer building/shipping systems over
    deep-learning research") — distinct from Resume, which records work
    history, not preference. Not currently fed to the Judge Agent — the
    long_term_career_alignment rubric dimension this once fed was dropped
    (too thin a spec, no calibration built) rather than developed further.
    Kept for potential future use. Single-row table, same shape as Resume."""

    __tablename__ = "career_goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ScreeningResult(Base):
    """One row per job: the Screening Agent's Stage 1 output (judge/stage1_screen.py)
    — skill_score is deterministic (analysis/skill_match.py, no LLM), seniority_score
    and expertise_score are each a separate calibrated LLM call (judge/seniority_fit.py,
    judge/expertise_match.py). Deliberately excludes company research — too
    slow/expensive to run on every job in this fast first-pass screen; see
    CONTEXT.md "Job Judge". total_score is a plain unweighted sum of the three
    0-5 dimensions (0-15) for dashboard ranking — each dimension stays
    independently visible too, rather than only the blended number."""

    __tablename__ = "screening_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)   # one screen per job — re-screening overwrites

    skill_score: Mapped[int | None]                          # 0-5, floor(matched/total * 5); 0 if JD names no taxonomy skill
    skill_ratio: Mapped[float | None]                        # raw overlap ratio behind skill_score
    skill_matched: Mapped[list | None] = mapped_column(JSON)          # direct resume<->JD skill matches
    skill_group_matched: Mapped[list | None] = mapped_column(JSON)    # JD skills satisfied via a SKILL_GROUPS sibling, not a direct match
    skill_missing: Mapped[list | None] = mapped_column(JSON)

    seniority_score: Mapped[int | None]                      # 0-5
    seniority_evidence: Mapped[str | None] = mapped_column(Text)
    seniority_years_required: Mapped[int | None]
    seniority_inferred: Mapped[bool | None]
    seniority_confidence: Mapped[str | None]
    seniority_note: Mapped[str | None] = mapped_column(Text)

    expertise_score: Mapped[int | None]                      # 0-5
    expertise_evidence: Mapped[str | None] = mapped_column(Text)
    expertise_matched_domains: Mapped[list | None] = mapped_column(JSON)
    expertise_matched_capabilities: Mapped[list | None] = mapped_column(JSON)
    expertise_matched_weaknesses: Mapped[list | None] = mapped_column(JSON)
    expertise_confidence: Mapped[str | None]
    expertise_note: Mapped[str | None] = mapped_column(Text)

    total_score: Mapped[int | None]                          # 0-15, plain sum of skill_score + seniority_score + expertise_score
    screened_at: Mapped[datetime | None]

    job: Mapped[Job] = relationship(back_populates="screening_result")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str]                                                  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
