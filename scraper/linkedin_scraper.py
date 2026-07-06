"""
LinkedIn job search scraper.

Architecture: LinkedIn renders all 25 job_id wrappers
(li[data-occludable-job-id]) in the DOM immediately on page load — no
scrolling needed to discover which jobs are on a given results page. Only
the *inner* card content (title/company/logo) lazy-renders as you scroll,
and we don't need that at all: for genuinely new job_ids, we fetch the full
two-pane detail view instead (title, company, location, industry, company
size, raw JD text, posted date, applicant stats, salary — all from one page
load), which is both more complete and avoids scrolling entirely.

Run:
    python scraper/linkedin_scraper.py "machine learning"
"""

import json                                    # write results to a .json file at the end
import random                                   # random.uniform() for human-like delay jitter
import re                                       # parsing posted-date/salary/text out of button/span labels
import sys                                      # read the keyword from argv, sys.exit() on error
import time                                     # time.sleep() for the jitter delays
from pathlib import Path                        # cross-platform file path handling
from urllib.parse import quote                  # URL-encode the keyword (e.g. spaces -> %20)

from bs4 import BeautifulSoup                   # parses raw HTML into a searchable tree
from selenium import webdriver                  # drives an actual Chrome browser
from selenium.webdriver.chrome.options import Options   # Chrome launch flags (profile dir, window size)
from selenium.webdriver.common.by import By      # "how to locate an element" enum (CSS, XPath, etc.)
from selenium.webdriver.support import expected_conditions as EC  # wait-until conditions
from selenium.webdriver.support.ui import WebDriverWait           # explicit "wait up to N seconds" helper

PROFILE_DIR = Path(__file__).parent / ".chrome-profile"   # the dedicated Chrome profile dir from setup_chrome_profile.py
OUTPUT_FILE = Path(__file__).parent / "sample_output.json"  # where this test run's results get saved


