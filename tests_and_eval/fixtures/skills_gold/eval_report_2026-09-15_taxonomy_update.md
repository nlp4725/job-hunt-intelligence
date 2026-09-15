# Skill extraction eval: 2026-09-15, after the taxonomy update

The working-tree taxonomy (`analysis/skills_extractor.py`) scored against human-labelled skills and compared with the taxonomy at HEAD (`5d6fd51`). The only difference between the two is the new Stakeholder Management patterns.

- **JDs:** 50 documents, all `verified` gold, pipeline `extract_skills(normalize(text))`.
- **Resumes:** the 30 files in `docs/test_resume/`, with labels from `test_resume_skill_labels.md`. Those labels are a Claude draft that no person has reviewed yet. Pipeline `extract_skills(resume_to_text(file).text)`. `test-resume-26` (`resume_1.jpg`) is skipped because `resume_to_text` doesn't accept images, so resume figures cover 29 documents.
- **Skill Match score error:** every JD × resume pair (50 × 29 = 1,450), scored once from gold skills and once from extracted skills, both with the current `SKILL_GROUPS`.

Script: the session scratchpad's `rerun_eval.py`, not committed. It does not touch the gold labels.

## 1. Headline

| | Taxonomy | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| **JDs** (50) | HEAD | 504 | 30 | 128 | 0.944 | 0.797 | 0.864 |
| | **updated** | 514 | 36 | 118 | **0.935** | **0.813** | **0.870** |
| **Resumes** (29) | HEAD | 531 | 22 | 76 | 0.960 | 0.875 | 0.916 |
| | **updated** | 532 | 22 | 75 | **0.960** | **0.876** | **0.916** |

| Skill Match score error | MAE | Mean signed | Exact match |
|---|---|---|---|
| HEAD | 0.328 | +0.105 | 71% |
| **updated** | **0.304** | +0.071 | 72% |

The update raises JD recall by 1.6 points and costs 0.9 points of precision. Score error falls by 7%.

## 2. What the Stakeholder Management change did

Gold currently has Stakeholder Management on 13 JDs and 1 resume (`test-resume-10`).

- **Now found (FN → TP), 10:** jd-11909, jd-1702, jd-2200, jd-4060, jd-531, jd-6092, jd-6457, jd-6675, jd-6778, jd-7176, plus test-resume-10.
- **Still missed, 3:** jd-4449 (never says "stakeholder"), jd-6670 and jd-8000.
- **New false positives, 6:**

| JD | Status |
|---|---|
| jd-16324, jd-16736, jd-17082, jd-18064, jd-6280 | Proposed additions in `stakeholder_recheck_2026-09-15.md` that were never applied. If they are approved, these become TPs. |
| jd-14169 | **Real pipeline error.** The text says the role "does not own … stakeholder relationships". |

`stakeholder_recheck_2026-09-15.md` is partly out of date: 7 of its 13 "proposed" JDs (jd-2200, 4060, 6092, 6457, 6675, 6778, 7176) already have the label in gold. Still pending: jd-16324, jd-16736, jd-17082, jd-18064, jd-5773, jd-6280.

If all six pending additions are applied: JD precision 0.944, recall 0.813, F1 **0.874**; score MAE 0.312.

## 3. Remaining errors (updated taxonomy)

### JDs

| Pipeline only (FP) | n | | Missed (FN) | n |
|---|---|---|---|---|
| A/B Testing | 8 | | Master's Degree | 9 |
| Stakeholder Management | 6 | | API Design & Development | 9 |
| LLM Evaluation | 3 | | Model Monitoring | 8 |
| Generative AI, Copilot, JavaScript, Git, Responsible AI | 2 each | | Agents | 7 |
| | | | Tool Use / Function Calling, OpenAI API | 6 each |
| | | | Prompt Engineering, Transformers | 5 each |
| | | | Software Testing, Anthropic API, Data Pipelines, Distributed Systems | 4 each |

Lowest recall: jd-17198 3/7, jd-11909 5/11, jd-15386 6/13, jd-15331 4/8, jd-15646 7/15.

### Resumes

| Pipeline only (FP) | n | | Missed (FN) | n |
|---|---|---|---|---|
| **Git** | **17** | | **Master's Degree** | **16** |
| Time Series Forecasting, ETL, LLM Evaluation, R, PyTorch | 1 each | | API Design & Development | 8 |
| | | | Data Pipelines | 7 |
| | | | Data Visualization, Statistics, Transformers | 4 each |

The two large resume errors both come from how the resume text is written:

- **Git ×17:** almost all come from the contact line `GitHub: akarim-risk`. That is a username, not a skill (README rule). The `github` variant matches it. One case (`test-resume-09`, "GitHub Actions") is a labelling miss instead, because the JD gold counts GitHub Actions as Git.
- **Master's Degree ×16:** degrees written as "M.S. Data Science" or "M.Sc. Computer Engineering". The variants only cover `master's degree`, `ms degree` and `msc`.

## 4. Suggested next steps

1. Decide on the 6 pending Stakeholder Management JD additions, then run `skills_gold_eval --update-baseline`.
2. **Master's Degree:** add "M.S." / "M.Sc." / "Masters" variants. There are 25 misses across both sets, the largest single gap.
3. **Git:** don't count `GitHub:` or `github.com/…` profile links.
4. **Stakeholder Management:** exclude negations like "does not own … stakeholder relationships" (jd-14169).
5. **A/B Testing:** narrow the bare `experimentation` variant (8 FPs, unchanged since the last report).
6. Have a person review the 30 test-resume labels, and add image support (or OCR) for `resume_1.jpg`.
