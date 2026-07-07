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
    salary_min: Mapped[float | None]                                  # future: parsed via LLM from raw_text/salary_text
    salary_max: Mapped[float | None]

    posted_date: Mapped[str | None]                                   # raw text, e.g. "1 week ago" — not parsed to a real date yet
    applicant_stats: Mapped[str | None]                               # e.g. "Over 100 people clicked apply"

    match_score: Mapped[float | None]                                 # placeholder for future resume-match scoring

    applied: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(default="active")            # active/stale — set stale if a scrape stops seeing it
    detail_fetched: Mapped[bool] = mapped_column(default=False)       # False = placeholder row (id only, saved during phase-1 id collection) still awaiting phase-2 detail fetch
    is_relevant: Mapped[bool] = mapped_column(default=True)           # False = title didn't match its track's curated terms (see analysis/title_filter.py) — LinkedIn's keyword search matches full JD text, not just title, so noisy off-track matches slip through

    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    company: Mapped[Company | None] = relationship(back_populates="jobs")
    skills: Mapped[list["JobSkill"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    analysis: Mapped["JobAnalysis"] = relationship(back_populates="job", uselist=False, cascade="all, delete-orphan")


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


class JobAnalysis(Base):
    __tablename__ = "job_analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)   # one analysis per job — re-analyzing overwrites
    keyword_score: Mapped[float | None]
    semantic_score: Mapped[float | None]
    overall_score: Mapped[float | None]
    matched_skills: Mapped[list | None] = mapped_column(JSON)
    gap_skills: Mapped[list | None] = mapped_column(JSON)
    narrative: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime | None]

    job: Mapped[Job] = relationship(back_populates="analysis")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str]                                                  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
