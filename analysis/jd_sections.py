"""Split a job description into its structural sections.

Why this exists: `skills_extractor.extract_skills()` is deliberately
structure-agnostic — it reports presence/absence of a term anywhere in the
blob. That is the right default for a "what's trending" dashboard, but it is
wrong for *screening a specific job*, because a JD's company blurb sells the
employer's product while its requirements describe the actual work.

Real case from the corpus (job 10360, Sigma "Data Engineer, Data Platform"):
the About Us section says "the AI Apps and agentic analytics platform", so the
whole JD gets tagged with the `Agents` skill — while the job itself is
Snowflake / dbt / Terraform pipelines with no agent work at all. Across the
internal ml_ai corpus, 73 postings (4%) mention AI ONLY in the blurb.

Sections are labelled:
    role  - responsibilities / what you'll do
    req   - qualifications / requirements / skills
    co    - company blurb ("About us", "our mission")
    pay   - benefits, compensation, EEO, application logistics

`requirement_text()` is what screening code should score against.
"""

import re

__all__ = ["split_sections", "requirement_text", "blurb_text", "SECTION_KINDS"]

SECTION_KINDS = ("role", "req", "co", "pay")

# Body sections must total at least this many characters to be trusted. Below
# it, a stray heading word (e.g. the literal "skills" inside a compensation
# paragraph: "...skills, certifications, etc.") is almost certainly a false
# heading rather than a real section, and treating it as the body would strand
# the actual job description in the `co` bucket. Observed on job 12734, whose
# entire 2.8k-char body was otherwise misfiled as company blurb.
_MIN_BODY_CHARS = 300

_HEADING = re.compile(
    r"(?i)\b("
    r"(?P<role>what you.{0,18}(will )?(be )?(do|doing|work on)|responsibilities"
    r"|your (role|impact|mission)|in this role|day to day"
    r"|what you.{0,6}ll (do|own|build)|the (role|opportunity)|key duties)"
    r"|(?P<req>qualifications|requirements|what (we.{0,6}re looking for|you.{0,6}ll (need|bring))"
    r"|who you are|about you|skills|experience (required|we)|must have|nice to have"
    r"|preferred|basic qualifications)"
    r"|(?P<co>about (us|the company|our)|who we are|our (mission|story|team|company)"
    r"|why join|company overview)"
    r"|(?P<pay>benefits|compensation|salary|base pay|perks|equal opportunity|eeo"
    r"|additional job details|total rewards|what we offer|pay range|application|how to apply)"
    r")\b"
)

# LinkedIn page chrome that leaks into scraped raw_text. Stripped before
# sectioning so it cannot be mistaken for job content. The Premium upsell
# carries the logged-in user's own first name, so it is matched generically.
# LinkedIn prefixes every scraped posting with "About the job". It is not a
# heading and not company blurb — stripping it keeps "the job" out of the
# role-heading alternation, where it used to split the posting one word in.
_LEAD_PREFIX = re.compile(r"^\s*About the job\s*", re.I)

_CHROME = (
    re.compile(r"window\.__[\s\S]{0,600}?\}\)?;"),
    re.compile(r"\b\w+ and millions of other members[\s\S]{0,240}"),
    re.compile(r"Premium subscribers are [\s\S]{0,160}"),
)


def _clean(text: str) -> str:
    text = _LEAD_PREFIX.sub("", text)
    for pattern in _CHROME:
        text = pattern.sub(" ", text)
    return text


def split_sections(text: str) -> list[tuple[str, str]]:
    """Return [(kind, chunk), ...] in document order. Never returns []."""
    if not text:
        return []
    text = _clean(text)

    marks = []
    for match in _HEADING.finditer(text):
        kind = next(k for k, v in match.groupdict().items() if v)
        marks.append((match.start(), kind))

    if not marks:
        # No headings at all — the whole posting is job content.
        return [("role", text)]

    out = []
    for i, (pos, kind) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.append((kind, text[pos:end]))

    body_chars = sum(len(c) for k, c in out if k in ("role", "req"))
    # A body is "missing" only if it is both small in absolute terms AND a tiny
    # slice of the posting. The absolute test alone mis-fires on short postings
    # with genuinely brief sections; the relative test alone mis-fires on long
    # ones. Both together isolate the real failure: a stray heading word that
    # carved a 77-char section out of a 3k-char posting (job 12734).
    body_missing = body_chars < _MIN_BODY_CHARS and body_chars < 0.25 * len(text)

    # Text before the first heading. LinkedIn always prefixes "About the job",
    # so this leading chunk is the job description itself — only demote it to
    # blurb when a substantial role/req section exists elsewhere to carry the
    # requirements.
    if marks[0][0] > 0:
        lead = text[: marks[0][0]]
        out.insert(0, ("role" if body_missing else "co", lead))

    # Still no real body? Then every non-pay chunk is job content, otherwise
    # the entire posting would read as "company marketing".
    if body_missing:
        out = [("role" if k == "co" else k, c) for k, c in out]

    return out


def requirement_text(text: str) -> str:
    """The parts of a JD that describe the actual job — responsibilities and
    requirements. This is what screening should score against."""
    return " ".join(c for k, c in split_sections(text) if k in ("role", "req"))


def blurb_text(text: str) -> str:
    """Company marketing and benefits/legal boilerplate — everything screening
    should NOT treat as a job requirement."""
    return " ".join(c for k, c in split_sections(text) if k in ("co", "pay"))
