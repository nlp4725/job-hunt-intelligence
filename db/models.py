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
# AI scientist / LLM all roll up to "ml_ai"; product manager is its own "pm"
# track. Old entries (machine learning, ai engineer, ai scientist, product
# manager in software) are kept even after the active scraper keyword list
# narrowed to just "llm" (see scraper/run_scrape.py, Aug 2026) — historical
# jobs already in the DB were scraped under those keywords and still need
# to resolve to the right track.
KEYWORD_TRACKS: dict[str, str] = {
    "machine learning": "ml_ai",
    "ai engineer": "ml_ai",
    "ai scientist": "ml_ai",
    "llm": "ml_ai",
    "llm remote": "ml_ai",  # 2026-08-17: "remote" folded into the literal keyword itself (see scraper/run_scrape.py's KEYWORDS) rather than relied on as a separate LinkedIn filter — see CONTEXT.md's broad-match Remote-filter-reliability note.
    "product manager in software": "pm",
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

    ats_provider: Mapped[str | None]                                        # e.g. "greenhouse", "lever" — set by analysis/ats_detector.py probing public job-board APIs; NULL = not yet checked or no match found among supported providers
    ats_slug: Mapped[str | None]                                            # the slug that matched on ats_provider's board (e.g. "affirm" for boards-api.greenhouse.io/v1/boards/affirm/jobs)
    ats_checked_at: Mapped[datetime | None]                                 # when ats_detector.py last probed this company — distinguishes "checked, no match" from "never checked"

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
    workplace_type: Mapped[str | None]                                # "Remote" / "Hybrid" / "On-site" — the job's own displayed badge (job-details-fit-level-preferences), not derived from which f_WT filter the search used
    workplace_type_source: Mapped[str | None]                         # "linkedin" when read off the posting, "raw_text" when inferred offline by analysis/workplace_from_raw_text.py (~95% precise, Remote only) — never treat the two as equally trustworthy

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
    applied_at: Mapped[datetime | None]                               # set when `applied` is switched to True via PATCH /api/jobs/<id> (backend/app.py); cleared if switched back to False. Existing applied=True rows predating this column stay NULL — no reliable way to back-date them.
    applied_resume_version: Mapped[str | None]                        # which resume variant was used, e.g. "v1"/"v2" — set by the extension's apply buttons for the August 2026 resume A/B test (see CONTEXT.md "Resume A/B Test"). Null for anything applied to before this existed, or applied to outside the extension.
    expired: Mapped[bool] = mapped_column(default=False)              # user-marked: listing is dead/filled, not scrape-detected staleness (see `status` below)
    not_interested: Mapped[bool] = mapped_column(default=False)       # user-marked: decided not to apply
    not_interested_note: Mapped[str | None] = mapped_column(Text)     # why, only meaningful when not_interested is True
    note: Mapped[str | None] = mapped_column(Text)                    # general free-text note, independent of status (e.g. interview/assessment tracking)
    status: Mapped[str] = mapped_column(default="active")            # active/stale — set stale if a scrape stops seeing it
    repost_count: Mapped[int] = mapped_column(default=0)             # bumped when an already-known, still-open job (not applied/not_interested) reappears in a TIME_RANGE_DAY search after a >REPOST_GAP_DAYS gap since last_seen_at — see scraper/run_scrape.py dedup_page()
    detail_fetched: Mapped[bool] = mapped_column(default=False)       # False = placeholder row (id only, saved during phase-1 id collection) still awaiting phase-2 detail fetch
    is_relevant: Mapped[bool] = mapped_column(default=True)           # False = title didn't match its track's curated terms (see analysis/title_filter.py) — LinkedIn's keyword search matches full JD text, not just title, so noisy off-track matches slip through
    duplicate_of_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))  # set at scrape time (analysis/duplicate_detector.py) when a same-company job's JD text is a near-exact match to an earlier job — same underlying posting rescraped under a different LinkedIn job_id (a repost), not a same-company-different-role coincidence. Points at the earliest match, not necessarily the very first ever posted.

    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    # NO onupdate= here, deliberately. It used to carry onupdate=utcnow, which
    # fires on *any* UPDATE to the row regardless of which columns changed —
    # so an unrelated backfill re-stamped it wholesale (2026-09-08:
    # analysis/workplace_from_raw_text.py writing workplace_type moved
    # last_seen_at forward on 3181 rows in one commit). That silently rewrote
    # every affected job's displayed post date, since posted_date is stored as
    # relative text ("4 days ago") and used to be anchored to this column.
    # Set it explicitly at the three sites that genuinely re-see a listing:
    # scraper/run_scrape.py (dedup_page), db/job_writer.py (save_new_job),
    # backend/app.py (the duplicate-capture merge).
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    # When job.posted_date's *text* was actually read off LinkedIn. This is the
    # only correct anchor for interpreting that relative string (see
    # analysis/posted_date_parser.py) and exists precisely so the parse can
    # never again be broken by an unrelated write. Distinct from last_seen_at:
    # a listing can be re-seen (row touched, freshness confirmed) without its
    # posted_date text being re-read — scraper/run_scrape.py bumps last_seen_at
    # for already-detailed jobs without refetching detail, which had drifted
    # 443 rows' post dates forward by up to 43 days. Write it *only* alongside
    # a write to posted_date. NULL only for pre-migration rows nothing could
    # date; parse_posted_date falls back to last_seen_at in that case.
    posted_date_seen_at: Mapped[datetime | None]

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
    track: Mapped[str | None]                                         # "ml_ai" or "pm" — which track's jobs this resume scores against; NULL = default/fallback used when no track-specific row exists
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

    to_c_product_pm: Mapped[bool] = mapped_column(default=False)  # PM track only: True when expertise_matched_domains includes "D2" (marketing & consumer/to-C products) — derived deterministically from that field, no separate LLM call. Meaningless/left False for ml_ai track jobs.

    total_score: Mapped[int | None]                          # 0-15, plain sum of skill_score + seniority_score + expertise_score
    screened_at: Mapped[datetime | None]

    job: Mapped[Job] = relationship(back_populates="screening_result")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str]                                                  # "user" or "assistant"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ExtractionEvent(Base):
    """One row per browser-extension capture, recording which extraction
    strategy won for each field — and, when a field had no winner at all, a
    snapshot of the DOM that defeated it.

    Why this table exists. LinkedIn rebuilds this page's markup regularly
    (hashed CSS classes 2026-08, the top-card meta-line collapse 2026-09) and
    every rebuild silently breaks a field. Before this, a break left no trace:
    extension/content/extract.js logged a console warning nobody was reading,
    the row saved with NULL, and the failure was only noticed ~10K rows later
    when analysis/workplace_from_raw_text.py had to reconstruct the field
    offline. Worse, by the time anyone looked, the markup that broke it was
    gone — so writing a new strategy meant re-visiting LinkedIn and hoping to
    hit the same layout again.

    So: record the winning strategy per field on EVERY capture (a single null
    is normal — not every posting shows applicant stats; a null *rate* is the
    real signal), and keep the evidence when nothing won. The snapshots double
    as regression fixtures, so a future selector change can be tested against
    every layout already seen without touching the network.
    """

    __tablename__ = "extraction_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str | None]                                  # LinkedIn's job id, not Job.id — an event is worth keeping even if the capture never produced a row
    captured_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    strategies: Mapped[dict | None] = mapped_column(JSON)       # {field: winning strategy name, or null if every strategy failed}
    failed_fields: Mapped[list | None] = mapped_column(JSON)    # fields with no winner; [] on a clean capture
    # Trimmed outerHTML of the top card, stored ONLY on a failed capture and
    # only for the first few occurrences of each distinct failure signature
    # (see backend/app.py:_record_extraction_event) — enough to diagnose and
    # to serve as a fixture, without turning every LinkedIn layout tweak into
    # thousands of near-identical HTML blobs.
    snapshot_html: Mapped[str | None] = mapped_column(Text)


