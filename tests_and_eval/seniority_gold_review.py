"""Confirm seniority gold labels in the browser.

    PYTHONPATH=. ./venv/bin/python -m tests_and_eval.seniority_gold_review      → http://127.0.0.1:5058

Each posting shows the drafted label (level, is_contract) with its
evidence highlighted in the text. Accept it or change it, then Save: that
writes `gold` and marks the label confirmed. The latest seniority_level.py run
on the job is shown for comparison; decide from the posting text, not from
the model's answer. Old local seniority scores are deliberately not shown.
"""

import html
import json

from flask import Flask, redirect, render_template_string, request, url_for

from db.cloud_models import SENIORITY_LEVELS
from tests_and_eval.seniority_gold_eval import GOLD, _describe

app = Flask(__name__)

PAGE = """<!doctype html><meta charset=utf-8><title>Seniority gold · {{ title }}</title>
<style>
 body{font:14px system-ui,sans-serif;margin:0;color:#1c1008;background:#fffdf7}
 header{padding:10px 20px;border-bottom:1px solid #f0dfa8;display:flex;gap:16px;align-items:center}
 a{color:#b45309} main{display:grid;grid-template-columns:1fr 360px;height:calc(100vh - 45px)}
 .text{padding:16px 20px;overflow:auto;white-space:pre-wrap;line-height:1.55;border-right:1px solid #f0dfa8}
 mark{background:#fde68a} form{padding:14px 16px;overflow:auto}
 h3{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#7a6248;margin:14px 0 6px}
 label{display:block;padding:3px 0} select,input[type=number]{font-size:14px;padding:3px}
 table{border-collapse:collapse;margin:16px 20px} td,th{padding:4px 10px;border-bottom:1px solid #f0dfa8;text-align:left}
 button{background:#b45309;color:#fff;border:0;border-radius:5px;padding:8px 16px;font-size:14px;cursor:pointer;margin-top:12px}
 textarea{width:100%;height:80px} .draft{color:#7a6248;font-size:13px}
</style>{{ body|safe }}"""


def _labels() -> dict[int, dict]:
    return {json.loads(p.read_text())["job_id"]: json.loads(p.read_text()) for p in sorted((GOLD / "labels").glob("jd-*.json"))}


def _latest_model_answers(job_id: int) -> tuple[str, list[dict]] | None:
    """(run name, rows for this job) from the newest saved eval run that has it."""
    runs = sorted((GOLD / "runs").glob("*.json"), reverse=True) if (GOLD / "runs").exists() else []
    for path in runs:
        rows = [r for r in json.loads(path.read_text())["rows"] if r["job_id"] == job_id and "contract" in r and "agency" not in r]
        if rows:
            return path.stem, sorted(rows, key=lambda r: r["rep"])
    return None


def _path(job_id: int):
    return GOLD / "labels" / f"jd-{job_id}.json"


@app.get("/")
def index():
    labels = _labels()
    done = sum(label["status"] == "confirmed" for label in labels.values())
    rows = "".join(
        f"<tr><td><a href='{url_for('job', job_id=j)}'>jd-{j}</a></td><td>{label['split']}</td><td>{label['status']}</td>"
        f"<td>{html.escape(_describe(*(lambda d: (d['level'], d['is_contract']))(label['gold'] or label['draft'])))}</td></tr>"
        for j, label in sorted(labels.items(), key=lambda kv: (kv[1]["status"] == "confirmed", kv[0]))
    )
    body = (f"<header><b>Seniority gold set</b> {done} / {len(labels)} confirmed</header>"
            f"<table><tr><th>Posting</th><th>Split</th><th>Status</th><th>Label</th></tr>{rows}</table>")
    return render_template_string(PAGE, title="index", body=body)


@app.get("/job/<int:job_id>")
def job(job_id):
    label = _labels()[job_id]
    current = label["gold"] or label["draft"]
    text = html.escape((GOLD / "jds" / f"jd-{job_id}.txt").read_text())
    for fragment in sorted(label["draft"].get("evidence") or [], key=len, reverse=True):
        escaped = html.escape(fragment)
        text = text.replace(escaped, f"<mark>{escaped}</mark>")

    def options(values, selected):
        return "".join(f"<option value='{v}' {'selected' if v == selected else ''}>{v or '—'}</option>" for v in values)

    def checkbox(name, on):
        return f"<label><input type=checkbox name={name} value=1 {'checked' if on else ''}> {name}</label>"

    latest = _latest_model_answers(job_id)
    if latest:
        run_name, rows = latest
        answers = "<br>".join(html.escape(r["error"]) if r["error"] else
                              f"{html.escape(_describe(r['level'], r['contract']))} ({'ok' if r['all_ok'] else 'MISS'})"
                              for r in rows)
        model = f"<h3>Model</h3><div class=draft>{answers}<br><small>{html.escape(run_name)}</small></div>"
    else:
        model = "<h3>Model</h3><div class=draft>not run on this job yet</div>"
    draft = label["draft"]
    form = f"""<form method=post action='{url_for('save', job_id=job_id)}'>
      {model}
      <h3>Draft</h3><div class=draft>{html.escape(_describe(draft['level'], draft['is_contract']))}
        · years: {draft['years_required']}<br>{html.escape(draft.get('note') or '')}</div>
      <h3>Level</h3><select name=level>{options([*SENIORITY_LEVELS, ''], current['level'] or '')}</select>
      <h3>Flags</h3>{checkbox('is_contract', current['is_contract'])}
      <h3>Years required</h3><input type=number name=years_required min=0 max=40 value='{current['years_required'] if current['years_required'] is not None else ''}'>
      <h3>Note</h3><textarea name=note>{html.escape(current.get('note') or '')}</textarea>
      <button>Save and confirm</button></form>"""
    pending = [j for j, other in sorted(_labels().items()) if other["status"] != "confirmed" and j != job_id]
    nxt = f"<a href='{url_for('job', job_id=pending[0])}'>next unconfirmed →</a>" if pending else ""
    body = (f"<header><a href='{url_for('index')}'>← all</a><b>jd-{job_id}</b> {label['split']} · {label['status']} {nxt}</header>"
            f"<main><div class=text>{text}</div>{form}</main>")
    return render_template_string(PAGE, title=f"jd-{job_id}", body=body)


@app.post("/job/<int:job_id>")
def save(job_id):
    label = _labels()[job_id]
    years = request.form.get("years_required", "").strip()
    label["gold"] = {
        "level": request.form.get("level") or None,
        "is_contract": bool(request.form.get("is_contract")),
        "years_required": int(years) if years else None,
        "inferred": not years,
        "evidence": label["draft"].get("evidence") or [],
        "note": request.form.get("note", "").strip() or None,
    }
    label["status"] = "confirmed"
    _path(job_id).write_text(json.dumps(label, indent=1, ensure_ascii=False) + "\n")
    pending = [j for j, other in sorted(_labels().items()) if other["status"] != "confirmed"]
    return redirect(url_for("job", job_id=pending[0]) if pending else url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5058, debug=False)
