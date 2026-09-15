# Stakeholder Management re-check after the 2026-09-15 taxonomy update

The taxonomy update changed no skill names or categories. It changed two things:

1. **Stakeholder Management patterns.** The skill now covers managing or working with stakeholders, not only the exact phrase "stakeholder management".
2. **LLM Eval/Observability Tool group split** into LLM Eval Framework (Ragas, DeepEval) and LLM Observability Platform (LangSmith, Arize). Groups only affect Skill Match scoring, so no label changes.

Only Stakeholder Management needed a re-check. Every passage mentioning "stakeholder" in the 50 JDs and 30 test resumes was read by Claude. The deterministic pipeline was not used.

**Rule applied.** The skill counts when the role or candidate works with, partners with, collaborates with, engages, aligns, influences or manages stakeholders, or when the text names stakeholder mapping, relationships, partnership or leadership. It does not count for only communicating or explaining things to stakeholders, for company boilerplate, or when the text says the role does not own stakeholder relationships.

## JDs: proposed changes (not applied)

All 50 JDs are `verified`, so their `gold` lists were left unchanged. Approve these to apply them.

| | JDs |
|---|---|
| Add the label | 13 |
| Already labelled, still correct | 5 |
| No label, and none needed | 32 |

Gold Stakeholder Management would go from 5 to 18 of 50 JDs.

### Add Stakeholder Management (13)

| JD | Evidence |
|---|---|
| jd-16324 | Partner with product managers and stakeholders to shape product vision |
| jd-16736 | Collaborate with product managers, engineers, and stakeholders |
| jd-17082 | Partner with stakeholders to evaluate opportunities |
| jd-18064 | collaborate professionally with technical and non-technical stakeholders |
| jd-2200 | Partner with scientists, engineers, and cross-functional stakeholders |
| jd-4060 | Collaborate closely with peers and stakeholders |
| jd-5773 | Work with data architects, QA teams, and business stakeholders |
| jd-6092 | AI/ML cost management and stakeholder leadership |
| jd-6280 | Engages with stakeholders to understand project requirements |
| jd-6457 | partner with executive leadership and cross-functional stakeholders |
| jd-6675 | working closely with product stakeholders to agree on MVP requirements |
| jd-6778 | account planning, stakeholder mapping |
| jd-7176 | Experience in working with a diverse set of stakeholders |

jd-18064 is the closest call. The phrase sits in a communication-skills bullet, but it says "collaborate with".

### Keep as labelled (5)

jd-11909, jd-1702, jd-4449, jd-531 and jd-8000 already have the skill, and it still holds. jd-4449 never says "stakeholder", but "drive alignment with CX and Engineering leadership … managing push-back" is stakeholder management.

### Mentions that don't qualify

| JD | Why not |
|---|---|
| jd-10986 | documentation for internal stakeholders |
| jd-13184 | communication with non-technical stakeholders |
| jd-13759 | communicating concepts to non-technical stakeholders; company boilerplate |
| jd-14169 | "does not own … stakeholder relationships" |
| jd-15955 | status updates for executive stakeholders; translating concepts |
| jd-16145 | describing AI system behavior to teammates and stakeholders |

## Test resumes: applied

`test_resume_skill_labels.md` is a Claude draft, so these were applied directly:

- `test-resume-10`: **added**. "Worked with clinical stakeholders to reduce model false-alarm rate".
- `test-resume-03`: **removed**. Only "communicating with stakeholders".
- `test-resume-04`: **removed**. Only "communicate with stakeholders".
- `test-resume-17` ("conveying complex concepts to diverse stakeholders") and `test-resume-28` ("communicating with stakeholders"): stay without the label.
