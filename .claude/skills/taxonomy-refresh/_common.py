"""Shared helpers for the taxonomy-refresh scripts.

Every script in this directory is run by path from the repo root, e.g.
`./venv/bin/python .claude/skills/taxonomy-refresh/diff_vocab.py ...`, so this
module puts the repo root on sys.path and loads the repo's .env explicitly —
load_dotenv() with no argument searches from the script's own directory and
would miss it.

Database access goes through db.session like the rest of the codebase, so
these scripts follow the database wherever it moves (SQLite today, hosted
Postgres when the refresh runs as a cloud routine).
"""

import hashlib
import json
import re
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]
RUNS_DIR = SKILL_DIR / "runs"
CACHE_DIR = RUNS_DIR / "_cache"
DECISIONS_PATH = SKILL_DIR / "decisions.json"
TAXONOMY_RELPATH = "analysis/skills_extractor.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")


def norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def squash(text: str) -> str:
    """Lowercase with ALL whitespace removed. Captured raw_text has lost its
    line breaks (words glue at block boundaries), while LLM evidence usually
    restores a space there — comparing squashed forms tolerates both."""
    return re.sub(r"\s+", "", text).lower()


def read_json(path) -> dict | list:
    return json.loads(Path(path).read_text())


def write_json(path, data) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str))
    return path


def today_run_dir() -> Path:
    return RUNS_DIR / datetime.now().strftime("%Y-%m-%d")


def load_decisions() -> dict:
    if not DECISIONS_PATH.exists():
        return {"runs": [], "accepted": [], "rejected": []}
    return read_json(DECISIONS_PATH)


def rejected_names(decisions: dict) -> set[str]:
    names = set()
    for entry in decisions.get("rejected", []):
        names.add(norm_name(entry["name"]))
        names.update(norm_name(a) for a in entry.get("aliases", []))
    return names


def last_window_end() -> datetime | None:
    runs = load_decisions().get("runs", [])
    if not runs:
        return None
    return datetime.fromisoformat(runs[-1]["window_end"])


def taxonomy_fingerprint() -> str:
    return hashlib.sha256((REPO_ROOT / TAXONOMY_RELPATH).read_bytes()).hexdigest()[:12]


def load_taxonomy_module(ref: str | None = None) -> types.ModuleType:
    """The working-tree taxonomy (ref=None), or the version committed at a git
    ref — executed into a throwaway module so both can be compared in one
    process without touching the working tree."""
    if ref is None:
        import analysis.skills_extractor as module

        return module
    if ref.startswith("file:"):
        # A saved copy of the taxonomy file — the "before" snapshot when the
        # working tree already has other uncommitted taxonomy edits.
        path = Path(ref[len("file:"):])
        module = types.ModuleType(f"skills_extractor_from_{path.stem}")
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
        return module
    source = subprocess.run(
        ["git", "show", f"{ref}:{TAXONOMY_RELPATH}"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    module = types.ModuleType(f"skills_extractor_at_{ref}")
    exec(compile(source, f"{ref}:{TAXONOMY_RELPATH}", "exec"), module.__dict__)
    return module


def load_corpus(track: str | None = None, since: datetime | None = None, include_all: bool = False):
    """Jobs with JD text. By default excludes confirmed duplicates and
    off-track rows (is_relevant=False), so a skill doesn't look more common
    just because one posting was reposted five times."""
    from db.models import Job
    from db.session import SessionLocal

    session = SessionLocal()
    try:
        query = session.query(Job.id, Job.track, Job.title, Job.first_seen_at, Job.raw_text).filter(
            Job.raw_text.isnot(None), Job.raw_text != ""
        )
        if not include_all:
            query = query.filter(Job.is_relevant.is_(True), Job.duplicate_of_job_id.is_(None))
        if track:
            query = query.filter(Job.track == track)
        if since:
            query = query.filter(Job.first_seen_at >= since)
        return query.all()
    finally:
        session.close()


_worker_taxonomy = None


def _init_tag_worker(ref: str | None) -> None:
    global _worker_taxonomy
    _worker_taxonomy = load_taxonomy_module(ref)


def _tag_one(text: str) -> list[str]:
    # The same normalize() db/job_writer.py applies at capture, so corpus-wide
    # tags agree with stored job_skills.
    from analysis.text_normalize import normalize

    return _worker_taxonomy.extract_skills(normalize(text))


def tag_texts(texts: list[str], ref: str | None = None, workers: int | None = None) -> list[list[str]]:
    """extract_skills over many texts, across all CPU cores. A single-process
    pass of 158 patterns over the ~14K-JD corpus takes minutes; every
    corpus-wide script needs one."""
    import os
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=workers or os.cpu_count(),
                             initializer=_init_tag_worker, initargs=(ref,)) as pool:
        return list(pool.map(_tag_one, texts, chunksize=64))


def match_context(text: str, start: int, end: int, width: int = 40) -> str:
    return "…" + text[max(0, start - width):end + width].replace("\n", " ") + "…"
