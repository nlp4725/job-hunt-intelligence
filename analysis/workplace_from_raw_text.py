"""Infer workplace_type (Remote / Hybrid / On-site) from a job's raw_text.

Why this exists: LinkedIn's September 2026 layout change stopped the extension
recording workplace_type (see extension/content/extract.js, findTopCardScope).
That's fixed for new captures, but ~10K historical rows have it NULL and
re-visiting each posting on LinkedIn is slow and risks a block. raw_text is
already stored for all of them, so this recovers the field offline.

This is an INFERENCE, not LinkedIn's own tag. Accuracy is measured against the
2,317 rows that do carry a LinkedIn tag (run with --eval), and only tiers that
clear a precision bar should ever be written back.

Two traps this has to avoid, both common in AI/ML postings specifically:
  - "remote sensing", "remote server", "remote repository", "remote procedure
    call" are not work arrangements.
  - "hybrid search", "hybrid retrieval", "hybrid model", "hybrid approach" are
    architecture words, not work arrangements.
Both are handled by requiring a work-context word near the match rather than
matching the bare keyword.
"""

import re

# ---------------------------------------------------------------- primitives

WORK_CONTEXT = r"(?:work|working|office|onsite|on-site|in-person|schedule|week|days?|role|position|employee|team|arrangement|located|location|based|commut\w*|hq|headquarters)"

# A keyword only counts as a work-arrangement signal when a work-context word
# sits within ~60 characters either side of it.
def _near(text, keyword, context=WORK_CONTEXT, window=60):
    """Count matches of `keyword` that have a `context` word within `window`."""
    hits = 0
    for m in re.finditer(keyword, text, re.I):
        lo = max(0, m.start() - window)
        hi = min(len(text), m.end() + window)
        if re.search(context, text[lo:hi], re.I):
            hits += 1
    return hits


# "remote" is a load-bearing technical word in AI/ML postings. Strip these
# senses before any work-arrangement reasoning, or a posting about remote
# execution reads as a remote job.
REMOTE_TECH = re.compile(
    r"\bremote\s+(?:sensing|server|servers|repositor\w+|procedure|host|hosts|desktop|"
    r"machine|machines|api|apis|access|execution|branch|branches|origin|url|urls|call|calls)\b",
    re.I,
)


# Explicit declarations — highest confidence. These are the phrasings that
# only ever appear when the poster is stating the arrangement outright.
EXPLICIT = [
    ("Remote", r"\b(?:100%|fully|entirely|permanently)\s+remote\b"),
    ("Remote", r"\bremote[-\s]first\b"),
    # "work from anywhere" states the arrangement; bare "work from home" is
    # usually a perk in a benefits list ("Work from home flexibility") next to
    # commuter benefits and free lunches, and mislabelled Hybrid roles Remote.
    ("Remote", r"\bwork\s+from\s+anywhere\b"),
    ("Remote", r"\bthis\s+(?:is\s+a|role\s+is\s+a?|position\s+is\s+a?)\s*(?:fully\s+)?remote\b"),
    ("Remote", r"\b(?:location|work\s*model|work\s*type|workplace)\s*[:\-]\s*remote\b"),
    ("Hybrid", r"\b(?:location|work\s*model|work\s*type|workplace)\s*[:\-]\s*hybrid\b"),
    # Deliberately excludes "hybrid model/environment/approach/setup": in AI
    # postings those are architecture words ("hybrid model", "hybrid search",
    # "hybrid retrieval") and they were the single largest error source in the
    # first eval — 130 genuinely-Remote jobs mislabelled Hybrid.
    ("Hybrid", r"\bhybrid\s+(?:work|working|role|position|schedule|arrangement)\b"),
    ("Hybrid", r"\bwork\s+(?:model|arrangement|setup)\s*[:\-]?\s*hybrid\b"),
    # Day counts only mean Hybrid below five. "5 days in-person" / "5 Days
    # Onsite" is a full-time office role and was the largest On-site error in
    # the second eval, all of it landing on Hybrid.
    ("Hybrid", r"\b[1-4]\s*(?:\+|or\s+more)?\s*days?\s+(?:per\s+week\s+|a\s+week\s+|each\s+week\s+)?(?:in|at|from)\s+(?:the\s+)?office\b"),
    ("Hybrid", r"\b[1-4]\s*days?\s+(?:on-?site|in-?person|in\s+(?:the\s+)?office)\b"),
    ("On-site", r"\b5\s*\+?\s*days?\s+(?:a\s+week\s+|per\s+week\s+|each\s+week\s+)?(?:on-?site|in-?person|in\s+(?:the\s+)?office)\b"),
    ("On-site", r"\b(?:100%|fully|entirely)\s+(?:on-?site|in-?person|in\s+office)\b"),
    ("On-site", r"\b(?:location|work\s*model|work\s*type|workplace)\s*[:\-]\s*on-?site\b"),
    ("On-site", r"\b5\s*days?\s+(?:a|per)\s+week\s+(?:in|at|from)\s+(?:the\s+)?office\b"),
    ("On-site", r"\b(?:must|required\s+to)\s+(?:be\s+)?(?:able\s+to\s+)?relocate\b"),
    ("On-site", r"\bthis\s+(?:is\s+an?|role\s+is\s+an?|position\s+is\s+an?)\s*(?:fully\s+)?(?:on-?site|in-?person)\b"),
]

