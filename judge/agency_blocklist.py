"""
Agency Blocklist (see CONTEXT.md "Agency Blocklist") — two detection
methods, checked before a job reaches the Screening Agent:

1. A curated list of known data-labeling/AI-training gig platforms and
   freelance marketplaces, grounded in what's actually in the DB (found by
   inspecting real company_name values, not guessed) — these post generic
   contractor gigs ("AI Trainer", "Freelance ... Project") rather than
   direct roles at a real employer, and don't reliably trigger the
   seniority_fit/expertise_match rubrics' own recruiter-detection rule
   (that rule catches phrasing like "I'm partnered with...", which these
   postings don't use).
2. A deterministic check against Company.industry == "Staffing and
   Recruiting" (captured for most companies already in the DB).

This was previously documented in CONTEXT.md but never implemented —
screening_run.py screened every job regardless, so known agency/gig
postings ended up in screening_results with real scores.

is_agency_company_name() reuses the same two checks to filter by company
name alone, at scraper/run_scrape.py's list-page stage (before a job's full
detail — and therefore a Job row — exists at all), so a known agency
posting never costs a full detail-page fetch. is_agency_job()/
delete_agency_jobs() remain as a backstop for a company seen for the first
time this run, whose industry only becomes known after its own detail
fetch.
"""

# Curated by inspecting real job listings in the DB (see judge/screening_run.py
# usage) — each of these was confirmed to post generic contractor/freelance
# "AI Trainer"-style gigs, not roles at a real, specific employer.
AGENCY_COMPANY_NAME_SUBSTRINGS = [
    "dataannotation",
    "turing",
    "alignerr",
    "yo it consulting",
    "toloka",
    "micro1",
    "mercor",
    "prolific",
    "meridial marketplace",  # "... by Invisible"
    "toptal",
    "scale ai",
    "telus digital",
    "braintrust",
    "handshake",
    "remotehunter",  # self-reports industry as "Software Development", not "Staffing and Recruiting" — the industry check alone misses it; postings admit "not the Employer of Record... connect candidates with leading employers"
    "fetchjobs",  # self-reports as "IT Services and IT Consulting", 2-10 employees — reposts/scrapes listings from real employers (Rockstar Games, Instacart, Deccan AI...) with title/content mismatches; inconsistently caught by the LLM rules, one instance scored a perfect 15/15
    "agilegrid",  # self-reports industry as "Software Development" — postings are actually for other companies (e.g. Prodigious Worldwide/Publicis Groupe), same anonymized-recruiter pattern as remotehunter/fetchjobs
    "hackajob",  # self-reports industry as "Software Development" — 23 postings in DB, many titled "... (Train AI Models Part Time!)", same generic AI-training-gig pattern as toloka/prolific/alignerr
    "bright vision",  # self-reports as "IT System Custom Software Development", 51-200 employees — 94 postings in DB (~2.3% of the whole corpus from one "company"), almost all generic "AI ___ Engineer" title variants (Security/Performance/Data/Research/Reinforcement Learning); raw JD text itself says "Bright Vision Technologies SOW" (statement-of-work, contract-staffing language) and "Position Type: In-house" — same anonymized-recruiter/contractor-shop pattern as remotehunter/fetchjobs/agilegrid
    "jobright",  # self-reports industry as "Software Development" — 51 postings in DB, 33 explicitly "part of the Jobright TNT" / "Jobright Direct Hiring Network" reposts naming a different real employer ("Hiring Company: Cresta", "...Deduction.com", etc.); the remaining postings are Jobright's own real "AI Engineer, Entry Level"/"Data Analyst, New Grad"/"Data Scientist, Early Career" openings repeated many times over — blocked wholesale rather than splitting the handful of genuine listings out, same tradeoff as bright vision
    "haystack",  # self-reports industry as "Technology, Information and Internet", 51-200 employees — 163 postings in DB, raw JD text says "We're hiring on behalf of a Haystack partner!" / "Apply via Haystack today!" for a different, unnamed real employer — same anonymized-recruiter pattern as remotehunter/fetchjobs/agilegrid
    "jobgether",  # self-reports industry as "Internet Marketplace Platforms" — 148 postings in DB, raw JD text says "This position is listed on behalf of a partner company, who manages all applications and next steps" for a different, unnamed real employer — same anonymized-recruiter pattern as haystack/remotehunter
    "sundayy",  # self-reports industry as "Technology, Information and Internet" — postings open with an "About The Company" block naming a different real employer (Rolls-Royce, Capital One, ...) rather than Sundayy itself — same anonymized-recruiter pattern as haystack/jobgether
    "rex.zone",  # self-reports industry as "Technology, Information and Internet" — postings are generic "STEM Jobs in Brazil"/AI-data-labeling gig listings ("Rex.zone connects ... professionals with Remote ... roles") rather than a specific employer's own opening, same AI-training-gig pattern as toloka/prolific/alignerr
    "chatgpt jobs",  # self-reports industry as "Technology, Information and Internet" — postings name an unrelated real employer inside the JD ("Company: Light & Wonder", "About The Client E-Commerce company...") behind a generic "ChatGPT Jobs" listing company, same anonymized-recruiter pattern as haystack/jobgether/sundayy
]


def _matches_agency_substring(company_name: str | None) -> bool:
    name = (company_name or "").lower()
    return any(needle in name for needle in AGENCY_COMPANY_NAME_SUBSTRINGS)


def is_agency_job(job) -> bool:
    """job: db.models.Job, with .company (relationship) loaded or loadable."""
    if _matches_agency_substring(job.company_name):
        return True

    if job.company is not None and job.company.industry == "Staffing and Recruiting":
        return True

    return False


def is_agency_company_name(session, company_name: str | None) -> bool:
    """Same two checks as is_agency_job, but usable before a job has a Job
    row at all — meant for filtering by the company name shown on a
    LinkedIn list-page card (see scraper/linkedin_scraper.py parse_job_card)
    before spending a full detail-page fetch on it. The industry check here
    only catches companies this DB has already seen and detail-fetched
    before (industry is only known post-detail-fetch); a same-run
    first-sighting still needs the delete_agency_jobs() backstop below."""
    if not company_name:
        return False
    if _matches_agency_substring(company_name):
        return True

    from db.models import Company  # deferred: avoids a circular import at module load time

    company = session.query(Company).filter(Company.name == company_name).first()
    return company is not None and company.industry == "Staffing and Recruiting"


def delete_agency_jobs(session) -> int:
    """Deletes every Job (plus its JobSkill and, defensively, ScreeningResult
    rows) matching is_agency_job() — meant to be called once at the end of
    a scrape run so blocklisted postings never pile up waiting for someone
    to clean them out by hand. ScreeningResult rows here are expected to be
    zero (agency jobs are filtered out before ever reaching the Screening
    Agent, see screening_run.py), but are deleted too in case a company got
    added to the blocklist after some of its postings were already
    screened. Returns the number of jobs deleted."""
    from db.models import Job, JobSkill, ScreeningResult  # deferred: avoids a circular import at module load time

    job_ids = [j.id for j in session.query(Job).all() if is_agency_job(j)]
    if not job_ids:
        return 0

    session.query(JobSkill).filter(JobSkill.job_id.in_(job_ids)).delete(synchronize_session=False)
    session.query(ScreeningResult).filter(ScreeningResult.job_id.in_(job_ids)).delete(synchronize_session=False)
    session.query(Job).filter(Job.id.in_(job_ids)).delete(synchronize_session=False)
    session.commit()
    return len(job_ids)
