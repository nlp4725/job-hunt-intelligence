"""
Shared text cleanup run on both JD and resume text before extract_skills().

Only changes how characters are encoded, never which words are there, so the
same taxonomy regex matches whether text came from LinkedIn, a PDF or a DOCX.
See docs/resume_jd_skill_pipeline.md §2.1.
"""

import re
import unicodedata

# Unicode hyphen look-alikes that render as "-" but don't match it in a regex.
# Applied after NFKC, which turns U+2011 into U+2010 rather than into "-".
_HYPHENS = str.maketrans({
    "‐": "-",   # hyphen
    "‑": "-",   # non-breaking hyphen
    "­": None,  # soft hyphen: invisible "may break here" marker inside a word
})


def normalize(text: str) -> str:
    # NFKC: ligatures (U+FB01 -> "fi"), non-breaking and full-width spaces,
    # full-width letters -> their plain keyboard characters.
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_HYPHENS)
    # A hyphen at a line end: join the lines but KEEP the hyphen. "scikit-\nlearn"
    # becomes "scikit-learn"; dropping it would turn "end-to-\nend" into "end-toend".
    text = re.sub(r"(\w)-[ \t]*\n[ \t]*(\w)", r"\1-\2", text)
    # Collapse spaces/tabs only; newlines are kept for line and section splitting.
    return re.sub(r"[ \t]+", " ", text)