# Weaker keyword signals, only counted when a work-context word is nearby.
KEYWORD = [
    # "hybrid" alone needs an office/week/commute word beside it, not merely a
    # generic work word — otherwise "hybrid search ... for our remote team"
    # scores as Hybrid.
    ("Hybrid", r"\bhybrid\b", r"(?:office|week|days?|commut\w*|in-?person|on-?site|schedule)"),
    ("On-site", r"\b(?:on-?site|in-?person|in\s+the\s+office)\b", WORK_CONTEXT),
    ("Remote", r"\bremote(?:ly)?\b", WORK_CONTEXT),
]

# Phrases that flip a "remote" mention into an explicit denial of remote work.
REMOTE_NEGATION = r"\b(?:not|no|isn'?t|non)\s*[-\s]?(?:a\s+)?(?:fully\s+)?remote\b|\bremote\s+work\s+is\s+not\b|\bno\s+remote\s+(?:work|option)\b"


# Job titles sometimes carry the arrangement outright — "AI Engineer II
# (REMOTE)". Measured on the labelled set this parenthetical is 97.7% precise
# but fires on only 1.9% of rows, so it is used purely as a rescue when the
# body text abstains (95.5% precise in exactly that role, +96 rows on the
# backfill target). It deliberately does NOT override a raw_text verdict:
# titles do lie — a live posting on 2026-09-08 read "Machine Learning (AI)
# Engineer (Hybrid)" while LinkedIn's own tag and its meta line both said
# Remote.
TITLE_PARENTHETICAL = re.compile(r"\(\s*(remote|hybrid|on-?site)\s*\)", re.I)
_TITLE_LABEL = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site", "onsite": "On-site"}


def infer_from_title(title):
    if not title:
        return None
    match = TITLE_PARENTHETICAL.search(title)
    return _TITLE_LABEL[match.group(1).lower()] if match else None


def infer_workplace_type(raw_text, title=None):
    """Return (label, confidence) where confidence is 'high' | 'low' | None.

    `title` is optional and only consulted when the body text yields nothing.
    """
    if not raw_text or not raw_text.strip():
        from_title = infer_from_title(title)
        return (from_title, "title") if from_title else (None, None)
    text = REMOTE_TECH.sub(" ", re.sub(r"\s+", " ", raw_text))

    denies_remote = bool(re.search(REMOTE_NEGATION, text, re.I))

    # Explicit declarations win. Hybrid is checked before Remote because a
    # hybrid posting nearly always also says "remote" somewhere ("2 remote
    # days"), while a genuinely remote posting rarely says "hybrid".
    hits = {label for label, pattern in EXPLICIT if re.search(pattern, text, re.I)}
    for label in ("Hybrid", "On-site", "Remote"):
        if label in hits:
            if label == "Remote" and denies_remote:
                continue
            # An explicit signal for two different arrangements is a real
            # ambiguity (e.g. "remote or hybrid"), not a high-confidence call.
            if len(hits - {"Remote"} if label == "Remote" else hits) > 1:
                break
            return label, "high"

    # Fall back to context-checked keyword counts. Postings mention their real
    # arrangement repeatedly and rival arrangements in passing ("no hybrid
    # option", "unlike our hybrid teams"), so compare frequencies rather than
    # mere presence, and require a clear winner.
    counts = {}
    for label, pattern, context in KEYWORD:
        n = _near(text, pattern, context)
        if n:
            counts[label] = counts.get(label, 0) + n
    if denies_remote:
        counts.pop("Remote", None)
    if not counts:
        return None, None
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    top, top_n = ranked[0]
    runner_n = ranked[1][1] if len(ranked) > 1 else 0
    if top_n >= 2 * max(runner_n, 1) or (len(ranked) == 1 and top_n >= 2):
        return top, "low"

    from_title = infer_from_title(title)
    return (from_title, "title") if from_title else (None, None)


