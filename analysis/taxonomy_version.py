"""A short fingerprint of the skill taxonomy, stored next to every skill set and
score so a taxonomy update can tell which were computed with an older list
(productization plan §9, "Applying a taxonomy update to users")."""

import hashlib
from functools import lru_cache
from pathlib import Path

import analysis.skills_extractor as skills_extractor


@lru_cache(maxsize=1)
def taxonomy_version() -> str:
    return hashlib.sha256(Path(skills_extractor.__file__).read_bytes()).hexdigest()[:12]
