"""
LinkedIn job search scraper.

Architecture: LinkedIn renders all 25 job_id wrappers
(li[data-occludable-job-id]) in the DOM immediately on page load, but the
list itself is virtualized — a card's title text only renders once that
card has scrolled near the viewport at least once (confirmed via direct
inspection, 2026-07-06: the first ~7 cards have a title on load, the rest
come back with an empty <strong> until scrolled to; once rendered a
title stays populated even after scrolling past it). get_job_cards_on_page()
scrolls the whole list to the bottom, then pulls job_id + title for all 25
cards, so callers can filter out off-track titles (analysis/title_filter.py)
before ever spending a full detail-page fetch on a job. Only for titles
that pass that filter do we fetch the full two-pane detail view (company,
location, industry, company size, raw JD text, posted date, applicant
stats, salary — all from one page load).

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

SORT_BY_MOST_RECENT = "DD"    # LinkedIn's sortBy param for newest-posted-first (default "R" = relevance, which reshuffles between page loads)
TIME_RANGE_DAY = "r86400"     # f_TPR: last 24 hours — routine daily scrape
TIME_RANGE_MONTH = "r2592000"  # f_TPR: last 30 days — one-time initial backfill


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


def simulate_list_browsing(driver: webdriver.Chrome) -> None:
    """Scroll the results list all the way to the bottom so every one of
    this page's 25 cards renders its title. Confirmed via direct inspection
    (2026-07-06): LinkedIn's job-card list is virtualized — only cards near
    the viewport have title text in the DOM at all (cards further down come
    back with an empty <strong>) — but once a card has rendered, its title
    stays populated even after scrolling past it, so a single top-to-bottom
    pass is enough; no need to re-check earlier cards.

    The actual scrollable element is the <ul>'s parent div, not
    div.scaffold-layout__list itself (that one reported scrollHeight ==
    clientHeight — not scrollable — while scrolling it silently did
    nothing). Ember gives the real scrollable div a hashed, unstable class
    name each session, so it's located structurally (ul.parentElement)
    rather than by class.

    Movement is randomized (direction/distance/pauses), like
    simulate_reading(), just biased more strongly downward since the goal
    here is full coverage of the page's cards, not idle browsing.
    """
    container = driver.execute_script(
        "const ul = document.querySelector('div.scaffold-layout__list ul'); "
        "return ul ? ul.parentElement : null;"
    )
    if container is None:
        print("  WARNING: list scroll container not found — card titles may not render.")
        return

    max_scroll = driver.execute_script(
        "return arguments[0].scrollHeight - arguments[0].clientHeight;", container
    )
    if max_scroll <= 0:
        return  # fewer cards than fit on one screen — nothing to scroll

    scrolled = 0
    iterations = 0
    while scrolled < max_scroll and iterations < 40:  # iteration cap is just a safety backstop against an unexpected page state
        direction = 1 if random.random() < 0.85 else -1   # mostly down, occasional up — same anti-detection idea as simulate_reading()
        step = direction * random.randint(250, 400)       # smaller steps than a first pass at this — more overlap between steps gives each card's async render more of a chance to finish before we move past it
        driver.execute_script("arguments[0].scrollTop += arguments[1];", container, step)
        scrolled = driver.execute_script("return arguments[0].scrollTop;", container)
        time.sleep(random.uniform(0.9, 1.8))              # longer than the original 0.6-1.4s — that pace still occasionally scrolled past a card before its title finished rendering (see retry_missing_titles for the backstop)
        iterations += 1

    driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)  # guarantee the last card rendered even if the jittered loop undershot
    time.sleep(1.0)


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


def parse_job_card(li) -> dict:
    """Extract job_id + title from one list-page card. The list is
    virtualized (confirmed via direct inspection, 2026-07-06) — a card's
    title is only in the DOM once that card has scrolled near the viewport
    at least once (see simulate_list_browsing), so title comes back None
    for any card not yet rendered. Pulled from the <strong> inside
    a.job-card-list__title--link rather than that link's aria-label or
    sibling visually-hidden span, both of which append a " with
    verification" suffix for LinkedIn-verified postings that <strong>'s own
    text doesn't have."""
    job_id = li.get("data-occludable-job-id")
    title = None
    title_link = li.select_one("a.job-card-list__title--link")
    if title_link:
        strong = title_link.select_one("strong")
        title = text_or_none(strong) if strong else text_or_none(title_link)
    return {"job_id": job_id, "title": title}


