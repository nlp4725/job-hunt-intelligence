"""Confirm gold skill labels in the browser.

    ./venv/bin/python -m tests_and_eval.skills_gold_review      → http://127.0.0.1:5057

For each document: the LLM draft is pre-checked, skills the extractor found
that the draft didn't are listed separately (check the text before ticking),
and every other taxonomy skill is available by category. Saving writes the
checked set as `gold` and marks the document verified.
"""

import html
import json
import re
from pathlib import Path

from flask import Flask, redirect, render_template_string, request, url_for

from analysis.skills_extractor import SKILL_CATEGORIES
from tests_and_eval.skills_gold_eval import GOLD, extract, load_docs

app = Flask(__name__)

PAGE = """<!doctype html><meta charset=utf-8><title>Skills gold · {{ title }}</title>
<style>
 body{font:14px system-ui,sans-serif;margin:0;color:#1c1008;background:#fffdf7}
 header{padding:10px 20px;border-bottom:1px solid #f0dfa8;display:flex;gap:16px;align-items:center}
 a{color:#b45309} main{display:grid;grid-template-columns:1fr 380px;gap:0;height:calc(100vh - 45px)}
 .text{padding:16px 20px;overflow:auto;white-space:pre-wrap;line-height:1.55;border-right:1px solid #f0dfa8}
 mark{background:#fde68a} form{padding:14px 16px;overflow:auto}
 h3{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#7a6248;margin:14px 0 6px}
 label{display:block;padding:2px 0} .ev{color:#a3845c;font-size:12px} .ext{color:#c2410c}
 table{border-collapse:collapse;margin:16px 20px} td,th{padding:4px 10px;border-bottom:1px solid #f0dfa8;text-align:left}
 button{background:#b45309;color:#fff;border:0;border-radius:5px;padding:8px 16px;font-size:14px;cursor:pointer;margin-top:12px}
 textarea{width:100%;height:60px}
</style>{{ body|safe }}"""


def _docs() -> dict[str, dict]:
    return {d["id"]: d for d in load_docs(status=None)}


@app.get("/")
def index():
    docs = _docs().values()
    done = sum(d["status"] == "verified" for d in docs)
    rows = "".join(
        f"<tr><td><a href='{url_for('doc', doc_id=d['id'])}'>{d['id']}</a></td><td>{d['kind']}</td>"
        f"<td>{html.escape(str(d['source'].get('picked_for', d['source'].get('format', ''))))}</td>"
        f"<td>{d['status']}</td><td>{len(d['draft'] or [])}</td></tr>"
        for d in sorted(docs, key=lambda d: (d["status"] == "verified", d["kind"], d["id"]))
    )
    body = (f"<header><b>Skills gold set</b> {done} / {len(docs)} verified</header>"
            f"<table><tr><th>Document</th><th>Kind</th><th>Picked for</th><th>Status</th><th>Draft skills</th></tr>{rows}</table>")
    return render_template_string(PAGE, title="index", body=body)


@app.get("/doc/<doc_id>")
def doc(doc_id):
    d = _docs()[doc_id]
    text = d["_path"].read_text()
    draft = {x["skill"]: x["evidence"] for x in (d["draft"] or [])}
    extracted = extract(d)
    checked = set(d["gold"]) if d["gold"] is not None else set(draft)

    shown = html.escape(text)
    for ev in sorted(set(draft.values()), key=len, reverse=True):
        esc = html.escape(ev)
        shown = re.sub(re.escape(esc).replace(r"\ ", r"\s+"), lambda m: f"<mark>{m.group(0)}</mark>", shown, flags=re.I)

    def box(skill, note=""):
        on = "checked" if skill in checked else ""
        return f"<label><input type=checkbox name=skill value=\"{html.escape(skill)}\" {on}> {html.escape(skill)} {note}</label>"

    parts = ["<h3>LLM draft</h3>"]
    parts += [box(s, f"<span class=ev>— {html.escape(draft[s])}</span>") for s in sorted(draft)]
    extra = sorted(extracted - set(draft))
    parts.append("<h3>Extractor found, not in draft (check the text)</h3>")
    parts += [box(s, "<span class=ext>extractor</span>") for s in extra] or ["<i>none</i>"]
    listed = set(draft) | set(extra)
    for cat, skills in SKILL_CATEGORIES.items():
        rest = [s for s in skills if s not in listed]
        if rest:
            parts.append(f"<details><summary>{html.escape(cat)}</summary>{''.join(box(s) for s in rest)}</details>")
    parts.append(f"<h3>Notes</h3><textarea name=notes>{html.escape(d.get('notes', ''))}</textarea><button>Save as verified</button>")

    body = (f"<header><a href='{url_for('index')}'>← all</a><b>{doc_id}</b> {d['kind']} · {d['status']} · "
            f"{html.escape(str(d['source'].get('title', '')))}</header>"
            f"<main><div class=text>{shown}</div><form method=post>{''.join(parts)}</form></main>")
    return render_template_string(PAGE, title=doc_id, body=body)


@app.post("/doc/<doc_id>")
def save(doc_id):
    d = _docs()[doc_id]
    label_path = GOLD / ("jds" if d["kind"] == "jd" else "resumes") / f"{doc_id}.json"
    label = json.loads(label_path.read_text())
    label["gold"] = sorted(set(request.form.getlist("skill")))
    label["notes"] = request.form.get("notes", "")
    label["status"] = "verified"
    label_path.write_text(json.dumps(label, indent=1, ensure_ascii=False) + "\n")
    remaining = [i for i, x in sorted(_docs().items()) if x["status"] != "verified"]
    return redirect(url_for("doc", doc_id=remaining[0]) if remaining else url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5057, debug=False)