# ------------------------------------------------------- remote / not-remote

# The three-way task is hard mainly because Hybrid and On-site are hard to
# tell apart (57% and 44% precision respectively). But that distinction is not
# what the dashboard filter is for — the question is only "can this be worked
# from home". Collapsing the two office classes into NOT-REMOTE turns every
# Hybrid-vs-On-site mistake into a non-mistake, so this is decided directly
# rather than by post-hoc collapsing the 3-way label.
EXPLICIT_REMOTE = [p for label, p in EXPLICIT if label == "Remote"]
EXPLICIT_OFFICE = [p for label, p in EXPLICIT if label in ("Hybrid", "On-site")]


def infer_is_remote(raw_text, title=None):
    """Return (is_remote, confidence) with confidence 'high' | 'low' | None.

    is_remote is True / False / None (abstain).
    """
    text = REMOTE_TECH.sub(" ", re.sub(r"\s+", " ", raw_text or ""))
    if not text.strip():
        from_title = infer_from_title(title)
        return (from_title == "Remote", "title") if from_title else (None, None)

    # An explicit denial of remote work settles it outright.
    if re.search(REMOTE_NEGATION, text, re.I):
        return False, "high"

    remote_x = any(re.search(p, text, re.I) for p in EXPLICIT_REMOTE)
    office_x = any(re.search(p, text, re.I) for p in EXPLICIT_OFFICE)

    # Exactly one side declaring itself is the clean case.
    if office_x and not remote_x:
        return False, "high"
    if remote_x and not office_x:
        return True, "high"

    remote_n = _near(text, r"\bremote(?:ly)?\b", WORK_CONTEXT)
    office_n = _near(text, r"\bhybrid\b", r"(?:office|week|days?|commut\w*|in-?person|on-?site|schedule)") + _near(
        text, r"\b(?:on-?site|in-?person|in\s+the\s+office)\b", WORK_CONTEXT
    )

    # Both sides declared themselves ("remote or hybrid", "remote-first but 2
    # days in office"): let volume break the tie, since the real arrangement
    # gets restated and the alternative gets mentioned once.
    if remote_x and office_x:
        if remote_n >= 2 * max(office_n, 1):
            return True, "low"
        if office_n >= 2 * max(remote_n, 1):
            return False, "low"
        return None, None

    if remote_n or office_n:
        if remote_n >= 2 * max(office_n, 1):
            return True, "low"
        if office_n >= 2 * max(remote_n, 1):
            return False, "low"

    from_title = infer_from_title(title)
    return (from_title == "Remote", "title") if from_title else (None, None)


def _eval_binary():
    import collections
    import sys

    sys.path.insert(0, ".")
    from db.session import SessionLocal
    from db.models import Job

    session = SessionLocal()
    rows = [
        j
        for j in session.query(Job)
        .filter(Job.workplace_type != None, Job.workplace_type_source == "linkedin")
        .all()
        if (j.raw_text or "").strip()
    ]

    cells = collections.Counter()
    for job in rows:
        pred, conf = infer_is_remote(job.raw_text, job.title)
        truth = job.workplace_type == "Remote"
        cells[(conf or "abstain", pred, truth)] += 1

    total = len(rows)
    print(f"ground truth rows (LinkedIn-tagged): {total}\n")
    print(f"{'tier':<9}{'predicts':<13}{'n':>6}{'precision':>11}")
    for tier in ("high", "low", "title"):
        for pred, name in ((True, "Remote"), (False, "NOT remote")):
            n = cells[(tier, pred, True)] + cells[(tier, pred, False)]
            if not n:
                continue
            correct = cells[(tier, pred, pred)]
            print(f"{tier:<9}{name:<13}{n:>6}{100 * correct / n:>10.1f}%")
    decided = sum(v for (t, p, _), v in cells.items() if t != "abstain")
    correct = sum(v for (t, p, tr), v in cells.items() if t != "abstain" and p == tr)
    print(f"\noverall decided: {decided} ({100 * decided / total:.1f}% coverage), "
          f"accuracy {100 * correct / decided:.1f}%")


