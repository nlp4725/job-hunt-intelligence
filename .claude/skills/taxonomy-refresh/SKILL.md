---
name: taxonomy-refresh
description: Grow and correct the deterministic skill taxonomy (analysis/skills_extractor.py) from newly collected job descriptions — LLM-extracts skills from a sample, finds vocabulary gaps, pattern gaps and false matches, drafts taxonomy + test changes, verifies them against the whole corpus, and hands them to a human as a PR. Use when asked to refresh, grow or update the skill taxonomy, find skills the extractor misses, fix skill false matches, or when /taxonomy-refresh is invoked (manually or by the biweekly schedule).
---

# Taxonomy Refresh

Production skill extraction is deterministic: regex over `SKILL_TAXONOMY`, the same vocabulary for job descriptions and resumes. This skill uses an LLM **offline** to find what that vocabulary misses, then proposes reviewed changes. The LLM never touches production scoring.

Background, measurements and the schedule plan: `docs/productization_build_plan.md` §8.

## Hard rules

1. **Never merge.** A taxonomy change moves every skill score. The output of a run is a branch and a report; a human merges.
2. **Never run `reextract_job_skills.py --apply` or `recompute_skill_scores.py --apply`** except after the change is merged to `main`.
3. **Pushing and opening the PR:** interactive run → ask the user first. Scheduled run (invoked as `/taxonomy-refresh scheduled`) → push the branch and open the PR without asking; still never merge.
4. **Nothing is proposed without evidence:** every candidate carries a `check_candidate.py` result and tests.
5. **Respect `decisions.json`:** never re-propose a rejected name or alias.

## Defaults — don't ask the user to restate these

| | Default |
|---|---|
| Window | Since `window_end` of the last run in `decisions.json`; first run: last 14 days |
| Sample | 500 JDs, proportional by track, seed 0 |
| Model | `deepseek-v4-pro`, thinking off (~$0.0025/JD off-peak → ~$1.25 per run; peak is 2x) |
| Propose a new skill when | seen in **≥ max(3, 1% of sample) JDs** AND passes the "what counts" rules below |
| Batch size | At most **15 changes per PR** (additions + pattern edits + false-match fixes). Leave the rest in the report as "next run" |

All commands run from the repo root. `S=.claude/skills/taxonomy-refresh`, `PY=./venv/bin/python`.

## Procedure

### 1. Sample and extract
```bash
$PY $S/sample_new_jds.py --n 500
$PY $S/llm_extract.py --sample $S/runs/<date>/sample.json
```
`llm_extract.py` is resumable; if it's interrupted, re-run the same command. It drops any skill whose evidence isn't verbatim in the posting.

### 2. Diff against the taxonomy
```bash
$PY $S/diff_vocab.py --extraction $S/runs/<date>/extraction.json
```
Read all three lists:
- **missing:** vocabulary gaps → candidate new skills.
- **pattern_gaps:** the skill exists but its patterns didn't fire. Check the evidence: the LLM often infers loosely ("hosted in Google's cloud" → GCP). Only a real wording ("supervised fine tuning") becomes a pattern.
- **unconfirmed_tags:** tags the LLM didn't list, with the text the regex fired on → false-match candidates ("R&D" → R, "Predisposition" → Redis).

### 3. Decide what to propose (judgment)
Merge synonyms first ("apis" / "api development" / "api integration" are one candidate; "ml ops" is MLOps). Then apply:

**What counts as a skill**
- A concrete, teachable capability a resume can show: a tool, language, framework, platform, method, or well-defined engineering practice (Distributed Systems, Unit Testing, Data Modeling).
- **Not:** soft skills; generic engineering verbs nearly everyone claims (code review, debugging, problem solving); industries/domains (healthcare); company products; job-title words.
- **Too broad to score** (e.g. "Machine Learning" in ml_ai, "APIs"): **don't add these**, since nearly every JD and resume would match and inflate everyone's score. Record them as `rejected` with reason `too broad to score`.
- **Credentials** (degrees, certifications) belong only in the `Education` category. Note that they currently count toward Skill Match; don't add more until that is fixed.
- **Off-track one-offs** (pre-silicon power analysis in an ml_ai sample) → skip unless the threshold is met *within the track they belong to*.

**Category and group**
- Every new skill goes in exactly one `SKILL_CATEGORIES` list (the import-time check enforces it). Create a new category only for a cluster of ≥3 skills, e.g. "AI Coding Tools": Cursor, Claude Code, Copilot.
- Add to `SKILL_GROUPS` only when having one really covers a request for another. Follow the existing bar: Docker/Kubernetes are deliberately *not* grouped. Explain the reasoning in the PR.