class LinkedInBlockedError(Exception):
    """Raised when LinkedIn serves a checkpoint/challenge/login page instead
    of search results. There's no formal "rate limit" to detect on a scraped
    web page (no 429 status, no rate-limit headers) — this is the actual
    signal: LinkedIn stops showing us content and shows a challenge instead.
    The right response is to stop entirely for this run, not retry — retrying
    into a challenge is what actually gets accounts flagged/restricted.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def check_not_blocked(driver: webdriver.Chrome) -> None:
    url = driver.current_url                                     # the page we actually ended up on (may differ from the URL we requested, if redirected)
    if "/checkpoint/" in url or "/authwall" in url:               # LinkedIn's security-challenge / CAPTCHA / "unusual activity" pages live under /checkpoint/
        raise LinkedInBlockedError("challenge")                   # stop immediately — do not retry, that's what makes things worse
    if "/login" in url:                                            # got bounced to the login page — our saved session expired or was invalidated
        raise LinkedInBlockedError("logged_out")


def jitter(a: float = 1.5, b: float = 5) -> None:
    time.sleep(random.uniform(a, b))        # sleep a random amount between a and b seconds — avoids robotic fixed-interval timing


def simulate_reading(driver: webdriver.Chrome, min_seconds: float = 3.0, max_seconds: float = 15.0) -> None:
    """Scroll through the job description pane in small increments over a
    randomized 3-15s dwell time, mimicking someone actually reading the
    posting rather than a script that loads the page and immediately
    scrapes it. Scrolls the actual nested scrollable container
    (div.jobs-search__job-details--wrapper), not window — the two-pane
    detail view's right-hand pane scrolls independently of the outer page
    (confirmed via direct inspection: scrollHeight 5663 vs clientHeight 748).

    Direction, distance, and pause length are all randomized independently —
    including occasional scroll-UP moves, not just monotonic downward
    scrolling, since a real reader re-checks something above far more often
    than a script would ever think to. Scrolling down still wins on average
    (65% of moves) so we make net forward progress through the posting.
    """
    target_dwell = random.uniform(min_seconds, max_seconds)  # pick one total dwell time for this listing
    elapsed = 0.0
    while elapsed < target_dwell:
        direction = 1 if random.random() < 0.65 else -1      # mostly down, but sometimes back up — breaks the monotonic scripted pattern
        scroll_amount = direction * random.randint(80, 500)   # random distance in either direction
        driver.execute_script(
            "const el = document.querySelector('div.jobs-search__job-details--wrapper'); "
            "if (el) { el.scrollTop += arguments[0]; }",
            scroll_amount,
        )
        pause = min(random.uniform(0.8, 3.5), target_dwell - elapsed)  # don't overshoot the target dwell time
        time.sleep(max(pause, 0.1))
        elapsed += pause


def build_driver() -> webdriver.Chrome:
    options = Options()                                                        # container for Chrome launch flags
    options.add_argument(f"--user-data-dir={PROFILE_DIR.resolve()}")            # point Chrome at our dedicated profile (has the LinkedIn login)
    options.add_argument("--profile-directory=Default")                        # use the "Default" sub-profile inside that user-data-dir
    options.add_argument("--window-size=1280,1000")                            # open at a fixed, reasonably large size
    return webdriver.Chrome(options=options)                                    # launch real Chrome with those options and return the controller


def text_or_none(el) -> str | None:
    if el is None:                                              # element wasn't found by the caller's selector
        return None
    return " ".join(el.get_text(strip=True).split()) or None    # get all text, collapse whitespace/newlines to single spaces


def get_job_ids_on_page(driver: webdriver.Chrome, keyword: str, start: int) -> list[str]:
    """Fetch just the job IDs for one page of results — no scrolling needed.
    Confirmed directly: LinkedIn renders all 25 li[data-occludable-job-id]
    wrappers in the DOM immediately on page load; only the inner card content
    (title/company/logo) lazy-renders on scroll, which we skip entirely since
    scrape_job_detail() gets everything for genuinely new jobs anyway."""
    search_url = f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&f_WT=2&start={start}"
    print(f"Navigating to: {search_url}")
    driver.get(search_url)
    check_not_blocked(driver)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "li[data-occludable-job-id]"))
        )
    except Exception:
        print("  WARNING: job list didn't appear within 15s — page may need manual inspection.")

    jitter()                                              # brief human-like pause before reading the DOM

    soup = BeautifulSoup(driver.page_source, "lxml")
    ul = soup.select_one("div.scaffold-layout__list-detail-inner div.scaffold-layout__list ul")  # exact container path, confirmed via direct inspection
    if not ul:
        print("  Results list container not found.")
        return []

    job_ids = [li.get("data-occludable-job-id") for li in ul.select("li[data-occludable-job-id]")]
    return [jid for jid in job_ids if jid]                  # drop any None values, just in case


def scrape_keyword(driver: webdriver.Chrome, keyword: str, max_pages: int | None = None) -> list[str]:
    """Page through &start=0, 25, 50, ... collecting job_ids until LinkedIn
    returns an empty page (i.e. genuinely out of results for this
    keyword+filter), rather than stopping at an arbitrary result count.
    `max_pages` is an optional safety ceiling for later (e.g. a smaller cap
    for routine daily runs, once that cadence is decided) — leave it None
    for "run until exhausted", which is what the initial backfill run wants.
    """
    all_job_ids: list[str] = []
    start = 0
    page_num = 1
    while True:
        if max_pages is not None and page_num > max_pages:
            print(f"  Reached max_pages={max_pages}, stopping pagination.")
            break

        print(f"\n--- Page {page_num} (start={start}) ---")
        job_ids = get_job_ids_on_page(driver, keyword, start)
        if not job_ids:
            print(f"  Page {page_num} returned no job ids — end of results for this keyword.")
            break

        print(f"  Page {page_num}: {len(job_ids)} job ids")
        all_job_ids.extend(job_ids)

        start += 25          # LinkedIn's confirmed per-page increment
        page_num += 1
        jitter(3.0, 6.0)      # longer pause between page loads than the brief pause within a page

    return all_job_ids


# Matches salary ranges like "$119K/yr - $173K/yr" or "$80/hr - $95/hr" —
# used to pick the salary button out of job-details-fit-level-preferences,
# which also contains non-salary buttons like "Remote" and "Full-time".
SALARY_PATTERN = re.compile(r"\$[\d,.]+\s*[kK]?(?:/yr|/hr|/year|/hour)?\s*-\s*\$[\d,.]+\s*[kK]?(?:/yr|/hr|/year|/hour)?")


def parse_top_card_info(soup: BeautifulSoup) -> tuple[str | None, str | None, str | None]:
    """Pulls location, posted-date text, and applicant-count text out of the
    tertiary description container under the job title. Matched by keyword
    content (" ago", "clicked apply"/"applicant"), not fixed span position,
    since not every job shows every span (e.g. "Promoted by hirer" isn't
    always present). The first span matching neither pattern is location,
    since location always renders first among these spans."""
    tertiary = soup.select_one("div.job-details-jobs-unified-top-card__tertiary-description-container")
    if not tertiary:
        return None, None, None

    location = None
    posted_date = None
    applicant_stats = None
    for span in tertiary.select("span.tvm__text"):
        # LinkedIn's nested <span> structure drops the space between e.g.
        # "Reposted" and "1 week ago" when concatenated by get_text() — add
        # it back wherever a letter is immediately followed by a digit.
        text = re.sub(r"(?<=[a-zA-Z])(?=\d)", " ", span.get_text(strip=True))
        if not text or text == "·":               # skip empty spans and the "·" bullet separators
            continue
        low = text.lower()
        if "ago" in low:
            posted_date = text
        elif "clicked apply" in low or "applicant" in low:
            applicant_stats = text
        elif location is None:                     # first span matching neither pattern = location
            location = text

    return location, posted_date, applicant_stats


def parse_salary(soup: BeautifulSoup) -> str | None:
    """job-details-fit-level-preferences holds a row of buttons (salary,
    work type, job type) but not every job has a salary one — check each
    button's text against SALARY_PATTERN rather than assuming position."""
    fit_level = soup.select_one("div.job-details-fit-level-preferences")
    if not fit_level:
        return None
    for btn in fit_level.select("button"):
        text = btn.get_text(strip=True)
        if SALARY_PATTERN.search(text):
            return text
    return None


