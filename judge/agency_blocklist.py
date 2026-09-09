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

import re

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
    "accion labs",  # self-reports industry as "Information Technology & Services" — job poster titled "Senior Technical Recruiter", JD reads "12+ Months Contract Role" for an unnamed end client — same IT-staffing pattern as fetchjobs/agilegrid
    "sgf global",  # self-reports industry blank — job poster bio literally reads "Managing Requisitions for Deloitte and Multinational Clients", JD states the role is "for a Deloitte project" — unambiguous staffing placement
    "photon",  # self-reports industry as "IT Services and IT Consulting" — job poster titled "Talent Acquisition Director | Recruitment Maestro", company bio touts "powered Digital Experiences for the Fortune 500" — IT consulting/staffing shop, not a direct employer
    "techifide",  # self-reports industry blank — job poster bio reads "Turning VC Funding into Scalable Software Teams | Nearshore IT Professionals" — nearshore staffing shop
    "okaya infocom",  # self-reports industry blank — job poster titled "Assistant Manager (Sales & Recruitment)" — staffing agency
    "hmg america",  # self-reports industry as "IT Services and IT Consulting" — job poster bio reads "Lead US/CANADA/INDIA IT Recruiter | ... | We help people find the perfect job" — staffing agency
    "bestinfo systems",  # self-reports industry blank — job poster bio reads "Hiring for Pharmaceutical/Healthcare/Manufacturing/BFSI/IT/NON-IT/DOD/Aerospace/... -Direct top-rated clients" — staffing agency placing across many unrelated industries
    "futran solutions",  # self-reports industry as "Information Technology & Services" — job poster bio reads "Building the Future of Staffing with MSP & VMS Expertise" — explicit staffing/VMS shop
    "precision technologies",  # self-reports industry as "IT Services and IT Consulting" — JD states "Location: United States (Onsite / Hybrid / Remote – Based on Client Requirement)" and poster is "Team Lead – Talent Management" — body-shop contractor placement, not a direct role
    "ikuto",  # self-reports industry blank — job poster titled "Technology Recruitment Specialist", JD reads "I am partnering with one of the fastest-growing AI infrastructure platforms ... to expand their team" for an unnamed employer — anonymized-recruiter pattern
    "talent software services",  # self-reports industry as "IT Services and IT Consulting" — name plus hourly-rate ($75-85/hr) contractor postings, same IT-staffing pattern as accion labs/sgf global
    "pineq lab",  # self-reports industry as "IT Services and IT Consulting" — job poster titled "Senior Account Manager | Driving Client Success", JD reads "Contract, 12+ Months, Extendable project" — staffing/consulting placement
    # --- added 2026-08-27, each with a JD/profile tell recorded at the time ---
    "ladders",  # job board reselling client reqs — all three postings seen 2026-08-27 (AI Technical Architect, AI Cloud Platform Engineer, Applied AI Scientist, Staff AI Engineer, Principal MCP/AI Developer) open "For our client, we are seeking..." for an unnamed employer
    "workerbee",  # JD reads "C2C not available | No third-party suppliers... Workerbee employees will not respond to direct communication" and describes itself as a "talent network" — staffing intermediary, not the employer
    "netrolynx",  # posting titled "Lead AI Engineer | Netrolynx AI" but the JD's own "About The Company" section opens "At Capital One, we are dedicated to..." — an unattributed repost of another employer's req
    "underdog.io",  # JD reads "Our hiring partner is offering a rare opportunity..." — explicit placement language; company name itself is a candidate marketplace
    "concentric recruitment",  # name is unambiguous; posts "AI Backend Engineer" with no named end employer
    "hawthorne executive search",  # name is unambiguous — executive search firm
    "apetan consulting",  # scored 8/15 with seniority 0; contract/W2 placement postings, IT-staffing pattern
    "tekvalue",  # scored 7/15 with seniority 0; same contract-placement pattern as apetan
    "anagh technologies",  # scored 3/15 with seniority 0 and expertise 0; explicit W2-contract staffing postings
    "jack & jill",  # JD literally states "This is a job that Jill, our AI Recruiter, is recruiting for on behalf of one of our customers"
    "district partners",  # hiring-team bio reads "Executive Search & Consulting"; JD describes a "private-equity-backed national wealth management organization" without naming it
    "qp group",  # hiring-team bio reads "Executive Search - Healthcare Life Sciences"; JD opens "We're partnering with a fast-growing life sciences LIMS company..."
    "zest for tech",  # hiring-team bio reads "Technology Recruiter at Zest | Helping startups build AI/ML & software engineering teams"
    "smart it frame",  # hiring-team bio reads "Lead Recruiter @ Smart IT Frame | US IT Recruitment"
    "avp vigilant",  # JD reads "This position is listed on behalf of a partner company, who manages all applications and next steps"
    "top gen ai jobs",  # job-board scraper — its "About the job" reads "Home/Jobs/AI Automation Specialist ... Bio-Rad", a repost of Bio-Rad's own listing under an aggregator name
    "blufeather solutions",  # self-reports industry blank — job poster titled "Recruiter at Blufeather Solutions | IT & Tech Hiring | Connecting Top Talent with Great Opportunities", hourly-rate ($55-60/hr) contract postings — staffing agency
    # --- added 2026-08-28 ---
    "burtch works",  # self-reports industry as "IT Services and IT Consulting" — its own JD boilerplate reads "we're not just a recruitment agency" and "our approach blends human expertise with the efficiency of our Staffing Platform"; the Sr Full Stack AI Engineer posting scored 11/15 before the tell was found
    "attis",  # self-reports industry as "Staffing and Recruiting", 2-10 employees — JD boilerplate reads "We operate as a staffing agency and employment business" and "Attis is a specialist executive search and recruitment" firm
    "shields group search",  # name is unambiguous (executive search), same class as hawthorne executive search / concentric recruitment; its "Principal Applied AI Engineer" posting scored 3/15 with seniority 0 and expertise 0 — no real employer described
    "revolent",  # self-reports industry as "IT Services and IT Consulting" — its "Forward Deployed AI Engineer - Train & Deploy" JD asks for "regulated-industry exposure (aligned to our client base)" plus "teaching, mentoring, bootcamp, or curriculum-design experience", i.e. it cross-trains people and places them at client sites (Revolent is Tenth Revolution Group's talent-creation arm), not a direct employer
    "sibitalent",  # job poster's own headline reads "SENIOR US IT TECHNICAL RECRUITER" and the JD's interview plan is "1st video round with implementation client and 2nd video round with end client" — a two-layer body shop; the posting still scored 11/15 before the tell was found
    "brooksource",  # self-reports industry as "IT Services and IT Consulting" — its "Cloud Machine Learning Engineer" JD quotes "Rate: 45-65/hr dependent on level" and describes reporting into an unnamed client's ML org; Brooksource is Eight Eleven Group's contract-staffing brand
    # --- added 2026-09-09 ---
    # IT contract-staffing firms. All of these slipped BOTH existing checks: none
    # was on this list, and every one self-reports its industry as "IT Services
    # and IT Consulting" or "Business Consulting and Services" rather than
    # "Staffing and Recruiting", so the industry check in is_agency_job() never
    # fired. That is the general weakness of that check — an agency picks its own
    # label, and agencies rarely pick this one.
    "insight global",  # "Our Healthcare Insurance Client is looking to hire a AI Staff Engineer for a 1 year contract to hire position" — 50 postings in DB
    "kforce",  # "Kforce is looking for a Lead Data Platform Engineer for a contract to hire opportunity in Kansas City, MO" — 22 postings, none scoring >=10
    "teksystems",  # "This is a Contract position based out of Chicago, IL" — 16 postings, none scoring >=10
    "piper companies",  # "Piper Companies is seeking a Principal System Data Analyst to support an industry leader in financial services for a 6 month contract remote role" — full phrase, not "piper", to avoid matching unrelated names
    "optomi",  # "...in partnership with a major US airline, is seeking an Agentic AI Engineer to fulfill a 12-month contract-to-hire with a client based in the DFW metroplex"
    "programmers.io",  # "One of our clients which is having operations globally is looking a Senior AI/ML Lead/Architect"
    "stellar consulting solutions",  # "We are seeking candidates for a Technical Product Manager (TPM) role with one of our clients"
    "delta system & software",  # "Position: Senior AI Engineer - Vertex AI ... Duration: 6 - 12+ month contract" — hourly-contract placement
    "saragossa",  # "The contract-to-hire structure gives you the chance to see the company properly before committing"
    "pragmatike",  # "Your data may be shared with our client(s) for hiring consideration, but will not be disclosed to third parties outside of the recruitment process" — 17 postings, none scoring >=10
    "biospace",  # biotech job-board aggregator — its postings carry another employer's own copy verbatim ("Join Amgen's Mission of Serving Patients ... At Amgen ..."), and all six BioSpace hits duplicate Amgen reqs already in the DB (incl. "Machine Learning Engineer, AI Studio"); same repost-under-an-aggregator-name pattern as top gen ai jobs
]


def _matches_agency_substring(company_name: str | None) -> bool:
    """Word-boundary match, not naive substring — a plain `in` check let the
    "turing" entry (meant for the company Turing) false-positive on any
    company with "manufacturing" in its name (e.g. Modine Manufacturing
    Company, Mallard Manufacturing), silently skipping real employers."""
    name = (company_name or "").lower()
    return any(re.search(r"\b" + re.escape(needle) + r"\b", name) for needle in AGENCY_COMPANY_NAME_SUBSTRINGS)


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
