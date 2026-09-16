"""Resume upload and skill confirmation for a cloud user (productization plan
§3.1; parsing and redaction in docs/resume_jd_skill_pipeline.md).

A user has one active resume: the one their latest profile version points at.
The caller owns the transaction: these functions flush, never commit.
"""

from pathlib import PurePath

from sqlalchemy import func

from analysis.skills_extractor import SKILL_TAXONOMY
from analysis.taxonomy_version import taxonomy_version
from analysis.user_scoring import active_profile, score_user
from db.cloud_models import User, UserProfile, UserResume
from resume.pii import KnownIdentity
from resume.pipeline import process_resume
from resume.store import ResumeCipher, resume_storage_key


def _next_version(db, user_id: int) -> int:
    return (db.query(func.max(UserResume.version)).filter(UserResume.user_id == user_id).scalar() or 0) + 1


def add_resume(db, user: User, filename: str, data: bytes, store, cipher: ResumeCipher) -> UserResume:
    """Store an upload as the user's next resume version, with skills extracted
    but not yet confirmed. Raises UnsupportedResume before anything is stored."""
    processed = process_resume(filename, data, KnownIdentity(name=user.display_name or "", email=user.email))
    version = _next_version(db, user.id)
    key = resume_storage_key(user.id, version, filename)
    store.put(key, cipher.encrypt(data))
    resume = UserResume(
        user_id=user.id, version=version, original_filename=PurePath(filename).name, storage_key=key,
        text_encrypted=cipher.encrypt(processed.text.encode()), redacted_text=processed.redacted_text,
        skills_extracted=sorted(set(processed.skills)), taxonomy_version=taxonomy_version(),
    )
    db.add(resume)
    db.flush()
    return resume


def confirm_skills(db, user_id: int, resume_id: int, skills: list[str]) -> UserResume:
    """Save the user's skill set for a resume and make that resume active.

    The first confirmation fills in the version in place; editing an already
    confirmed set writes a new version (same file and text), so scores computed
    from the old set stay attributable. If the user has a profile, a new profile
    version points at the resume and the user is rescored; during onboarding the
    profile comes later and scoring happens then."""
    resume = db.get(UserResume, resume_id)
    if resume is None or resume.user_id != user_id:
        raise LookupError("resume not found")
    unknown = sorted(set(skills) - SKILL_TAXONOMY.keys())
    if unknown:
        raise ValueError(f"not in the skill list: {', '.join(unknown)}")
    confirmed = sorted(set(skills))

    if resume.skills_confirmed is None:
        resume.skills_confirmed = confirmed
    else:
        resume = UserResume(
            user_id=user_id, version=_next_version(db, user_id), original_filename=resume.original_filename,
            storage_key=resume.storage_key, text_encrypted=resume.text_encrypted, redacted_text=resume.redacted_text,
            skills_extracted=resume.skills_extracted, skills_confirmed=confirmed, taxonomy_version=resume.taxonomy_version,
        )
        db.add(resume)
    db.flush()

    profile = active_profile(db, user_id)
    if profile is not None:
        db.add(UserProfile(
            user_id=user_id, version=profile.version + 1, resume_id=resume.id, seniority_target=profile.seniority_target,
            seniority_scores=profile.seniority_scores,
            target_roles=profile.target_roles, note=profile.note, years_experience=profile.years_experience,
        ))
        db.flush()
        score_user(db, user_id)
    return resume
