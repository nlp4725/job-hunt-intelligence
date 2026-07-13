"""
Flask REST API for the Screening dashboard — local-only, no auth (single
user, runs on localhost). GET /api/jobs returns every screened job (Job +
Company + ScreeningResult joined) ranked by total_score descending; sorting
beyond the default happens client-side in the UI (see templates/index.html)
since the whole result set is small enough (thousands of rows, not
millions) to ship in one response and sort in the browser rather than
building server-side sort/pagination params. PATCH /api/jobs/<id> updates
the user-marked status fields (applied, expired, not_interested,
not_interested_note) — the only mutations this API exposes.
"""

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from analysis.posted_date_parser import parse_posted_date
from db.models import Company, Job, ScreeningResult
from db.session import get_session

app = Flask(__name__)
CORS(app)

_PATCHABLE_BOOL_FIELDS = ("applied", "expired", "not_interested")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/jobs")
def api_jobs():
    session = get_session()
    try:
        rows = (
            session.query(Job, Company, ScreeningResult)
            .join(ScreeningResult, ScreeningResult.job_id == Job.id)
            .outerjoin(Company, Job.company_id == Company.id)
            .order_by(ScreeningResult.total_score.desc())
            .all()
        )

        jobs = []
        for job, company, result in rows:
            posted_at = parse_posted_date(job.posted_date, job.first_seen_at)
            jobs.append({
                "id": job.id,
                "title": job.title,
                "company_name": job.company_name,
                "company_industry": company.industry if company else None,
                "company_size": company.size if company else None,
                "location": job.location,
                "url": job.url,
                "posted_date_raw": job.posted_date,
                "posted_at": posted_at.isoformat() if posted_at else None,
                "skill_score": result.skill_score,
                "seniority_score": result.seniority_score,
                "expertise_score": result.expertise_score,
                "total_score": result.total_score,
                "applied": job.applied,
                "expired": job.expired,
                "not_interested": job.not_interested,
                "not_interested_note": job.not_interested_note,
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

    note_provided = "not_interested_note" in body
    if note_provided and body["not_interested_note"] is not None and not isinstance(body["not_interested_note"], str):
        return jsonify({"error": "'not_interested_note' must be a string or null"}), 400

    if not updates and not note_provided:
        return jsonify({"error": f"body must include at least one of {_PATCHABLE_BOOL_FIELDS + ('not_interested_note',)}"}), 400

    session = get_session()
    try:
        job = session.get(Job, job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404

        for field, value in updates.items():
            setattr(job, field, value)
        if note_provided:
            job.not_interested_note = body["not_interested_note"]

        session.commit()
        return jsonify({
            "id": job.id,
            "applied": job.applied,
            "expired": job.expired,
            "not_interested": job.not_interested,
            "not_interested_note": job.not_interested_note,
        })
    finally:
        session.close()


if __name__ == "__main__":
    app.run(debug=True, port=5050)
