"""Build the skills gold set: pick JDs, anonymise resumes, draft labels with an LLM.

LLM labels are only DRAFTS. A human confirms each document in
skills_gold_review.py; the eval counts only documents with status "verified".
Existing label files are never overwritten.

    ./venv/bin/python -m tests_and_eval.skills_gold_build select-jds
    ./venv/bin/python -m tests_and_eval.skills_gold_build resumes
    ./venv/bin/python -m tests_and_eval.skills_gold_build draft
"""

import argparse
import json
import random
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLD = Path(__file__).resolve().parent / "fixtures" / "skills_gold"
JD_DIR, RESUME_DIR = GOLD / "jds", GOLD / "resumes"

# Known false-match traps, each included on purpose: pattern -> JDs to pick.
TRICKY = {
    "r_and_d": (r"\bR\s*&\s*D\b", 2),
    "resume_cv": (r"\b(?:your|upload|resume/)\s*CV\b", 2),
    "spark_verb": (r"\bspark (?:innovation|creativity|ideas)", 2),
    "embedding_verb": (r"\bembedding (?:AI|these|with|directly)", 2),
    "julia": (r"\bJulia\b", 1),
    "redis_substring": (r"[a-z]redis", 1),
}
GLUED_CAPTURE_START = datetime(2026, 9, 1)  # captures from here on mostly lost their line breaks

# Distinct versions only; the rest are near-copies. Resumes from other people
# (anonymised) should be added here as they become available.
RESUME_SOURCES = {
    "resume-ai-engineer": "nasi_resume_ai_v3.md",
    "resume-ml-governed-v2": "old_resume/nasi_resume_ai_ml_v2.md",
    "resume-ml-governed-v1": "old_resume/august_150_0%/nasi_v1.pdf",
    "resume-product-manager": "old_resume/Nasi_pm.pdf",
}

_PII = [
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "candidate@example.com"),
    (r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w-]+/?", "linkedin.com/in/candidate"),
    (r"/in/[\w-]+/?", "/in/candidate"),
    (r"(?:https?://)?github\.com/[\w-]+", "github.com/candidate"),
    (r"\b\d{3}[-. ]?\d{3}[-. ]?\d{4}\b", "000-000-0000"),
]


def _identity() -> tuple[str, list[str]]:
    """The resume owner's full name and other identifying terms (phone,
    handles). Read from .env (GOLD_PII_NAME, GOLD_PII_TERMS comma-separated),
    not written here, so this file can be committed without them."""
    from dotenv import load_dotenv
    import os

    load_dotenv(REPO / ".env")
    name = os.environ.get("GOLD_PII_NAME", "").strip()
    terms = [t.strip() for t in os.environ.get("GOLD_PII_TERMS", "").split(",") if t.strip()]
    if not name:
        raise SystemExit("Set GOLD_PII_NAME (and GOLD_PII_TERMS) in .env before anonymising resumes.")
    return name, terms


def anonymise(text: str) -> str:
    name, terms = _identity()
    parts = name.split()
    text = re.sub(r"\s+".join(map(re.escape, parts)) + r"(?:,\s*PhD)?", "Candidate A", text)
    for pattern, replacement in _PII:
        text = re.sub(pattern, replacement, text)
    for part in parts:
        text = re.sub(rf"\b{re.escape(part)}\b", "Candidate", text)
    for term in terms:
        text = re.sub(rf"\b{re.escape(term)}\b", "candidate", text)
    leftover = re.compile("|".join(map(re.escape, [*parts, *terms])), re.I).search(text)
    if leftover:
        raise ValueError(f"PII left after anonymising: {leftover.group(0)!r}")
    return text


def _write_label(path: Path, label: dict) -> None:
    path.write_text(json.dumps(label, indent=1, ensure_ascii=False) + "\n")


def select_jds(seed: int, n_old: int, n_new: int) -> None:
    from db.models import Job, ScreeningResult
    from db.session import SessionLocal

    session = SessionLocal()
    try:
        rows = (session.query(Job.id, Job.title, Job.first_seen_at, Job.raw_text)
                .join(ScreeningResult, ScreeningResult.job_id == Job.id)
                .filter(Job.track == "ml_ai", Job.is_relevant.is_(True),
                        Job.duplicate_of_job_id.is_(None), Job.raw_text.isnot(None))
                .order_by(Job.id).all())
    finally:
        session.close()

    rng = random.Random(seed)
    picked: dict[int, tuple] = {}
    for label, (pattern, k) in TRICKY.items():
        hits = [r for r in rows if r.id not in picked and re.search(pattern, r.raw_text)]
        for r in rng.sample(hits, min(k, len(hits))):
            picked[r.id] = (r, label)
    old = [r for r in rows if r.id not in picked and r.first_seen_at < GLUED_CAPTURE_START]
    new = [r for r in rows if r.id not in picked and r.first_seen_at >= GLUED_CAPTURE_START]
    for r in rng.sample(old, n_old):
        picked[r.id] = (r, "random_before_sep1")
    for r in rng.sample(new, n_new):
        picked[r.id] = (r, "random_since_sep1")

    JD_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for r, label in picked.values():
        doc_id = f"jd-{r.id}"
        if (JD_DIR / f"{doc_id}.json").exists():
            continue
        (JD_DIR / f"{doc_id}.txt").write_text(anonymise(r.raw_text))
        _write_label(JD_DIR / f"{doc_id}.json", {
            "id": doc_id, "kind": "jd", "text_file": f"{doc_id}.txt",
            "source": {"job_id": r.id, "title": r.title, "first_seen_at": r.first_seen_at.isoformat(), "picked_for": label},
            "draft": None, "gold": None, "status": "draft", "notes": "",
        })
        written += 1
    print(f"picked {len(picked)} JDs, wrote {written} new")