# ------------------------------------------------------------------ eval CLI

def _eval():
    import collections
    import sys

    sys.path.insert(0, ".")
    from db.session import SessionLocal
    from db.models import Job

    session = SessionLocal()
    rows = [
        j
        for j in session.query(Job).filter(Job.workplace_type != None, Job.raw_text != None).all()
        if (j.raw_text or "").strip()
    ]

    stats = collections.defaultdict(lambda: {"n": 0, "correct": 0})
    confusion = collections.Counter()
    for job in rows:
        pred, conf = infer_workplace_type(job.raw_text, job.title)
        tier = conf or "abstain"
        stats[tier]["n"] += 1
        if pred == job.workplace_type:
            stats[tier]["correct"] += 1
        if conf:
            confusion[(job.workplace_type, pred)] += 1

    total = len(rows)
    print(f"ground truth rows: {total}\n")
    print(f"{'tier':<10}{'n':>7}{'coverage':>11}{'precision':>12}")
    for tier in ("high", "low", "title", "abstain"):
        s = stats[tier]
        if not s["n"]:
            continue
        prec = "-" if tier == "abstain" else f"{100 * s['correct'] / s['n']:.1f}%"
        print(f"{tier:<10}{s['n']:>7}{100 * s['n'] / total:>10.1f}%{prec:>12}")

    print("\nconfusion (actual -> predicted), non-abstaining only:")
    labels = ["Remote", "Hybrid", "On-site"]
    print(f"{'actual':<10}" + "".join(f"{l:>10}" for l in labels))
    for actual in labels:
        row = "".join(f"{confusion[(actual, p)]:>10}" for p in labels)
        print(f"{actual:<10}{row}")


# Measured on the 2,317 LinkedIn-labelled rows (python -m analysis.
# workplace_from_raw_text --eval). Only (tier, label) pairs that cleared ~90%
# precision are written back; Hybrid never is, at any tier, because office
# roles frequently just don't state the arrangement and the rule then guesses
# Hybrid off an incidental mention:
#     high/Remote   95.9%  (n=415)      high/Hybrid  57.2%  <- rejected
#     high/On-site  90.5%  (n=21)       low/On-site  43.5%  <- rejected
#     low/Remote    90.3%  (n=103)      low/Hybrid   66.7%  <- rejected
WRITEBACK_ALLOWED = {
    ("high", "Remote"),
    ("high", "On-site"),
    ("low", "Remote"),
    ("title", "Remote"),
    ("title", "On-site"),
}


def _apply(dry_run=True):
    import collections
    import sys

    sys.path.insert(0, ".")
    from db.session import SessionLocal, init_db
    from db.models import Job

    init_db()
    session = SessionLocal()
    rows = [
        j
        for j in session.query(Job).filter(Job.workplace_type == None).all()
        if (j.raw_text or "").strip()
    ]

    counts = collections.Counter()
    for job in rows:
        pred, conf = infer_workplace_type(job.raw_text, job.title)
        if not pred:
            counts["abstain"] += 1
        elif (conf, pred) in WRITEBACK_ALLOWED:
            counts[f"write {pred} ({conf})"] += 1
            if not dry_run:
                job.workplace_type = pred
                job.workplace_type_source = "raw_text"
        else:
            counts[f"skip {pred} ({conf}) — below precision bar"] += 1

    if not dry_run:
        session.commit()

    print(f"candidate rows (workplace_type NULL, raw_text present): {len(rows)}")
    for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>6}  {key}")
    written = sum(n for k, n in counts.items() if k.startswith("write"))
    print(f"\n{'WOULD WRITE' if dry_run else 'WROTE'}: {written} rows"
          f" ({100 * written / len(rows):.1f}% of candidates)")
    if dry_run:
        print("re-run with --apply to commit")


if __name__ == "__main__":
    import sys

    if "--binary" in sys.argv:
        _eval_binary()
    elif "--apply" in sys.argv:
        _apply(dry_run=False)
    elif "--dry-run" in sys.argv:
        _apply(dry_run=True)
    else:
        _eval()
