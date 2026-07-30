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

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from analysis.posted_date_parser import parse_posted_date
from analysis.top_tech_companies import is_top500_tech
from db.models import Company, Job, ScreeningResult, utcnow
from db.session import get_session

app = Flask(__name__)
CORS(app)

_PATCHABLE_BOOL_FIELDS = ("applied", "expired", "not_interested")
_PATCHABLE_TEXT_FIELDS = ("not_interested_note", "note")


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

        jobs = []
        for job, company, result in rows:
            posted_at = parse_posted_date(job.posted_date, job.first_seen_at)
            duplicate_of = duplicate_targets.get(job.duplicate_of_job_id) if job.duplicate_of_job_id else None
            jobs.append({
                "id": job.id,
                "title": job.title,
                "track": job.track,
                "company_name": job.company_name,
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
        for field, value in text_updates.items():
            setattr(job, field, value)

        session.commit()
        return jsonify({
            "id": job.id,
            "applied": job.applied,
            "applied_at": job.applied_at.isoformat() if job.applied_at else None,
            "expired": job.expired,
            "not_interested": job.not_interested,
            "not_interested_note": job.not_interested_note,
            "note": job.note,
        })
    finally:
        session.close()


if __name__ == "__main__":
    app.run(debug=True, port=5050)
