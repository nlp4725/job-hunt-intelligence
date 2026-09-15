# Resume + JD skill pipeline: what to build

Status: plan, 2026-09-15. Built test-first; see §6 for the order.

**Progress (2026-09-15):** §6 steps 1–12 are built and green (`test_text_normalize.py`, `test_skill_extraction.py`, `test_resume_text.py`, `test_resume_pii.py`, `test_resume_pipeline.py`). §3's JD-side `normalize()` runs in `db/job_writer.py`, `skill_match_score` and the taxonomy-refresh tagger (`test_jd_normalize.py`). Where the build differs from the sketch below:

- Step 12 runs on 29 real resume PDFs kept on Nasi's machine (`tests_and_eval/fixtures/skills_gold/resumes/`, gitignored): no email, phone or profile link survives redaction on any of them, and every one yields skills. The test skips on a clone without them. Resume files and resume labels are never committed.
- Layer 3 also recognises markdown and bold headings (`## SUMMARY`, `**SKILLS**`).
- `normalize()` on the 15,186 stored JDs changes tags on 78, all additions (Event-Driven Architecture 21, Go-to-Market 12, Fine-tuning 11, scikit-learn 9, …; R on 3, not yet checked). Stored `job_skills` are **not** re-extracted yet: `reextract_job_skills.py --apply` would also apply the taxonomy changes from `5d6fd51` that were never applied to stored rows (+12,382 / −2,853 rows), which needs its own review.

- `ResumeDocument` holds `text` only. `lines` (layout) will be added when project chunking needs it.
- `redact_pii(text, identity)` takes text. Layer 3 detects a heading as a line that is only a known section name, so it needs no layout.
- Two-column PDFs are handled by finding a vertical gap in the middle half of the page that no word crosses, then reading each column in turn. It found no false splits on 8 real single-column resume PDFs.
- Changing spaces in patterns to `\s+` altered 0 of 15,182 stored JD tag sets, because only 31 JDs contain a newline (the capture bug in §2.3).
- On 8 real resume PDFs, no name, email, phone or link survives redaction. Layer 3 cuts at `SUMMARY` on every one.

**Goal.** Users upload a resume and get their own Skill Match against the shared board. Skill extraction stays **deterministic** (regex over `SKILL_TAXONOMY`, no LLM) and must work on most real resumes. Expertise Match is out of scope here and will be designed later.

> `productization_build_plan.md` §3 covers where this fits: resume upload, the user profile (seniority target) and per-user screening. This document is the detailed spec for resume parsing and skill extraction.

---

## 1. Design rules

1. **One extractor and one taxonomy for both JDs and resumes.** Skill Match is set overlap, so JDs and resumes have to be tagged with the same canonical names. Two extractors would drift apart without anyone noticing.
2. **Separate adapters in front of the extractor.** JDs and resumes arrive as different kinds of text (browser text vs PDF/DOCX). Each has its own `to_text`, and both go through the same `normalize()`.
3. **Extract skills from the whole document, never from detected sections.** A mis-detected heading can silently drop real skills to zero (measured on JDs; see `analysis/skill_match.py`). Layout is recorded, but skill extraction does not depend on it.
4. **PII redaction must not depend on layout.** Layout-based removal is only the last of three layers, so a missed heading cannot leak contact details.
5. **Extract once, match many times.** Skills are extracted once per JD and once per resume version, then stored. Matching compares stored skill sets and never re-reads text.

```
JD:      extension raw_text ─────────────► normalize() ─┐
                                                        ├─► extract_skills() ─► skill set (stored)
Resume:  file ─► resume_to_text() ─► lines ─► normalize() ┘
                                        └─► redact_pii() ─► redacted text (for anything leaving the extractor)

Skill Match: skill_match_from_skills(job_skills, resume_skills)
```

---

## 2. Shared pieces

### 2.1 `analysis/text_normalize.py` → `normalize(text) -> str`

Only changes how characters are encoded. Never adds or removes words.

| Step | Example in → out |
|---|---|
| Unicode NFKC | `ﬁne-tuning` → `fine-tuning`; non-breaking space → space; `ＰＹＴＨＯＮ` → `PYTHON` |
| Hyphen look-alikes → `-` | `fine‑tuning` (U+2011) → `fine-tuning`; soft hyphen (U+00AD) removed |
| Hyphen at a line break: join the lines, **keep the hyphen** | `end-to-\nend` → `end-to-end` (dropping the hyphen would produce `end-toend`) |
| Collapse spaces and tabs, **keep newlines** | `Python   \t SQL` → `Python SQL` |

Measured: on 4,393 September JDs, a prototype adds 16 correct tags (e.g. Event-Driven Architecture ×8, scikit-learn ×3) and removes none.

### 2.2 `analysis/skills_extractor.py`: multi-word variants tolerate line breaks