def scrape_job_detail(driver: webdriver.Chrome, keyword: str, job_id: str) -> dict:
    """Fetch everything for one job — title, company, location, industry,
    company size, full JD text, posted date, applicant stats, salary — from
    a single page load of the search-results two-pane view (by adding
    &currentJobId=<id> to the same search URL) rather than navigating to the
    standalone /jobs/view/<id>/ page. The standalone page renders with
    hashed/unstable CSS classes (confirmed by direct investigation); the
    two-pane view renders the same content with stable, semantic class names
    (jobs-company, job-details, jobs-company__inline-information, etc).
    """
    url = f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&f_WT=2&currentJobId={job_id}"
    driver.get(url)                                        # load the search page with this specific job pre-selected in the right-hand pane
    check_not_blocked(driver)                                # same block/challenge check as the list scrape
    simulate_reading(driver)                                 # 5-20s of human-like scrolling instead of a short fixed jitter — this is the page we actually "read"

    soup = BeautifulSoup(driver.page_source, "lxml")

    title = text_or_none(soup.select_one("h1"))                                              # confirmed: job title renders as the page's h1
    company = text_or_none(soup.select_one("div.job-details-jobs-unified-top-card__company-name"))  # confirmed: stable class for company name

    jd_container = soup.select_one("div#job-details")       # stable ID for the job description block
    raw_text = jd_container.get_text(separator=" ", strip=True) if jd_container else None

    industry = None
    company_size = None
    company_section = soup.select_one("section.jobs-company")  # the "About the company" mini-card in the same pane
    if company_section:
        info_div = company_section.select_one("div.t-14.mt5")   # holds industry as its own direct text, plus size/follower spans
        if info_div:
            industry_text = info_div.find(string=True, recursive=False)  # direct text only, not the child spans below
            industry = industry_text.strip() if industry_text else None
            for span in info_div.select("span.jobs-company__inline-information"):
                text = span.get_text(strip=True)
                if "employee" in text.lower():               # distinguishes "11-50 employees" from the other span, "13 on LinkedIn"
                    company_size = text

    location, posted_date, applicant_stats = parse_top_card_info(soup)
    salary_text = parse_salary(soup)

    return {
        "job_id": job_id,
        "url": f"https://www.linkedin.com/jobs/view/{job_id}/",  # clean canonical URL, stable across scrapes (no tracking params)
        "title": title,
        "company": company,
        "location": location,
        "raw_text": raw_text,
        "industry": industry,
        "company_size": company_size,
        "posted_date": posted_date,
        "applicant_stats": applicant_stats,
        "salary_text": salary_text,
    }


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "machine learning"  # take keyword from command line, or default

    if not PROFILE_DIR.exists():                                         # the profile must already exist (created by setup_chrome_profile.py)
        print(f"No Chrome profile found at {PROFILE_DIR}. Run setup_chrome_profile.py first.")
        sys.exit(1)                                                       # exit with a non-zero code — signals failure to the shell

    driver = build_driver()                                               # launch Chrome
    try:
        # NOTE: max_pages=2 here is just to keep this manual test run quick.
        # scrape_keyword's real default is max_pages=None (page until
        # LinkedIn is exhausted) — that's what the actual backfill run uses.
        job_ids = scrape_keyword(driver, keyword, max_pages=2)
    except LinkedInBlockedError as e:
        # note: no driver.quit() here — the `finally` below always runs next
        # and handles it, even though we're about to sys.exit()
        if e.reason == "logged_out":
            print("LinkedIn session expired/invalidated — re-run setup_chrome_profile.py to log in again.")
        else:
            print("LinkedIn served a security checkpoint/challenge page. Stopping now — do NOT retry immediately, "
                  "that's what actually gets accounts flagged. Wait and try again later (e.g. next scheduled run).")
        sys.exit(2)                                                       # distinct exit code so a future orchestrator script can tell this apart from other failures
    finally:
        driver.quit()                                                    # always close Chrome, even if scraping raised an error

    print(f"\nFound {len(job_ids)} job ids for '{keyword}': {job_ids}")

    # Proof-of-concept: fetch full detail for the first 2 job_ids found. In
    # the real pipeline (once the DB exists), this step only runs for
    # job_ids NOT already stored — that's the "quickly scan ids, skip if
    # already known" check. Here, without a DB yet, just capped to 2 jobs
    # for a quick manual test (each one now takes 5-20s of simulated reading).
    results = []
    driver2 = build_driver()
    try:
        for job_id in job_ids[:2]:
            print(f"\n--- Fetching detail for job_id={job_id} ---")
            detail = scrape_job_detail(driver2, keyword, job_id)
            results.append(detail)
            print(f"  title: {detail['title']!r} @ {detail['company']!r}")
            print(f"  location: {detail['location']}")
            print(f"  industry: {detail['industry']}")
            print(f"  company_size: {detail['company_size']}")
            print(f"  posted_date: {detail['posted_date']}")
            print(f"  applicant_stats: {detail['applicant_stats']}")
            print(f"  salary_text: {detail['salary_text']}")
            print(f"  raw_text length: {len(detail['raw_text']) if detail['raw_text'] else 0} chars")
    except LinkedInBlockedError as e:
        print(f"Blocked while fetching detail ({e.reason}) — stopping detail fetch.")
    finally:
        driver2.quit()

    OUTPUT_FILE.write_text(json.dumps(results, indent=2))                 # save the full list as pretty-printed JSON
    print(f"\nFull results saved to {OUTPUT_FILE}")


if __name__ == "__main__":                                                # only run main() when executed directly (not when imported)
    main()
