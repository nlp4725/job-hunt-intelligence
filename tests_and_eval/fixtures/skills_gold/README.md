# Skills gold set

Human-verified skill labels for measuring the deterministic skill pipeline (`normalize` → `extract_skills`, and `resume_to_text` for resumes).

| Folder | Contents |
|---|---|
| `jds/` | 50 ml_ai job descriptions (`<id>.txt`, anonymised) + labels (`<id>.json`) |
| `resumes/` | Anonymised resumes (`<slug>.txt`) + labels |
| `baseline.json` | Precision / recall / score error the gate compares against |

## Workflow

```bash
./venv/bin/python -m tests_and_eval.skills_gold_build select-jds   # pick JDs (never overwrites labels)
./venv/bin/python -m tests_and_eval.skills_gold_build resumes      # anonymise resume sources
./venv/bin/python -m tests_and_eval.skills_gold_build draft        # LLM draft labels
./venv/bin/python -m tests_and_eval.skills_gold_review             # confirm in the browser: http://127.0.0.1:5057
./venv/bin/python -m tests_and_eval.skills_gold_eval --update-baseline
./venv/bin/python -m pytest tests_and_eval/test_skills_gold.py
```

## What counts as a gold skill

- **Only skills in the taxonomy.** This set measures the extraction pipeline, not taxonomy coverage (the `taxonomy-refresh` skill measures coverage).
- A skill counts when the document **uses it as a technology, tool, method, practice or credential anywhere**: requirements, responsibilities, the company's tech stack, or a resume's skills list, experience, projects and education. This matches the extractor, which reads the whole document on purpose.
- It does **not** count when the same letters mean something else: "R&D" is not R; "your CV" is not Computer Vision; "spark innovation" is not Spark; "embedding AI into workflows" is not Embeddings; ordinary "recommendations" is not Recommendation Systems; URLs and usernames are not skills.
- Degrees count as Master's Degree / PhD when mentioned.

## Label file

```json
{"id": "jd-15049", "kind": "jd", "text_file": "jd-15049.txt",
 "source": {"job_id": 15049, "title": "...", "picked_for": "random_since_sep1"},
 "draft": [{"skill": "Python", "evidence": "..."}],
 "gold": ["Python", "..."], "status": "verified", "notes": ""}
```

`draft` is the LLM's suggestion; only `gold` on `verified` documents is used.

## Known gaps

- All four resumes are versions of one person's resume. Add anonymised resumes from other people and other fields.
- PDF resumes are stored as extracted text until `resume_to_text` supports PDF; after that, keep the original PDF next to its label.