103 of 261 variants contain a literal space, so `"Prompt\nEngineering"` misses today. Real resume PDFs wrap lines inside skill lists (`"Function\nCalling"` in Nasi's resume). Fix: when compiling patterns, a space in a variant matches `\s+`.

### 2.3 Not fixed by normalize: glued words in JDs

`"modelingSQL"`, `"PYTHONJAVAGIT"` come from `.textContent` in `extension/content/extract.js`. Splitting on case changes would break `PySpark` and `LangChain`, so the only fix is upstream: preserve line breaks at capture (prerequisite 1 in the productization plan).

---

## 3. JD side

| Build | Where |
|---|---|
| Run `normalize()` before `extract_skills()` when writing `job_skills` | `db/job_writer.py:136` |
| Re-extract `job_skills` for the corpus and report churn | one-off script, same shape as `taxonomy-refresh/score_churn.py` |
| Preserve newlines at capture | `extension/content/extract.js` (separate change) |

---

## 4. Resume side: new package `resume/`

(Not `profile/`, which would shadow Python's stdlib `profile` module.)

### 4.1 `resume/resume_text.py` → `resume_to_text(filename, data: bytes) -> ResumeDocument`

```python
@dataclass
class ResumeLine:
    text: str
    page: int
    x0: float          # left edge: two-column detection, indentation
    top: float
    font_size: float
    bold: bool

@dataclass
class ResumeDocument:
    lines: list[ResumeLine]
    text: str           # lines in reading order, normalized

class UnsupportedResume(Exception): ...   # unknown type, or a PDF with no text layer (scan)
```

- `.pdf` with **pdfplumber** (MIT); `.docx` with python-docx (heading styles and bold runs); `.md` / `.txt` read as they are.
- The parser is hidden behind this interface, so Docling can be swapped in later if it does better on the resume test set (§7).
- Two-column PDFs: lines are grouped by column before reading top to bottom.
- A scanned PDF raises `UnsupportedResume` with a user-facing message. No OCR.

### 4.2 `resume/pii.py` → `redact_pii(doc_or_text, identity: KnownIdentity) -> str`

Three layers, each enough on its own for what it covers:

1. **Known identity** from the login token (name, email): exact, case-insensitive replacement everywhere.
2. **Patterns anywhere**: emails, phone numbers (including a bare 10-digit number and a label/number split across lines, e.g. `"Phone: \n5551234567"`), URLs, LinkedIn/GitHub handles.
3. **Layout bonus**: drop everything above the first recognised section heading (SUMMARY, SKILLS, EXPERIENCE, EDUCATION, PROJECTS…).

Kept on purpose: employer and school names, which later matching will need.

### 4.3 `resume/pipeline.py` → `process_resume(filename, data, identity) -> ProcessedResume`

```python
@dataclass
class ProcessedResume:
    text: str             # normalized, NOT redacted: stored encrypted, never logged
    redacted_text: str    # the only text allowed into logs, LLM calls, embeddings
    skills: list[str]     # extract_skills(text)
    lines: list[ResumeLine]
```

This is the single entry point for the upload endpoint and for tests.

---

## 5. Matching

`analysis/skill_match.py`:

- New `skill_match_from_skills(job_skills: set[str], resume_skills: set[str]) -> dict`, with the same result shape and banding as today.
- `skill_match_score(resume_text, job_text)` becomes a thin wrapper around it, so current callers keep working.

---

## 6. Build order (test-first, one behaviour at a time)

| # | Behaviour under test | Test file |
|---|---|---|
| 1 | A skill written with a non-breaking hyphen is found after normalize | `test_text_normalize.py` |
| 2 | PDF ligatures and odd spaces don't hide skills | `test_text_normalize.py` |
| 3 | A hyphen at a line break joins the lines and keeps the hyphen | `test_text_normalize.py` |
| 4 | A multi-word skill split across lines is found | `test_skill_extraction.py` |
| 5 | Stored-set matching gives the same result as text matching | `test_skill_extraction.py` |
| 6 | `.txt`/`.md` resume → text; unknown type → `UnsupportedResume` | `test_resume_text.py` |
| 7 | `.docx` resume → text with the same skills as its text source | `test_resume_text.py` |
| 8 | `.pdf` resume → same skills; a PDF with no text → `UnsupportedResume` | `test_resume_text.py` |
| 9 | Two-column PDF: skills in both columns found, phrases not interleaved | `test_resume_text.py` |
| 10 | Redaction removes known name/email, pattern PII, and the header block | `test_resume_pii.py` |
| 11 | Redaction still removes pattern PII when no heading is detected | `test_resume_pii.py` |
| 12 | `process_resume` end to end on each test resume: expected skills present, no PII in `redacted_text` | `test_resume_pipeline.py` |

**Test resumes:** real resume PDFs stay on Nasi's machine under `tests_and_eval/fixtures/skills_gold/resumes/`, with draft skill labels in `test_resume_skill_labels.md`; both are gitignored and never committed. The committed suite uses synthetic resumes rendered as PDF and DOCX in the tests (fpdf2 / python-docx) for format handling.

---

## 7. Deferred

- **Storage:** `resumes` table (per user, versioned, encrypted original file) and stored resume skills. Waits for Postgres + Alembic (productization phase 1).
- **Upload endpoint** and a "confirm your skills" UI where users add or remove extracted skills.
- **Docling vs pdfplumber:** compare on the resume test set (headings found, two-column order, time per page). Docling runs ML models, so it would need a background job.
- **Project chunking** from `ResumeLine` layout, together with the new Expertise Match design.
- **Resume recall/precision baseline** on the labelled set, with resume-style `should_match` examples added to the taxonomy tests.