def get_job_cards_on_page(driver: webdriver.Chrome, keyword: str, start: int, time_range: str = TIME_RANGE_DAY) -> list[dict]:
    """Fetch job_id + title for one page of results. All 25
    li[data-occludable-job-id] wrappers are in the DOM immediately on page
    load, but the list is virtualized — a card's title text only renders
    once that card has scrolled near the viewport at least once (confirmed
    via direct inspection, 2026-07-06), so simulate_list_browsing() scrolls
    all the way to the bottom before parsing, not just a light jitter.
    Returning title here (not just the id) is what lets the caller drop
    off-track jobs (analysis/title_filter.py) before ever spending a full
    detail-page fetch on them. `sortBy=DD` (newest first) + `f_TPR` (time
    window) keep this page's results scoped to a small, newest-first slice
    instead of LinkedIn's whole relevance-ranked pool — the fix for
    already-seen jobs reappearing on later pages as that pool shifts mid-run.
    """
    search_url = (
        f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&f_WT=2"
        f"&sortBy={SORT_BY_MOST_RECENT}&f_TPR={time_range}&start={start}"
    )
    print(f"Navigating to: {search_url}")
    driver.get(search_url)
    check_not_blocked(driver)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "li[data-occludable-job-id]"))
        )
    except Exception:
        print("  WARNING: job list didn't appear within 15s — page may need manual inspection.")

    # The wait above only confirms the card *wrappers* exist — their title
    # text is a separate async render that can lag behind. Waiting for the
    # first card's title specifically (not just presence of the <li>s) gives
    # that render pipeline a moment to warm up before we start scrolling
    # away from it, which cuts down on how often retry_missing_titles has
    # to run at all.
    try:
        WebDriverWait(driver, 6).until(
            lambda d: d.find_element(By.CSS_SELECTOR, "a.job-card-list__title--link strong").text.strip() != ""
        )
    except Exception:
        print("  WARNING: first card's title didn't populate within 6s — list may be rendering slower than usual this run.")

    simulate_list_browsing(driver)                        # scrolls to the bottom — required to render every card's title, see docstring

    soup = BeautifulSoup(driver.page_source, "lxml")
    ul = soup.select_one("div.scaffold-layout__list-detail-inner div.scaffold-layout__list ul")  # exact container path, confirmed via direct inspection
    if not ul:
        print("  Results list container not found.")
        return []

    cards = [parse_job_card(li) for li in ul.select("li[data-occludable-job-id]")]
    cards = [card for card in cards if card["job_id"]]        # drop any None job_ids, just in case
    return retry_missing_titles(driver, cards)


def retry_missing_titles(driver: webdriver.Chrome, cards: list[dict], max_attempts: int = 2) -> list[dict]:
    """A card occasionally still comes back with no title even after the
    full scroll pass — its async render just hadn't finished before we
    scrolled past it. Left alone, that title=None would make
    is_relevant_title() treat a possibly-relevant job as off-track and drop
    it, so retry rather than accept the loss: scroll that specific card
    back into view directly (scrollIntoView, not the general list scroll).

    Parses each card's title immediately after scrolling to it, one at a
    time — NOT scroll-to-everything-then-snapshot-once. Confirmed via real
    scrape logs (2026-07-07): the list only keeps a small window of cards
    mounted at a time, so scrolling to card #2 can evict card #1's
    just-rendered title before a single end-of-loop snapshot would ever
    see it — a whole batch of retries can silently fail together this way
    if the missing cards are spread across a wide scroll range.
    """
    cards_by_id = {card["job_id"]: card for card in cards}
    for attempt in range(max_attempts):
        missing_ids = [c["job_id"] for c in cards if c["title"] is None]
        if not missing_ids:
            break
        print(f"  {len(missing_ids)} card(s) missing a title — retrying (attempt {attempt + 1}/{max_attempts})...")
        for job_id in missing_ids:
            selector = f'li[data-occludable-job-id="{job_id}"]'
            outer_html = driver.execute_script(
                "const li = document.querySelector(arguments[0]); "
                "if (!li) return null; "
                "li.scrollIntoView({block: 'center'}); "
                "return li.outerHTML;",
                selector,
            )
            time.sleep(1.0)
            if outer_html is None:
                continue
            # re-read straight after the wait (not the outer_html captured
            # before it) so the pause has a chance to let the render finish
            outer_html = driver.execute_script(
                "const li = document.querySelector(arguments[0]); return li ? li.outerHTML : null;", selector
            )
            if outer_html:
                li_soup = BeautifulSoup(outer_html, "lxml").select_one("li")
                if li_soup:
                    cards_by_id[job_id]["title"] = parse_job_card(li_soup)["title"]

    still_missing = [c["job_id"] for c in cards if c["title"] is None]
    if still_missing:
        print(f"  WARNING: {len(still_missing)} card(s) still have no title after {max_attempts} retries: {still_missing}")
    return cards