class CollectionPage(Base):
    """Per-page funnel for a manual LinkedIn screening session — the run record
    for the browser-extension collection path.

    scrape_runs only ever covered the Selenium scraper (last entry 2026-08-17);
    every job collected since then came through the extension and left no run
    record at all. So there was no way to answer the two questions you have to
    be able to answer about any ingestion system: did we see everything we
    should have, and what happened to what we saw?

    One row per search-results page, written by the linkedin-manual-screen
    skill as it works. `rendered` is the load-bearing number: LinkedIn serves
    25 cards per page, so anything less means the page was not fully scrolled
    and listings were silently never seen — a miss that is invisible in the DB
    afterwards, because a job you never enumerated leaves no trace anywhere.

    rendered = skipped + clicked must hold exactly. A gap means cards were
    enumerated and then dropped by neither rule — i.e. lost.
    """

    __tablename__ = "collection_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(index=True)   # one id per screening session, so pages group into a run
    keyword: Mapped[str]
    endpoint: Mapped[str | None]                          # "literal" (/jobs/search/) or "semantic" (/jobs/search-results/)
    page: Mapped[int]
    rendered: Mapped[int]                                 # cards actually enumerated on the page; expected 25
    skipped: Mapped[int]                                  # excluded by the standing filters (agency / off-track / already cached)
    clicked: Mapped[int]                                  # opened, captured and sent for screening
    recorded_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    # How many pages the run INTENDED to do, recorded on every page. Without it
    # a crashed run is indistinguishable from a short one: pages 1-14 are on
    # disk either way, and the end-of-run checks (SKILL.md report steps 5-6)
    # never ran to say otherwise, because a session that dies at page 14 never
    # reaches its own report. Knowing the target turns "14 pages recorded" into
    # "stopped after 14 of 20".
    pages_planned: Mapped[int | None]
