# Salary and company size are not Job Judge criteria

Both were part of the original 5-criteria proposal but were dropped after inspecting the real scraped corpus (`data/job_hunt.db`).

**Company size**: dropped in favor of folding growth/stability signal into Company Research's Momentum dimension. A hard >100-employee threshold would penalize small, well-funded, high-momentum startups — companies the user explicitly wants to surface, not filter out.

**Salary**: dropped entirely, including as a pass/fail floor, despite real disclosed-salary variance in the data (bottom-quartile ml_ai jobs top out near $104K, top quartile runs $169K–$416K — a genuine >4x spread, not "they all pay similar"). The deciding factor was coverage, not variance: only ~20% of scraped listings (472/2,308 ml_ai, 131/729 pm) disclose salary at all, so a salary criterion — scored or gated — would be silent for 80% of jobs, and a levels.fyi fallback lookup was judged not worth the added complexity.

Considered and rejected: keeping salary as a scored criterion with levels.fyi fallback for undisclosed listings (same missing-data pattern as Company Research's Missing-Dimension Rule) — rejected for coverage/complexity reasons, not because the signal itself lacks discriminating power.
