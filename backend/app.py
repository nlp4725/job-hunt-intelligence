"""
Flask REST API for the Screening dashboard — local-only, no auth (single
user, runs on localhost). GET /api/jobs returns every screened job (Job +
Company + ScreeningResult joined) ranked by total_score descending, PLUS
every confirmed duplicate listing (Job.duplicate_of_job_id set by
analysis/duplicate_detector.py at scrape time) even though a duplicate never
gets its own ScreeningResult (judge/screening_run.py skips scoring them) —
shown with a pointer back to the original job's title/applied
status/score instead of being silently invisible, so a repost of something
already applied to reads as "duplicate of X" rather than a fresh unscored
job to triage. Sorting beyond the default happens client-side in the UI
(see templates/index.html) since the whole result set is small enough
(thousands of rows, not millions) to ship in one response and sort in the
browser rather than building server-side sort/pagination params. PATCH
/api/jobs/<id> updates the user-marked status fields (applied, expired,
not_interested, not_interested_note, note) — the only mutations this API
exposes.
"""

from datetime import timedelta

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from analysis.posted_date_parser import parse_posted_date
from analysis.title_filter import classify_track
from analysis.top_tech_companies import is_top500_tech
from db.job_writer import save_new_job
from db.models import Company, ExtractionEvent, Job, ScreeningResult, utcnow
from db.session import get_session
from judge.agency_blocklist import is_agency_job
from judge.eligibility import load_resumes
from judge.stage1_screen import screen_job

app = Flask(__name__)
CORS(app)

_PATCHABLE_BOOL_FIELDS = ("applied", "expired", "not_interested")
_PATCHABLE_TEXT_FIELDS = ("not_interested_note", "note", "applied_resume_version")

# Fields the browser extension's extractJobDetail() must supply — a
# hand-maintained contract with extension/content/extract.js and
# db/job_writer.py:save_new_job()'s `detail[...]` accesses.
_EXTENSION_DETAIL_FIELDS = (
    "company", "industry", "company_size", "url", "title", "location",
    "workplace_type", "raw_text", "salary_text", "posted_date", "applicant_stats",
)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/jobs")
def api_jobs():
    session = get_session()
    try:
        rows = (
            session.query(Job, Company, ScreeningResult)
            .outerjoin(ScreeningResult, ScreeningResult.job_id == Job.id)
            .outerjoin(Company, Job.company_id == Company.id)
            .filter((ScreeningResult.id.isnot(None)) | (Job.duplicate_of_job_id.isnot(None)))
            .order_by(ScreeningResult.total_score.desc())
            .all()
        )

        # Duplicates have no ScreeningResult of their own — look up what each
        # one is a duplicate of so the dashboard can show "duplicate of
        # <title>, applied" instead of leaving it blank.
        duplicate_target_ids = {job.duplicate_of_job_id for job, _, _ in rows if job.duplicate_of_job_id}
        duplicate_targets = {}
        if duplicate_target_ids:
            target_rows = (
                session.query(Job, ScreeningResult)
                .outerjoin(ScreeningResult, ScreeningResult.job_id == Job.id)
                .filter(Job.id.in_(duplicate_target_ids))
                .all()
            )
            duplicate_targets = {
                t_job.id: {
                    "title": t_job.title,
                    "applied": t_job.applied,
                    "total_score": t_result.total_score if t_result else None,
                }
                for t_job, t_result in target_rows
            }

        # How many DISTINCT jobs the user has marked applied at each company,
        # across the whole DB — not just the rows in this response (which is
        # filtered to screened jobs) — so a company shows its true total.
        # Grouped on a case/whitespace-normalized company_name rather than
        # company_id because 46 applied rows have neither a Company row nor a
        # name; those must stay uncounted, not collapse into one fake company.
        applied_by_company = {}
        for (name,) in session.query(Job.company_name).filter(Job.applied.is_(True)).all():
            if not name or not name.strip():
                continue
            key = name.strip().lower()
            applied_by_company[key] = applied_by_company.get(key, 0) + 1

        jobs = []
        for job, company, result in rows:
            posted_at = parse_posted_date(job.posted_date, job.posted_date_seen_at or job.last_seen_at)
            duplicate_of = duplicate_targets.get(job.duplicate_of_job_id) if job.duplicate_of_job_id else None
            jobs.append({
                "id": job.id,
                "title": job.title,
                "track": job.track,
                "company_name": job.company_name,
                "company_applied_count": applied_by_company.get(
                    (job.company_name or "").strip().lower(), 0
                ),
                "company_industry": company.industry if company else None,
                "company_size": company.size if company else None,
                "top500_tech": is_top500_tech(job.company_name),
                "location": job.location,
                "workplace_type": job.workplace_type,
                "url": job.url,
                "posted_date_raw": job.posted_date,
                "posted_at": posted_at.isoformat() if posted_at else None,
                "skill_score": result.skill_score if result else None,
                "seniority_score": result.seniority_score if result else None,
                "expertise_score": result.expertise_score if result else None,
                "to_c_product_pm": result.to_c_product_pm if result else None,
                "total_score": result.total_score if result else None,
                "applied": job.applied,
                "applied_at": job.applied_at.isoformat() if job.applied_at else None,
                "applied_resume_version": job.applied_resume_version,
                "expired": job.expired,
                "not_interested": job.not_interested,
                "not_interested_note": job.not_interested_note,
                "note": job.note,
                "duplicate_of_job_id": job.duplicate_of_job_id,
                "duplicate_of_title": duplicate_of["title"] if duplicate_of else None,
                "duplicate_of_applied": duplicate_of["applied"] if duplicate_of else None,
                "duplicate_of_total_score": duplicate_of["total_score"] if duplicate_of else None,
            })

        return jsonify({"count": len(jobs), "jobs": jobs})
    finally:
        session.close()