def scrape_keyword(
    driver: webdriver.Chrome, keyword: str, max_pages: int | None = None, time_range: str = TIME_RANGE_DAY
) -> list[dict]:
    """Page through &start=0, 25, 50, ... collecting job cards (id + title)
    until LinkedIn returns an empty page (i.e. genuinely out of results for
    this keyword+filter), rather than stopping at an arbitrary result count.
    `max_pages` is an optional safety ceiling for later (e.g. a smaller cap
    for routine daily runs, once that cadence is decided) — leave it None
    for "run until exhausted", which is what the initial backfill run wants.
    """
    all_cards: list[dict] = []
    start = 0
    page_num = 1
    while True:
        if max_pages is not None and page_num > max_pages:
            print(f"  Reached max_pages={max_pages}, stopping pagination.")
            break

        print(f"\n--- Page {page_num} (start={start}) ---")
        cards = get_job_cards_on_page(driver, keyword, start, time_range)
        if not cards:
            print(f"  Page {page_num} returned no job ids — end of results for this keyword.")
            break

        print(f"  Page {page_num}: {len(cards)} job ids")
        all_cards.extend(cards)

        start += 25          # LinkedIn's confirmed per-page increment
        page_num += 1
        jitter(3.0, 6.0)      # longer pause between page loads than the brief pause within a page

    return all_cards


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


def scrape_job_detail(driver: webdriver.Chrome, keyword: str, job_id: str, time_range: str = TIME_RANGE_DAY) -> dict:
    """Fetch everything for one job — title, company, location, industry,
    company size, full JD text, posted date, applicant stats, salary — from
    a single page load of the search-results two-pane view (by adding
    &currentJobId=<id> to the same search URL) rather than navigating to the
    standalone /jobs/view/<id>/ page. The standalone page renders with
    hashed/unstable CSS classes (confirmed by direct investigation); the
    two-pane view renders the same content with stable, semantic class names
    (jobs-company, job-details, jobs-company__inline-information, etc).
    """
    url = (
        f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&f_WT=2"
        f"&sortBy={SORT_BY_MOST_RECENT}&f_TPR={time_range}&currentJobId={job_id}"
    )
    driver.get(url)                                        # load the search page with this specific job pre-selected in the right-hand pane
    check_not_blocked(driver)                                # same block/challenge check as the list scrape
    simulate_reading(driver)                                 # 3-15s of human-like scrolling instead of a short fixed jitter — this is the page we actually "read"

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
        cards = scrape_keyword(driver, keyword, max_pages=2)
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

    print(f"\nFound {len(cards)} job cards for '{keyword}':")
    for card in cards:
        print(f"  {card['job_id']}: {card['title']!r}")

    # Proof-of-concept: fetch full detail for the first 2 cards found. In
    # the real pipeline (once the DB exists), this step only runs for
    # job_ids NOT already stored and whose title passed the relevance filter
    # — that's the "quickly scan ids, skip if already known or off-track"
    # check. Here, without a DB yet, just capped to 2 jobs for a quick manual
    # test (each one now takes 3-15s of simulated reading).
    results = []
    driver2 = build_driver()
    try:
        for card in cards[:2]:
            job_id = card["job_id"]
            print(f"\n--- Fetching detail for job_id={job_id} ({card['title']!r}) ---")
            detail = scrape_job_detail(driver2, keyword, job_id, TIME_RANGE_DAY)
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
