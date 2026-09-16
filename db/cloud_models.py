"""Cloud-only tables: users and everything that belongs to one user, plus each
job's seniority level (docs/productization_build_plan.md §4).

Defined on their own metadata so the local SQLite app never sees them: local
code imports db.models, whose Base.metadata and init_db() stay exactly as they
were, and the shared tables (jobs, screening_results, ...) keep the shape local
code reads and writes. Foreign keys to shared tables point at db.models'
columns directly. Alembic migrates both metadatas in the cloud.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, JSON, LargeBinary, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from db.models import Job, utcnow

ROLES = ("user", "admin")
SENIORITY_LEVELS = ("entry", "mid", "senior", "senior_plus", "staff", "principal")
NON_FIT_REASONS = ("agency", "contract", "internship")
APPLICATION_STAGES = ("applied", "recruiter_screen", "interview", "offer", "rejected", "withdrawn")

JOB_ID = Job.__table__.c.id


def _one_of(name: str, column: str, values: tuple[str, ...]) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=name)


class CloudBase(DeclarativeBase):
    pass


class User(CloudBase):
    __tablename__ = "users"
    __table_args__ = (_one_of("ck_users_role", "role", ROLES),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The identity provider's `sub`, the only identity input once set. NULL until
    # a first login claims the row: the owner is created before Cognito exists.
    idp_subject: Mapped[str | None] = mapped_column(unique=True)
    email: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str | None]
    role: Mapped[str] = mapped_column(default="user")
    last_active_at: Mapped[datetime | None]
    taxonomy_version_seen: Mapped[str | None]                         # skills_extractor fingerprint; the update pop-up shows once per version
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None]


class UserResume(CloudBase):
    """One row per uploaded resume version. Named UserResume because
    db.models.Resume is the local, per-track resume table."""

    __tablename__ = "resumes"
    __table_args__ = (UniqueConstraint("user_id", "version", name="uq_resumes_user_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[int]
    original_filename: Mapped[str | None]
    storage_key: Mapped[str | None]                                    # encrypted original file in object storage
    text_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)  # normalized text, encrypted; never logged
    redacted_text: Mapped[str | None] = mapped_column(Text)            # the only resume text allowed into logs or LLM calls
    skills_extracted: Mapped[list | None] = mapped_column(JSON)
    skills_confirmed: Mapped[list | None] = mapped_column(JSON)        # after the user's edits; what matching uses
    taxonomy_version: Mapped[str | None]
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)


class UserProfile(CloudBase):
    """Versioned, never mutated: an edit writes version n+1, so every stored
    score stays attributable to the profile that produced it."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "version", name="uq_user_profiles_user_version"),
        _one_of("ck_user_profiles_seniority_target", "seniority_target", SENIORITY_LEVELS),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    version: Mapped[int]
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    seniority_target: Mapped[str]                                      # the level the user picked; proposes seniority_scores
    # The user's confirmed 0-5 score per job level, plus "not_a_fit" (internship /
    # contract / agency) and "unknown" (level unclear). Seniority Fit is a lookup
    # here (analysis/seniority_fit.py). NULL = not confirmed yet: the proposal for
    # seniority_target is used. Locked per version: an edit writes a new version.
    seniority_scores: Mapped[dict | None] = mapped_column(JSON)
    target_roles: Mapped[list | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    years_experience: Mapped[int | None]                               # inferred from the resume, a hint only
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class JobSeniority(CloudBase):
    """A job's level, classified once and shared by every user; each user's
    Seniority Fit is computed from it (§3.3)."""

    __tablename__ = "job_seniority"
    __table_args__ = (
        _one_of("ck_job_seniority_level", "level", SENIORITY_LEVELS),
        _one_of("ck_job_seniority_non_fit_reason", "non_fit_reason", NON_FIT_REASONS),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey(JOB_ID), unique=True)
    level: Mapped[str | None]                                          # NULL = nothing inferable
    non_fit_reason: Mapped[str | None]                                 # hard non-fit for every user
    years_required: Mapped[int | None]
    inferred: Mapped[bool | None]
    confidence: Mapped[str | None]
    evidence: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None]                                 # which prompt (or backfill) produced this row
    classified_at: Mapped[datetime] = mapped_column(default=utcnow)


class JobTracking(CloudBase):
    """A user's current status on a job. No row means untouched."""

    __tablename__ = "job_tracking"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_job_tracking_user_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(ForeignKey(JOB_ID))
    applied: Mapped[bool] = mapped_column(default=False)
    applied_at: Mapped[datetime | None]
    applied_resume_version: Mapped[str | None]
    not_interested: Mapped[bool] = mapped_column(default=False)
    not_interested_note: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ApplicationEvent(CloudBase):
    """Status history: appended on every stage change, never overwritten."""

    __tablename__ = "application_events"
    __table_args__ = (
        _one_of("ck_application_events_stage", "stage", APPLICATION_STAGES),
        Index("ix_application_events_user_job", "user_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(ForeignKey(JOB_ID))
    stage: Mapped[str]
    occurred_at: Mapped[datetime]
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class UserJobScore(CloudBase):
    """A user's scores on a job, computed in code (no LLM): Skill Match from
    stored skill sets, Seniority Fit from job_seniority and the profile target.
    The shared screening_results table stays the owner's per-job LLM output."""

    __tablename__ = "user_job_scores"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_job_scores_user_job"),
        Index("ix_user_job_scores_user_total", "user_id", "total_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    job_id: Mapped[int] = mapped_column(ForeignKey(JOB_ID))
    profile_version: Mapped[int]
    taxonomy_version: Mapped[str | None]
    skill_score: Mapped[int | None]
    skill_ratio: Mapped[float | None]
    skill_matched: Mapped[list | None] = mapped_column(JSON)
    skill_group_matched: Mapped[list | None] = mapped_column(JSON)
    skill_missing: Mapped[list | None] = mapped_column(JSON)
    seniority_fit: Mapped[int | None]
    total_score: Mapped[int | None]
    scored_at: Mapped[datetime] = mapped_column(default=utcnow)


class ApiToken(CloudBase):
    """Long-lived admin tokens for the extension and skill, stored hashed."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(unique=True)               # sha256; plaintext shown once
    label: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
