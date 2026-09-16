# Seniority gold set

Human-confirmed seniority labels for tuning and testing `judge/seniority_level.py`.

| File | Contents |
|---|---|
| `selection.json` | The 60 jobs: 10 per old local seniority score 0–5, 5 `tune` + 5 `test` in each |
| `jds/jd-<id>.txt` | Posting text as the prompt sees it (`judge.seniority_fit.format_posting`) |
| `labels/jd-<id>.json` | Draft label, then the confirmed `gold` label |

**Tune vs test.** The prompt is changed only while looking at `tune` results. `test` is scored once, at the end, with the final prompt. Looking at `test` failures and changing the prompt again would turn it into a second tuning set.

**Split and target (decided 2026-09-16).** 80/20, stratified by label: `tune` is the training set, `test` is scored once at the end. A run is correct only when level and `is_contract` both match; the target is ≥90% of runs on training before the test run.

## How to label

A label describes the **job**, not any candidate. Judge by the level of responsibility the posting describes, not by title strings. Five levels (decided 2026-09-16):

| Level | Years (half-open) | What the role actually expects |
|---|---|---|
| `intern` | — | An internship or co-op, whatever the duties |
| `entry` | [0, 2) | New grad / entry. Well-defined tasks under supervision; posting caps experience or targets recent graduates |
| `mid_senior` | [2, 5) | Works independently, owns features end to end, collaborates across teams. Ships, doesn't lead |
| `senior` | [5, 9) | Owns whole projects, makes technical decisions; may mentor, set a team's technical direction or manage engineers |
| `staff_principal` | [9, ∞) | Staff / principal / director. Drives architecture across teams or the org, sets strategy, or manages managers |

**One yes/no flag**, decided on its own. Still give the level when the posting shows one.
- `is_contract`: the role itself is contract / temporary / fixed-term / hourly or part-time freelance. "Contract-to-hire" counts. Boilerplate ("employees and contractors", hourly pay for a full-time job) is not.

**Agency postings are not labelled here.** They are filtered out before screening (`judge/agency_blocklist.py`), so they were moved to `excluded_agency/` (decided 2026-09-16).

**Rules, in order**
1. An internship or co-op → `intern`.
2. Stated years win over title. Use the half-open bands.
3. "X+ years" → X. A range ("3–7 years") → the minimum. Different years for different degrees ("BS + 5 or MS + 3") → the lowest path.
4. No years → infer from responsibilities; `inferred: true`.
5. Title and responsibilities disagree → trust responsibilities and say so in `note`.
6. Managing engineers → at least `senior`; managing managers → `staff_principal`.
7. Nothing inferable → `level: null`.

## Label file

```json
{"job_id": 12769, "split": "tune",
 "draft": {"level": "staff_principal", "is_contract": true, "years_required": 12, "inferred": false,
           "evidence": ["verbatim fragment under 15 words", "..."], "note": "why, in one or two sentences"},
 "gold": null, "status": "draft"}
```

Every `evidence` fragment must appear verbatim in `jds/jd-<id>.txt`. `gold` has the same shape as `draft` and is filled only by a human on review; only `status: "confirmed"` labels are used.