### 4. Draft patterns
Write candidates to `$S/runs/<date>/candidates.json` (format in `check_candidate.py`'s docstring), using the real wordings from the evidence.

Pattern rules:
- **Captured JD text has no line breaks, so words glue at block boundaries** ("development**Experience**", "PYTHONAWSGCP"; see the `extract.js` prerequisite in the plan). A `\b` next to a capitalized word can therefore miss real mentions. Put boundaries where false matches are the bigger risk:
  - **≤4-character tokens** (R, Go, SFT, MCP, RAG): always `\b` on both sides.
  - **Distinctive multi-word phrases** ("distributed systems", "feature store"): no boundaries needed.
  - **Known collisions**: use a lookaround and name the collision in a comment (`java(?!script)`, `(?<!of )recommendations?`).
- Allow plurals and hyphen/space variants: `fine[- ]?tun(?:e|ed|ing)`, `tool[- ]calling`.
- Keep the file's convention: a short comment above any non-obvious entry saying what it fixes and the corpus count that justified it.

### 5. Verify each candidate against the whole corpus
```bash
$PY $S/check_candidate.py --candidates $S/runs/<date>/candidates.json
```
Accept only when:
- `example_failures` is empty.
- **You have read the sampled contexts** and at most 1 in 20 is a false match.
- `inside_word_share` is explained: glued text is acceptable, a substring collision is not.
- `likely_duplicates` is empty, or the co-firing skill really is different (otherwise merge instead of adding).
- For pattern additions to an existing skill, `jds_gained_over_current_patterns` is worth it.

### 6. Edit, test, measure churn
```bash
git checkout -b taxonomy/<date>
```
1. Edit `analysis/skills_extractor.py`: `SKILL_TAXONOMY` entry, `SKILL_CATEGORIES`, and `SKILL_GROUPS` if justified.
2. Add tests to `tests_and_eval/test_skill_extraction.py`: for every change, one should-match test and, where a plausible collision exists, one should-not-match test. The docstring cites the corpus count from `check_candidate.py`, in the style of the existing tests.
3. Run the tests and measure churn:
   ```bash
   $PY -m pytest tests_and_eval/test_skill_extraction.py -q && $PY -m pytest -q
   $PY $S/score_churn.py --base-ref main
   $PY $S/diff_vocab.py --extraction $S/runs/<date>/extraction.json --out $S/runs/<date>/diff_after.json   # coverage after
   ```
4. If churn is surprising (e.g. >10% of a track's scores move, or scores mostly move down), find which change drives it (`biggest_changes`) and reconsider before proposing.

### 7. Report and hand off
Write `$S/runs/<date>/report.md`, which is also the PR body:

```markdown
## Taxonomy refresh <date>
Window <start> → <end> · sample <n> JDs (<per track>) · cost $<off-peak>
Coverage on sample: <before>% → <after>%

### Changes (≤15)
| Change | Skill | Patterns | JDs (ml_ai / pm) | Samples checked | Tests |
|---|---|---|---|---|---|

### Score churn (Skill Match)
<per-track % changed, mean Δ, notable biggest changes>

### Considered and not proposed
| Name | JDs | Reason |

### Next run
<candidates over the batch limit or needing more evidence>
```

Commit with a message listing the changes. Then push and open the PR (following hard rule 3), with `gh pr create --base main --head taxonomy/<date> --body-file $S/runs/<date>/report.md`.

### 8. Record decisions
Update `$S/decisions.json` on the branch, in the same PR:
- **`runs`:** append `{"date", "window_start", "window_end", "sample": n, "cost_usd", "coverage_before", "coverage_after", "pr": url}`.
- **`rejected`:** every candidate you decided against, with a specific reason and any aliases, so it isn't re-proposed.
- **`accepted`:** proposed changes, marked `"status": "proposed"`. The reviewer's merge makes them real; if the reviewer drops one in review, move it to `rejected` with their reason.

## After the PR is merged (human-initiated)
```bash
$PY $S/reextract_job_skills.py            # dry run: rows added/removed per skill
$PY $S/reextract_job_skills.py --apply
$PY $S/recompute_skill_scores.py          # dry run: how many stored scores change
$PY $S/recompute_skill_scores.py --apply
```

## Scheduling
Designed to run every two weeks as a cloud routine once the database is hosted (plan phase 8). Until then it runs locally against the SQLite database; a cloud run can't reach local data. A scheduled run needs:
- DeepSeek key and database access
- `gh` authenticated with permission to push branches and open PRs
- an alert on failure

## Known limitations
- Captured text has lost its line breaks until `extension/content/extract.js` is fixed, so `inside_word_share` is inflated by glued words, and boundary choices are compromises.
- Taxonomy entries are deliberately just **skill name → patterns** (plus categories and groups). Don't add per-entry fields.
- The LLM list is discovery, not ground truth: individual counts of 2–3 JDs are noise, and the theme-level signal is what to trust.