def build_resumes() -> None:
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    for slug, rel in RESUME_SOURCES.items():
        if (RESUME_DIR / f"{slug}.json").exists():
            continue
        src = REPO / rel
        if src.suffix == ".pdf":
            text = subprocess.run(["pdftotext", str(src), "-"], capture_output=True, text=True, check=True).stdout
        else:
            text = src.read_text()
        (RESUME_DIR / f"{slug}.txt").write_text(anonymise(text))
        _write_label(RESUME_DIR / f"{slug}.json", {
            "id": slug, "kind": "resume", "text_file": f"{slug}.txt",
            "source": {"format": src.suffix.lstrip("."), "anonymised": True},
            "draft": None, "gold": None, "status": "draft", "notes": "",
        })
        print(f"wrote {slug}")


RULES = """Label which skills from the ALLOWED list this {what} genuinely refers to.

Include a skill when the text uses it as a technology, tool, method, practice or credential ANYWHERE in the
document ({where}). Use the exact ALLOWED name.

Do NOT include a skill when the same letters mean something else, e.g.:
- "R&D" or an id like "R-102832" is not R; "your CV" / "Resume/CV" is not Computer Vision
- "spark innovation" is not Spark; "embedding AI into workflows" is not Embeddings
- ordinary "recommendations" is not Recommendation Systems; a URL or username is not a skill
Degrees: include Master's Degree / PhD when that degree is mentioned.

For each skill give evidence: a fragment copied EXACTLY from the text, under 12 words.

ALLOWED skills by category:
{allowed}"""


def draft_labels(workers: int) -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_deepseek import ChatDeepSeek
    from pydantic import BaseModel

    from analysis.skills_extractor import SKILL_CATEGORIES

    names = {n for group in SKILL_CATEGORIES.values() for n in group}
    allowed = "\n".join(f"- {cat}: {', '.join(group)}" for cat, group in SKILL_CATEGORIES.items())

    # Free-text name, filtered against the taxonomy below: a strict Literal
    # makes one off-list answer ("Machine Learning") fail the whole document.
    class Label(BaseModel):
        skill: str
        evidence: str

    class Labels(BaseModel):
        skills: list[Label]

    llm = ChatDeepSeek(model="deepseek-v4-pro", extra_body={"thinking": {"type": "disabled"}}).with_structured_output(Labels)
    prompts = {
        "jd": SystemMessage(RULES.format(what="job description", where="requirements, responsibilities, or the company's own tech stack", allowed=allowed)),
        "resume": SystemMessage(RULES.format(what="resume", where="skills list, experience, projects, education", allowed=allowed)),
    }

    def squash(s: str) -> str:
        return re.sub(r"\s+", "", s).lower()

    def run(path: Path):
        label = json.loads(path.read_text())
        text = (path.parent / label["text_file"]).read_text()
        for attempt in range(3):
            try:
                out = llm.invoke([prompts[label["kind"]], HumanMessage(text[:14000])])
                break
            except Exception as exc:
                if attempt == 2:
                    return label["id"], f"FAILED ({type(exc).__name__}); re-run draft to retry"
                time.sleep(2 ** attempt)
        haystack = squash(text)
        seen, draft = set(), []
        for item in out.skills:
            if item.skill in names and item.skill not in seen and squash(item.evidence) in haystack:
                seen.add(item.skill)
                draft.append({"skill": item.skill, "evidence": item.evidence})
        label["draft"] = draft
        _write_label(path, label)
        return label["id"], len(draft)

    todo = [p for d in (JD_DIR, RESUME_DIR) for p in sorted(d.glob("*.json")) if json.loads(p.read_text())["draft"] is None]
    print(f"drafting {len(todo)} documents")
    with ThreadPoolExecutor(workers) as pool:
        for doc_id, n in pool.map(run, todo):
            print(f"  {doc_id}: {n} skills")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    jds = sub.add_parser("select-jds")
    jds.add_argument("--seed", type=int, default=15)
    jds.add_argument("--old", type=int, default=20)
    jds.add_argument("--new", type=int, default=20)
    sub.add_parser("resumes")
    draft = sub.add_parser("draft")
    draft.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    if args.cmd == "select-jds":
        select_jds(args.seed, args.old, args.new)
    elif args.cmd == "resumes":
        build_resumes()
    else:
        draft_labels(args.workers)


if __name__ == "__main__":
    main()