@app.route("/api/jobs/<int:job_id>", methods=["PATCH"])
def api_update_job(job_id):
    body = request.get_json(silent=True) or {}
    updates = {k: v for k, v in body.items() if k in _PATCHABLE_BOOL_FIELDS}
    for field in updates:
        if not isinstance(updates[field], bool):
            return jsonify({"error": f"'{field}' must be a boolean"}), 400

    text_updates = {}
    for field in _PATCHABLE_TEXT_FIELDS:
        if field not in body:
            continue
        if body[field] is not None and not isinstance(body[field], str):
            return jsonify({"error": f"'{field}' must be a string or null"}), 400
        text_updates[field] = body[field]

    if not updates and not text_updates:
        return jsonify({"error": f"body must include at least one of {_PATCHABLE_BOOL_FIELDS + _PATCHABLE_TEXT_FIELDS}"}), 400

    session = get_session()
    try:
        job = session.get(Job, job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404

        for field, value in updates.items():
            setattr(job, field, value)
            if field == "applied":
                job.applied_at = utcnow() if value else None
                if not value:
                    job.applied_resume_version = None
        for field, value in text_updates.items():
            setattr(job, field, value)

        session.commit()
        return jsonify({
            "id": job.id,
            "applied": job.applied,
            "applied_at": job.applied_at.isoformat() if job.applied_at else None,
            "applied_resume_version": job.applied_resume_version,
            "expired": job.expired,
            "not_interested": job.not_interested,
            "not_interested_note": job.not_interested_note,
            "note": job.note,
        })
    finally:
        session.close()


def _job_summary(job: Job) -> dict:
    return {
        "id": job.id,
        "job_id": job.job_id,
        "title": job.title,
        "company_name": job.company_name,
        "track": job.track,
        "applied": job.applied,
        "applied_resume_version": job.applied_resume_version,
    }


def _agency_or_duplicate_response(job: Job, session) -> dict | None:
    """Shared by the capture and lookup endpoints: the blocked-status
    response if `job` is agency-flagged or a confirmed duplicate, else None
    (eligible to proceed — capture continues on to scoring, lookup treats
    None as "not decided yet, fall through to the normal capture flow")."""
    if is_agency_job(job):
        return {"status": "blocked", "reason": "agency", "job": _job_summary(job)}

    if job.duplicate_of_job_id is not None:
        target = session.get(Job, job.duplicate_of_job_id)
        target_result = (
            session.query(ScreeningResult).filter(ScreeningResult.job_id == target.id).first()
            if target else None
        )
        refreshed = _refresh_stale_duplicate_target(session, job, target)
        return {
            "status": "blocked",
            "reason": "duplicate",
            "refreshed": refreshed,
            "job": _job_summary(job),
            "duplicate_of": {
                "job_id": target.id if target else None,
                "title": target.title if target else None,
                "applied": target.applied if target else None,
                "total_score": target_result.total_score if target_result else None,
            },
        }
    return None


def _refresh_stale_duplicate_target(session, job: Job, target: Job | None) -> bool:
    """A duplicate capture is proof the underlying opening is live *today* —
    LinkedIn only served it because it's still posted. Before this, that
    proof was thrown away: the new row was skipped as a duplicate and the
    original kept its old date, so a still-open job you never applied to
    silently aged past the dashboard's 2-week cutoff and never resurfaced.
    Worse, if the original had been marked `expired` (index.html line 582
    hides those by default) both rows were invisible at once — observed on
    Dealstitch AI "AI Quality Engineer" (11/15, never applied): a Sep-4
    capture pointed at a Sep-2 row flagged expired.

    So when the original is still actionable — not applied, not marked
    not-interested — carry the new sighting's freshness onto it and return
    True. Deliberately NOT rescored: the cached score is the same JD, and
    re-running the LLM would burn a call to reach an identical number. Only
    recency and reachability were ever broken.

    Applied or not-interested originals are left exactly as they were: the
    user already decided, and resurfacing a decided job is noise.
    """
    if target is None or target.applied or target.not_interested:
        return False

    # The new capture's own posted_date is the better signal when it's a
    # real relative-time string, but it can be junk — the extension has been
    # seen writing a location ("Chicago, IL (Remote)") into this field, and
    # parse_posted_date returns None on anything it can't read. Fall back to
    # the same "0 hours ago" sentinel job_writer already uses, which is
    # honest here: we just watched LinkedIn serve this listing live.
    now = utcnow()
    posted = job.posted_date if parse_posted_date(job.posted_date, now) else None
    target.posted_date = posted or "0 hours ago"
    # This capture is where the new posted_date text came from, so it is also
    # the anchor that text must be interpreted against (see
    # analysis/posted_date_parser.py). Always written alongside posted_date.
    target.posted_date_seen_at = now
    target.last_seen_at = now
    # The original's URL may be dead (that's often why it got flagged
    # expired); the row we just captured is the one LinkedIn is serving, so
    # point at it or the user can't actually apply.
    if job.url:
        target.url = job.url
    target.expired = False
    target.repost_count = (target.repost_count or 0) + 1
    session.commit()
    return True


def _score_response(job: Job, result: ScreeningResult, cached: bool) -> dict:
    return {
        "status": "scored",
        "cached": cached,
        "job": _job_summary(job),
        "score": {
            "skill_score": result.skill_score,
            "seniority_score": result.seniority_score,
            "expertise_score": result.expertise_score,
            "total_score": result.total_score,
            "skill_missing": result.skill_missing,
            "seniority_note": result.seniority_note,
            "expertise_note": result.expertise_note,
        },
    }


@app.route("/api/extension/jobs/<job_id>")
def api_extension_lookup(job_id):
    """Read-only instant-status check for the browser extension: on opening
    a job, content_script.js checks here FIRST, before starting its 6s dwell
    timer — a DB read costs nothing, so an already-known job (applied,
    agency-blocked, a duplicate, or already scored) can render immediately
    instead of waiting through the dwell gate that exists to hold off *new*,
    LLM-costing scoring. Response shapes match api_extension_capture()'s
    scored/blocked so content_script.js's handleScoreResponse renders either
    one identically. Never upserts or calls screen_job — a job this endpoint
    doesn't recognize (404) just falls through to the normal capture flow.
    """
    session = get_session()
    try:
        job = session.query(Job).filter(Job.job_id == str(job_id)).first()
        if job is None or not job.detail_fetched:
            return jsonify({"status": "not_found"}), 404

        blocked = _agency_or_duplicate_response(job, session)
        if blocked is not None:
            return jsonify(blocked)

        if job.screening_result is None:
            return jsonify({"status": "not_found"}), 404

        return jsonify(_score_response(job, job.screening_result, cached=True))
    finally:
        session.close()


# How many snapshots to keep per distinct failure signature. A LinkedIn layout
# change breaks the same field on every card, so without a cap one screening
# session would store hundreds of near-identical blobs. Three is enough to see
# whether a failure is layout-wide or particular to one posting.
_SNAPSHOTS_PER_SIGNATURE = 3


def _record_extraction_event(session, job_id, meta):
    """Persist which extraction strategy won each field, and keep the DOM when
    one lost. See db/models.py:ExtractionEvent for why this exists at all.

    Deliberately best-effort: telemetry must never be the reason a capture
    fails. A malformed meta dict from an extension version that's out of step
    with this server costs an event row, not the job.
    """
    if not isinstance(meta, dict):
        return
    try:
        # Must check the container is a list first: iterating a stray string
        # here silently yields its characters as "field names".
        raw_failed = meta.get("failed_fields")
        failed = [f for f in raw_failed if isinstance(f, str)][:20] if isinstance(raw_failed, list) else []
        snapshot = meta.get("snapshot_html") if failed else None
        if snapshot:
            signature = ",".join(sorted(failed))
            seen = (
                session.query(ExtractionEvent)
                .filter(
                    ExtractionEvent.snapshot_html.isnot(None),
                    ExtractionEvent.failed_fields == failed,
                    ExtractionEvent.captured_at > utcnow() - timedelta(days=7),
                )
                .count()
            )
            if seen >= _SNAPSHOTS_PER_SIGNATURE:
                snapshot = None
        session.add(ExtractionEvent(
            job_id=str(job_id),
            strategies=meta.get("strategies") if isinstance(meta.get("strategies"), dict) else None,
            failed_fields=failed,
            snapshot_html=snapshot[:20000] if isinstance(snapshot, str) else None,
        ))
        session.commit()
    except Exception as exc:  # noqa: BLE001 - see docstring
        session.rollback()
        app.logger.warning("extraction event not recorded: %s", exc)


@app.route("/api/extension/jobs", methods=["POST"])
def api_extension_capture():
    """Capture endpoint for the browser extension (extension/): a single job
    the user is actively viewing on LinkedIn, extracted client-side and
    POSTed here to be upserted, checked for eligibility, and scored — the
    on-demand single-job equivalent of what scraper/run_scrape.py does in
    bulk. See docs/... or the extension's own code for the full flow.

    Blocked outcomes (agency/duplicate/no resume for track) are a normal,
    expected response shape (200), not an error — the injected panel needs
    to render something sensible in each case, not just fail.
    """
    body = request.get_json(silent=True) or {}
    job_id = body.get("job_id")
    title = body.get("title")
    raw_text = body.get("raw_text")
    if not job_id or not title or not raw_text:
        return jsonify({"error": "job_id, title, and raw_text are required"}), 400

    track_override = body.get("track_override")
    if track_override not in ("ml_ai", "pm"):
        track_override = None

    session = get_session()
    try:
        resolved_track = track_override or classify_track(title)
        if resolved_track is None:
            # Job.track is NOT NULL — nothing safe to write yet until the
            # user resolves the ambiguity via the panel's track picker.
            return jsonify({
                "status": "needs_track",
                "title": title,
                "company_name": body.get("company"),
            })

        detail = {field: body.get(field) for field in _EXTENSION_DETAIL_FIELDS}
        job = save_new_job(session, keyword="extension", track=resolved_track, job_id=str(job_id), detail=detail)
        _record_extraction_event(session, job_id, body.get("extraction_meta"))

        blocked = _agency_or_duplicate_response(job, session)
        if blocked is not None:
            return jsonify(blocked)

        resumes_by_track, default_content = load_resumes(session)
        resume_content = resumes_by_track.get(job.track, default_content)
        if resume_content is None:
            return jsonify({"status": "blocked", "reason": "no_resume_for_track", "job": _job_summary(job)})

        # Cost control: a mere revisit of an already-scored job shouldn't
        # re-bill the two DeepSeek calls. Only (re)score when there's no
        # existing result yet, or the user explicitly asked for a
        # re-classification via track_override.
        if job.screening_result is not None and track_override is None:
            result = job.screening_result
            cached = True
        else:
            class _ResumeStub:
                content = resume_content

            result = screen_job(job, _ResumeStub(), session)
            cached = False

        return jsonify(_score_response(job, result, cached))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


if __name__ == "__main__":
    app.run(debug=True, port=5050, threaded=True)
