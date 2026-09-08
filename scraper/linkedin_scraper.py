"""
LinkedIn job search scraper.

Architecture (rebuilt 2026-08-14 — LinkedIn reworked the list page's DOM
entirely, breaking every selector this module previously used): each list
card is now a div[role="button"] whose componentkey attribute embeds the
job id directly ("job-card-component-ref-<id>") — no real <a href> in the
card at all, navigation is a JS click handler. Title comes from a same-card
"Dismiss <title> job" button's aria-label, not the card's own visible
title text (which sometimes has a "(Verified job)" suffix). Confirmed via
direct inspection: unlike the old list, this one is NOT virtualized for
text content — all 25 cards have full title/company available immediately
on page load, no scroll-to-render wait needed (get_job_cards_on_page()
still does one lightweight scroll as cheap insurance against the automated
Selenium session rendering slower than the live interactive session this
was verified against). See parse_job_card() for the extraction logic.
get_job_cards_on_page() pulls job_id + title + company for all 25 cards, so
callers can filter out off-track titles (analysis/title_filter.py) and
known agency/staffing postings (judge/agency_blocklist.py, matched by
company name substring) before ever spending a full detail-page fetch on a
job. Only for jobs that pass both filters do we fetch the full two-pane
detail view (company, location, industry, company size, raw JD text,
posted date, applicant stats, salary — all from one page load).

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

import undetected_chromedriver as uc            # patched chromedriver + navigator.webdriver/cdc_ suppression (Chrome path only, see build_driver)
from bs4 import BeautifulSoup                   # parses raw HTML into a searchable tree
from selenium import webdriver                  # drives an actual Chrome/Edge browser
from selenium.common.exceptions import TimeoutException  # raised by set_page_load_timeout() instead of a raw socket timeout
from selenium.webdriver.chrome.options import Options   # Chrome launch flags (profile dir, window size)
from selenium.webdriver.edge.options import Options as EdgeOptions  # Edge launch flags — same flag names as Chrome (both Chromium-based); Selenium Manager auto-resolves a matching msedgedriver, no separate driver install needed
from selenium.webdriver.common.by import By      # "how to locate an element" enum (CSS, XPath, etc.)
from selenium.webdriver.support import expected_conditions as EC  # wait-until conditions
from selenium.webdriver.support.ui import WebDriverWait           # explicit "wait up to N seconds" helper

PROFILE_DIR = Path(__file__).parent / ".chrome-profile"   # the dedicated Chrome profile dir from setup_chrome_profile.py
EDGE_PROFILE_DIR = Path(__file__).parent / ".edge-profile"  # separate dedicated Edge profile dir — Chrome and Edge can't share a user-data-dir, each needs its own logged-in LinkedIn session (see setup_chrome_profile.py --browser edge)
OUTPUT_FILE = Path(__file__).parent / "sample_output.json"  # where this test run's results get saved

SORT_BY_MOST_RECENT = "DD"    # LinkedIn's sortBy param for newest-posted-first (default "R" = relevance, which reshuffles between page loads)
TIME_RANGE_DAY = "r86400"     # f_TPR: last 24 hours — routine daily scrape
TIME_RANGE_WEEK = "r604800"   # f_TPR: last 7 days — one-off catch-up scrape (e.g. after missed daily runs)
TIME_RANGE_MONTH = "r2592000"  # f_TPR: last 30 days — one-time initial backfill

# f_WT: LinkedIn's work-type filter. 1=On-site, 2=Remote, 3=Hybrid — comma-separated for multi-select.
WORK_TYPE_REMOTE = "2"
WORK_TYPE_HYBRID_ONSITE = "1,3"


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


class BroadMatchDegradedError(Exception):
    """Raised when a broad-match keyword's very first page (start=0) doesn't
    look like a healthy broad-match search. See CONTEXT.md's throttling
    note: a heavily-used session can silently degrade a broad-match query
    down toward narrow-literal-match behavior — same URL, same endpoint, no
    error, just a much smaller/less-remote result set. Checked once per
    keyword (start=0 only, not every page — a session that degrades mid-
    keyword wouldn't be caught by a start-of-run check anyway, and checking
    every page would be expensive for no benefit): (1) still actually on
    /jobs/search-results/, not silently redirected elsewhere, (2) the
    results-count header still says "99+" (a degraded session reported far
    fewer), (3) at least 18 of this page's 25 cards are workplace_hint ==
    "Remote" (a healthy "llm remote" search ran ~96% clean across two live-
    checked pages; a real regression should fail well below that, not just
    dip slightly). The right response is to stop this keyword's run
    entirely and surface it, not silently continue on bad data.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


DEBUG_DUMP_DIR = Path(__file__).parent / "logs" / "debug_dumps"


def _dump_diagnostics(driver: webdriver.Chrome, label: str) -> None:
    """Called when a selector-dependent step comes up empty in a way
    check_not_blocked() doesn't catch (URL looks fine — not /checkpoint/,
    /authwall/, /login — but the expected content isn't there either).
    check_not_blocked() only catches an explicit redirect to a block page;
    it can't tell "genuinely logged in and rendering normally" apart from
    "logged in, same URL, but served a stripped/degraded page" — which is a
    real, separately-observed LinkedIn behavior (confirmed 2026-08-14 while
    debugging the browser extension: a standalone job-detail page reached
    via direct/automated navigation came back with almost no content, no
    explicit block, while the same job loaded fine through organic
    in-app navigation). Dumps current URL, title, a body-text snippet (to
    the log — cheap, always readable) and the full page source (to a
    timestamped file — only when something's actually wrong, so this
    doesn't bloat storage on normal runs) so a failure like this is
    diagnosable from the log alone next time, without needing a live
    session to inspect.
    """
    try:
        url = driver.current_url
        title = driver.title
        body_text = driver.execute_script("return document.body ? document.body.innerText : ''") or ""
        print(f"  DEBUG [{label}]: url={url!r} title={title!r} body_text_len={len(body_text)}")
        print(f"  DEBUG [{label}]: body_text_snippet={body_text[:300]!r}")

        DEBUG_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        dump_path = DEBUG_DUMP_DIR / f"{label}_{time.strftime('%Y%m%d_%H%M%S')}.html"
        dump_path.write_text(driver.page_source, encoding="utf-8")
        print(f"  DEBUG [{label}]: full page source saved to {dump_path}")
    except Exception as e:
        print(f"  DEBUG [{label}]: diagnostics collection itself failed: {e}")


def jitter(a: float = 1.5, b: float = 5) -> None:
    time.sleep(random.uniform(a, b))        # sleep a random amount between a and b seconds — avoids robotic fixed-interval timing


# Chances/durations for maybe_take_a_break(). Scaled down from the original
# tuning once simulate_reading() switched to a skewed per-job dwell (most
# jobs get a 2-8s glance, a minority get a genuine 20-90s read, ~20s/job
# average vs. the old flat ~9s/job) — that skew already supplies a lot of
# the "this doesn't run at a constant pace" signal breaks were compensating
# for, so stacking the original large break budget on top would push a
# 500-job run well past the ~3h target. New expected value per page:
# 0.05*avg(330) + 0.15*avg(75) ≈ 27.75s/page (was ~240s/page).
BREAK_LONG_CHANCE = 0.05
BREAK_LONG_RANGE = (180.0, 480.0)   # 3-8 min — a "stepped away" break
BREAK_SHORT_CHANCE = 0.15
BREAK_SHORT_RANGE = (30.0, 120.0)   # 0.5-2 min — a shorter pause


def maybe_take_a_break() -> None:
    """Randomly pause for much longer than the routine per-page jitter,
    mimicking a real person stepping away mid-session (coffee, email,
    getting distracted) rather than a script that runs at a constant pace
    for hours straight. Meant to be called once between pages: most calls
    do nothing (80% chance of neither branch firing), sometimes a short
    break, occasionally a long one — see the module-level BREAK_* constants
    for the expected-value math behind the specific numbers.
    """
    roll = random.random()
    if roll < BREAK_LONG_CHANCE:
        duration = random.uniform(*BREAK_LONG_RANGE)
        print(f"  ...taking a longer break ({duration / 60:.1f} min) before continuing...")
        time.sleep(duration)
    elif roll < BREAK_LONG_CHANCE + BREAK_SHORT_CHANCE:
        duration = random.uniform(*BREAK_SHORT_RANGE)
        print(f"  ...taking a short break ({duration:.0f}s) before continuing...")
        time.sleep(duration)


# Real job-search browsing is bimodal, not evenly spread: most opened
# postings get a quick skim (title/comp/requirements don't match, move on),
# a minority actually look promising and get read closely. A flat
# uniform(3, 15) for every job (the old model) is more even/robotic than a
# real searcher ever is — see simulate_reading().
SHORT_GLANCE_CHANCE = 0.7          # fraction of opened jobs that are just a quick skim
SHORT_GLANCE_RANGE = (2.0, 8.0)    # seconds
LONG_READ_RANGE = (20.0, 90.0)     # seconds — the minority that get genuinely read


def simulate_reading(driver: webdriver.Chrome) -> None:
    """Scroll through the job description pane in small increments over a
    randomized dwell time, mimicking someone actually reading the posting
    rather than a script that loads the page and immediately scrapes it.
    Scrolls the actual nested scrollable container
    (div.jobs-search__job-details--wrapper), not window — the two-pane
    detail view's right-hand pane scrolls independently of the outer page
    (confirmed via direct inspection: scrollHeight 5663 vs clientHeight 748).

    Direction, distance, and pause length are all randomized independently —
    including occasional scroll-UP moves, not just monotonic downward
    scrolling, since a real reader re-checks something above far more often
    than a script would ever think to. Scrolling down still wins on average
    (65% of moves) so we make net forward progress through the posting.
    """
    if random.random() < SHORT_GLANCE_CHANCE:
        target_dwell = random.uniform(*SHORT_GLANCE_RANGE)
    else:
        target_dwell = random.uniform(*LONG_READ_RANGE)
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


PAGE_LOAD_TIMEOUT_SECONDS = 60  # bounds driver.get() — without this, a hung page load (LinkedIn interstitial that never fires "load", a stuck renderer) blocks until urllib3's own client-side socket read timeout (120s) fires a raw, hard-to-catch ReadTimeoutError instead of a clean, catchable selenium TimeoutException


# Deletes the property from Navigator.prototype entirely (not just
# overriding it on the instance) so 'webdriver' in navigator reads false —
# matching a genuinely non-automated browser, where the key is absent, not
# merely undefined. A plain Object.defineProperty(navigator, 'webdriver',
# {get: () => undefined}) still leaves the key present (in-check still
# true), which is itself a residual tell.
_HIDE_WEBDRIVER_CDP_SCRIPT = "delete Object.getPrototypeOf(navigator).webdriver"


def build_driver(browser: str = "chrome") -> webdriver.Chrome | webdriver.Edge:
    """browser: "chrome" (default) or "edge" — an alternative when Chrome
    itself is busy/unavailable, or to spread automated traffic across two
    different browser fingerprints rather than always presenting as Chrome.
    Edge has no "undetected-edgedriver" equivalent to undetected_chromedriver
    (Chrome-specific binary patching), so its stealth comes from manually
    injecting the same navigator.webdriver override via a CDP command instead
    — Edge is Chromium-based, so Selenium's CDP passthrough (execute_cdp_cmd)
    works on it the same way it does on Chrome. The cdc_ window-variable
    fingerprint (see undetected_chromedriver) isn't addressed on the Edge
    path since nothing here patches the msedgedriver binary itself; Chrome
    remains the more thoroughly hardened option."""
    if browser == "edge":
        options = EdgeOptions()
        options.add_argument(f"--user-data-dir={EDGE_PROFILE_DIR.resolve()}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--window-size=1280,1000")
        driver = webdriver.Edge(options=options)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _HIDE_WEBDRIVER_CDP_SCRIPT})
    elif browser == "chrome":
        options = uc.ChromeOptions()                                                # container for Chrome launch flags
        options.add_argument(f"--user-data-dir={PROFILE_DIR.resolve()}")            # point Chrome at our dedicated profile (has the LinkedIn login)
        options.add_argument("--profile-directory=Default")                        # use the "Default" sub-profile inside that user-data-dir
        options.add_argument("--window-size=1280,1000")                            # open at a fixed, reasonably large size
        driver = uc.Chrome(options=options, version_main=150)                      # patched chromedriver: navigator.webdriver reads undefined, cdc_ window vars suppressed; pinned to installed Chrome's major version (auto-detect grabbed a mismatched newer chromedriver otherwise)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _HIDE_WEBDRIVER_CDP_SCRIPT})  # uc's own patching leaves navigator.webdriver as a defined `false` rather than truly absent — this closes that gap the same way as the Edge path
    else:
        raise ValueError(f"Unknown browser {browser!r} — expected 'chrome' or 'edge'")

    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
    return driver


def safe_get(driver: webdriver.Chrome, url: str, retries: int = 2) -> None:
    """driver.get() bounded by PAGE_LOAD_TIMEOUT_SECONDS (see build_driver),
    retried on a hung/slow page load rather than failing the whole scrape
    run on one bad navigation — a transient LinkedIn slowdown or interstitial
    is common enough over an hour-plus run that it shouldn't be fatal. Lets
    TimeoutException propagate after `retries` attempts so the caller (which
    already knows whether "this one job" or "this whole run" should be
    skipped) decides what happens next, rather than deciding that here."""
    for attempt in range(retries + 1):
        try:
            driver.get(url)
            return
        except TimeoutException:
            if attempt == retries:
                raise
            print(f"  Page load timed out (attempt {attempt + 1}/{retries + 1}) — retrying: {url}")
            jitter(2.0, 4.0)


def text_or_none(el) -> str | None:
    if el is None:                                              # element wasn't found by the caller's selector
        return None
    return " ".join(el.get_text(strip=True).split()) or None    # get all text, collapse whitespace/newlines to single spaces


_CARD_LIST_SELECTOR = 'div[role="button"][componentkey^="job-card-component-ref-"]'

# LinkedIn also still serves this older card template on some searches/
# sessions — confirmed 2026-08-14 by dumping a real automated run's page
# source: a "5 results" search returned zero componentkey cards, but its
# actual rendered DOM (verified by walking up from the title text node,
# past a hidden hydration <code> blob that happened to contain the same
# text) used this classic structure instead. The componentkey template was
# separately live-verified against a 1000+-result search the same day, so
# both are real and apparently split by search/session, not a stale one
# replacing a live one — check for both rather than assuming either.
#
# This classic template only ever renders 7 cards per fetch — its own
# in-page "1 2 3 … Next" pager, not the 25-per-page the componentkey
# template and get_job_cards_on_page's `start` stepping were built around.
# Confirmed live 2026-08-14 this does NOT skip results: start=0/25/50 map
# 1:1 onto the pager's own pages 1/2/3 and returned three disjoint,
# non-overlapping sets of 7 job ids with no gap — LinkedIn is reinterpreting
# `start` as "internal page number" for this template rather than a literal
# result offset, so the existing start+=25 loop still walks every result in
# order, just 7 at a time instead of 25 (more iterations, not missed pages).
_CARD_LIST_SELECTOR_CLASSIC = 'div.job-card-container[data-job-id]'

# Matches the metadata clutter that shows up as a "leaf" text node alongside
# company name in a card — benefit/alumni counts, applicant status, salary,
# posted-date, bare separators — so parse_job_card can skip past all of it
# to find the actual company name without depending on a fixed leaf index
# (not every card shows the same set of badges).
_CARD_NOISE_PATTERN = re.compile(
    r"school alumni|\bbenefit|\bapplicant|actively review|early applicant|"
    r"\bago\b|verified job|^\$|^·$|^•$",
    re.IGNORECASE,
)


_CARD_WORKPLACE_PATTERN = re.compile(r"\((Remote|Hybrid|On-site)\)")


def _extract_card_workplace_hint(card) -> str | None:
    """"Remote"/"Hybrid"/"On-site" if the card's own visible text shows a
    "(Remote)" etc. tag next to its location, else None.

    Added 2026-08-17 after finding LinkedIn's broad-match "Remote" filter
    (see click_remote_filter) is not reliably applied — confirmed live: a
    fresh, correctly-filtered search can still include a genuinely on-site
    posting, and this is non-deterministic (the same flow run minutes apart
    produced 15/15 remote once and had leaked non-remote postings another
    time). A page-level "did the filter work" check can't catch this
    because it silently varies job-by-job even on a page that otherwise
    looks correctly filtered. This card-level tag is the same information
    LinkedIn's own detail page displays (verified against real postings),
    and reading it here lets the caller skip a job before ever paying for
    its expensive full detail-page fetch — the fix for "capturing non-
    remote jobs is fine, we'll filter later" being too slow in practice.
    """
    match = _CARD_WORKPLACE_PATTERN.search(card.get_text(" ", strip=True))
    return match.group(1) if match else None


def parse_job_card(card) -> dict:
    """Extract job_id + title + company from one list-page card.

    LinkedIn rebuilt the list page (confirmed via direct inspection,
    2026-08-14) — every old selector here (li[data-occludable-job-id],
    a.job-card-list__title--link, div.artdeco-entity-lockup__subtitle)
    returns zero matches now. New structure: a card is div[role="button"]
    whose componentkey attribute embeds the job id directly
    ("job-card-component-ref-<id>") — no real <a href> anywhere in the
    card, navigation is a JS click handler, not a link. Title comes from a
    same-card "Dismiss <title> job" button's aria-label rather than the
    card's own visible title text, which sometimes has a "(Verified job)"
    suffix the aria-label doesn't. Company has no dedicated class the way
    the old artdeco-entity-lockup__subtitle was — extracted as the first
    leaf-text node that isn't the title (or the title with "(Verified
    job)" appended) and doesn't match _CARD_NOISE_PATTERN.
    """
    component_key = card.get("componentkey", "") or ""
    raw_job_id = component_key.replace("job-card-component-ref-", "") or None
    # see parse_job_card_classic's docstring — a non-job widget with a
    # non-numeric id sentinel got through this template's sibling parser
    # once already; reject anything non-digit here too, defensively.
    job_id = raw_job_id if raw_job_id and raw_job_id.isdigit() else None

    title = None
    dismiss_btn = card.select_one('button[aria-label^="Dismiss "]')
    if dismiss_btn:
        label = dismiss_btn.get("aria-label", "") or ""
        if label.startswith("Dismiss ") and label.endswith(" job"):
            title = label[len("Dismiss "):-len(" job")]

    company = None
    for el in card.find_all(True):
        if el.find(True):          # has a child tag — not a leaf, skip (avoids double-counting nested text)
            continue
        text = text_or_none(el)
        if not text:
            continue
        if title and title in text:                # the title itself, possibly "<title> (Verified job)"
            continue
        if _CARD_NOISE_PATTERN.search(text):
            continue
        company = text
        break

    return {"job_id": job_id, "title": title, "company": company, "workplace_hint": _extract_card_workplace_hint(card)}


def parse_job_card_classic(card) -> dict:
    """Extract job_id + title + company from a classic-template card
    (see _CARD_LIST_SELECTOR_CLASSIC). job_id is a plain data attribute;
    title is the title link's aria-label with a trailing "with
    verification" suffix stripped (the visible text has it, the old
    componentkey template's equivalent suffix was "(Verified job)" —
    different wording, same badge); company is the entity-lockup subtitle,
    which this template does have a dedicated class for (unlike the
    componentkey template).

    job_id must be all-digits: a real DB row (id=12403) turned up with
    job_id='search', title=None, company=None, url='.../jobs/view/search/'
    — some non-job widget LinkedIn mixes into the list (a "refine your
    search" prompt or similar) reuses the job-card-container class with a
    literal "search" sentinel instead of a numeric data-job-id, and it
    passed the old `if job_id` truthy check since a non-empty string isn't
    falsy. Real LinkedIn job ids are always digit strings, so reject
    anything that isn't before it reaches the DB unique-constraint layer.
    """
    raw_job_id = card.get("data-job-id") or None
    job_id = raw_job_id if raw_job_id and raw_job_id.isdigit() else None
    title = None
    link = card.select_one("a.job-card-list__title--link")
    if link:
        label = link.get("aria-label", "") or ""
        title = re.sub(r"\s+with verification$", "", label, flags=re.IGNORECASE).strip() or None
    company = None
    subtitle = card.select_one(".artdeco-entity-lockup__subtitle")
    if subtitle:
        company = text_or_none(subtitle)
    return {"job_id": job_id, "title": title, "company": company, "workplace_hint": _extract_card_workplace_hint(card)}


def simulate_list_browsing(driver: webdriver.Chrome) -> None:
    """Scroll the results list all the way to the bottom so every one of
    this page's 25 cards renders.

    Restored 2026-08-14 after being deleted earlier the same day on the
    (wrong) assumption that the rebuilt list "isn't virtualized" — that was
    only confirmed true for the componentkey template on a 1000+-result
    search. The classic template (_CARD_LIST_SELECTOR_CLASSIC, also still
    live) turned out to be virtualized after all: live DOM inspection
    showed 25 real <li> elements per page, but only the first 7 hold actual
    card content — the other 18 sit as empty
    "jobs-search-results__job-card-search--generic-occlusion" placeholders
    until scrolled into view. Critically, a single jump-to-bottom
    (`window.scrollTo`/`scrollTop = scrollHeight` in one shot, what the
    componentkey-only version of this function's caller used) does NOT
    hydrate them — confirmed live both via JS scrollTop assignment and
    scrollIntoView, neither changed the hydrated count. Only genuine
    incremental scrolling (many small steps with pauses between, as below)
    hydrates each card in turn, matching a scroll-EVENT-driven virtualized
    window rather than one that renders based on final scroll position.

    Original docstring (2026-07-06), still accurate for why this is
    incremental rather than a single jump: once a card has rendered, its
    content stays populated even after scrolling past it, so a single
    top-to-bottom pass is enough; no need to re-check earlier cards.

    The actual scrollable element is the <ul>'s parent div, not
    div.scaffold-layout__list itself (that one reported scrollHeight ==
    clientHeight — not scrollable — while scrolling it silently did
    nothing). Ember gives the real scrollable div a hashed, unstable class
    name each session, so it's located structurally (ul.parentElement)
    rather than by class. Harmless no-op on the componentkey template,
    where the list is already fully rendered and max_scroll resolves to
    ~0 immediately.

    Movement is randomized (direction/distance/pauses), like
    simulate_reading(), just biased more strongly downward since the goal
    here is full coverage of the page's cards, not idle browsing.

    max_scroll is recomputed every iteration rather than once up front —
    found live 2026-08-14 debugging a run where 2 of 9 pages still stuck at
    7/25 despite this function running (no "container not found" warning
    logged): placeholder <li> heights before hydration are LinkedIn's own
    estimate, so scrollHeight can grow as real cards render in, meaning a
    max_scroll computed only once at the start can go stale mid-loop and
    make the loop exit believing it reached bottom when the true bottom
    had since moved further down.
    """
    container = driver.execute_script(
        "const ul = document.querySelector('div.scaffold-layout__list ul'); "
        "return ul ? ul.parentElement : null;"
    )
    if container is None:
        print("  WARNING: list scroll container not found — card titles may not render.")
        return

    iterations = 0
    while iterations < 40:  # iteration cap is just a safety backstop against an unexpected page state
        max_scroll = driver.execute_script(
            "return arguments[0].scrollHeight - arguments[0].clientHeight;", container
        )
        if max_scroll <= 0:
            break  # fewer cards than fit on one screen — nothing left to scroll
        scrolled = driver.execute_script("return arguments[0].scrollTop;", container)
        if scrolled >= max_scroll:
            break
        direction = 1 if random.random() < 0.85 else -1   # mostly down, occasional up — same anti-detection idea as simulate_reading()
        step = direction * random.randint(250, 400)       # smaller steps than a first pass at this — more overlap between steps gives each card's async render more of a chance to finish before we move past it
        driver.execute_script("arguments[0].scrollTop += arguments[1];", container, step)
        time.sleep(random.uniform(0.9, 1.8))              # longer than a faster pace would use — that pace still occasionally scrolled past a card before its content finished rendering (see retry_missing_titles for the backstop)
        iterations += 1

    driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)  # guarantee the last card rendered even if the loop undershot
    time.sleep(1.5)


def _hydrated_vs_total_cards(driver: webdriver.Chrome) -> tuple[int, int]:
    """(hydrated_card_count, total_li_count) in the results list — lets
    get_job_cards_on_page detect a scroll pass that finished early with
    cards still un-hydrated (see simulate_list_browsing's docstring) and
    retry rather than silently under-capturing the page."""
    result = driver.execute_script(
        "const ul = document.querySelector('div.scaffold-layout__list ul'); "
        "if (!ul) return [0, 0]; "
        "const lis = [...ul.children]; "
        "const hydrated = lis.filter(li => "
        "  li.querySelector('div.job-card-container[data-job-id]') || "
        "  li.querySelector('div[role=\"button\"][componentkey^=\"job-card-component-ref-\"]')"
        ").length; "
        "return [hydrated, lis.length];"
    )
    return tuple(result)


_BROAD_MATCH_BASE_URL = "https://www.linkedin.com/jobs/search-results/"

# LinkedIn's location entity id for "United States" (broad/nationwide, not a
# specific city/region) — confirmed live 2026-08-17 as part of the
# keyword-based Remote fix below, matches "United States" as shown in the
# real UI's location selector.
_BROAD_MATCH_GEO_ID = "103644278"

_MIN_REMOTE_CARDS = 18  # out of 25 — see BroadMatchDegradedError


def _get_results_count_text(driver: webdriver.Chrome) -> str | None:
    """The "N results"/"99+ results" header text on a /jobs/search-results/
    page, or None if it can't be found. Used only by the start=0 health
    check (see BroadMatchDegradedError) — not parsed into a number since
    "99+" is the only value that actually matters here."""
    return driver.execute_script(
        "const els = [...document.querySelectorAll('*')];"
        "const m = els.find(e => e.children.length===0 && /^\\d+\\+?\\s*results?$/i.test(e.textContent.trim()));"
        "return m ? m.textContent.trim() : null;"
    )


def _check_broad_match_health(driver: webdriver.Chrome, cards: list[dict]) -> None:
    """Raises BroadMatchDegradedError if this keyword's first page doesn't
    look like a healthy broad-match search. Call once, at start=0 only —
    see BroadMatchDegradedError's docstring for what's checked and why."""
    if _BROAD_MATCH_BASE_URL not in driver.current_url:
        raise BroadMatchDegradedError(f"url_changed: expected {_BROAD_MATCH_BASE_URL!r} in the URL, got {driver.current_url!r}")

    results_text = _get_results_count_text(driver)
    if not results_text or "99+" not in results_text:
        raise BroadMatchDegradedError(f"too_few_results: results header was {results_text!r}, expected \"99+ results\"")

    remote_count = sum(1 for c in cards if c.get("workplace_hint") == "Remote")
    if remote_count < _MIN_REMOTE_CARDS:
        raise BroadMatchDegradedError(f"not_remote: only {remote_count}/{len(cards)} cards tagged Remote, expected at least {_MIN_REMOTE_CARDS}")


def get_job_cards_on_page(
    driver: webdriver.Chrome,
    keyword: str,
    start: int,
    time_range: str = TIME_RANGE_DAY,
    geo_id: str | None = None,
    work_type: str = WORK_TYPE_REMOTE,
    broad_match: bool = False,
) -> list[dict]:
    """Fetch job_id + title + company for one page of results (up to 25
    cards). The componentkey template renders all 25 immediately without
    scrolling (confirmed via direct inspection, 2026-08-14, on a
    1000+-result search) — but the classic template
    (_CARD_LIST_SELECTOR_CLASSIC) is virtualized and only renders its first
    7 cards without help, so simulate_list_browsing()'s incremental scroll
    is required (see its docstring for how this was found — a same-day
    regression where this file briefly assumed neither template needed
    scrolling, confirmed wrong by live DOM inspection showing 18 of 25
    cards per page going uncaptured). Returning title here (not just the
    id) is what lets the caller drop off-track jobs
    (analysis/title_filter.py) before ever spending a full detail-page
    fetch on them. `sortBy=DD` (newest first) + `f_TPR` (time window) keep
    this page's results scoped to a small, newest-first slice instead of
    LinkedIn's whole relevance-ranked pool — the fix for already-seen jobs
    reappearing on later pages as that pool shifts mid-run.

    broad_match=True switches to /jobs/search-results/ (found 2026-08-14
    investigating why 'llm' only returned 4-5 results/day — LinkedIn's own
    UI, reached via its search-box autocomplete rather than a raw keyword
    submit, uses this endpoint and returns 99+ results for the same query
    by matching related AI/ML/GenAI terms rather than the literal string
    "llm", which /jobs/search/ requires). No sortBy=DD param here — this
    endpoint doesn't accept it the way /jobs/search/ does, so results
    aren't guaranteed newest-first; dedup_page's detail_fetched check still
    prevents rework, it just means "no more new ids" isn't as clean a
    signal of true end-of-results as on the literal-match endpoint.

    Remote is folded into the literal keyword text itself (caller passes
    e.g. "llm remote" as `keyword`) rather than applied as a separate
    LinkedIn filter — a prior version clicked a "Remote" filter chip and
    carried its resulting referralSearchId across pages, but that filter
    turned out to be unreliable even when it visibly "worked" (confirmed
    live 2026-08-17: On-site/Hybrid jobs still leaked through a genuinely-
    active Remote filter, non-deterministically run to run) and required
    a lot of fragile session-state bookkeeping. Just searching "<keyword>
    remote" as literal text is stateless — no click, no token to carry
    forward — and empirically far cleaner (96% Remote-tagged across two
    live-checked pages, vs. runs as low as ~15% clean with the filter-click
    approach). It's not perfect (still occasionally includes an on-site/
    hybrid job whose text happens to satisfy the search some other way),
    which is exactly why parse_job_card's workplace_hint check and
    run_scrape.py's detail-page-level workplace_type check both still
    exist as safety nets — this just makes them catch the exception
    instead of doing most of the filtering work. geoId=_BROAD_MATCH_GEO_ID
    ("United States") keeps this nationwide rather than defaulting to
    whatever location LinkedIn infers for the account.
    """
    if broad_match:
        search_url = f"{_BROAD_MATCH_BASE_URL}?keywords={quote(keyword)}&f_TPR={time_range}&geoId={_BROAD_MATCH_GEO_ID}&start={start}"
        print(f"Navigating to: {search_url}")
        safe_get(driver, search_url)
        check_not_blocked(driver)
    else:
        search_url = (
            f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&f_WT={work_type}"
            f"&sortBy={SORT_BY_MOST_RECENT}&f_TPR={time_range}&start={start}"
        )
        if geo_id:
            search_url += f"&geoId={geo_id}"
        print(f"Navigating to: {search_url}")
        safe_get(driver, search_url)
        check_not_blocked(driver)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, f"{_CARD_LIST_SELECTOR}, {_CARD_LIST_SELECTOR_CLASSIC}")
            )
        )
    except Exception:
        print("  WARNING: job list didn't appear within 15s — page may need manual inspection.")

    simulate_list_browsing(driver)
    for retry in range(2):  # bounded retry for the occasional page that finishes its scroll pass with cards still un-hydrated — see simulate_list_browsing's docstring
        hydrated, total = _hydrated_vs_total_cards(driver)
        if hydrated >= total:
            break
        print(f"  Only {hydrated}/{total} cards hydrated after scroll — retrying ({retry + 1}/2)...")
        simulate_list_browsing(driver)

    soup = BeautifulSoup(driver.page_source, "lxml")
    cards_html = soup.select(_CARD_LIST_SELECTOR)
    if cards_html:
        cards = [parse_job_card(card) for card in cards_html]
    else:
        cards_html = soup.select(_CARD_LIST_SELECTOR_CLASSIC)
        if cards_html:
            cards = [parse_job_card_classic(card) for card in cards_html]
        else:
            print("  Results list container not found.")
            _dump_diagnostics(driver, "empty_job_list")
            return []

    cards = [card for card in cards if card["job_id"]]        # drop any None job_ids, just in case
    cards = retry_missing_titles(driver, cards)

    if broad_match and start == 0:
        _check_broad_match_health(driver, cards)

    return cards


def retry_missing_titles(driver: webdriver.Chrome, cards: list[dict], max_attempts: int = 2) -> list[dict]:
    """A card occasionally still comes back with no title/company even
    though this list isn't virtualized for text (see get_job_cards_on_page)
    — a transient render lag, not the routine case it was on the old list.
    Left alone, that title=None would make is_relevant_title() treat a
    possibly-relevant job as off-track and drop it, and a missing company
    would let an agency posting slip past the company-blocklist check, so
    retry rather than accept the loss: scroll that specific card back into
    view directly (scrollIntoView), then re-read.
    """
    cards_by_id = {card["job_id"]: card for card in cards}
    for attempt in range(max_attempts):
        missing_ids = [c["job_id"] for c in cards if c["title"] is None or c["company"] is None]
        if not missing_ids:
            break
        print(f"  {len(missing_ids)} card(s) missing a title/company — retrying (attempt {attempt + 1}/{max_attempts})...")
        for job_id in missing_ids:
            selector = (
                f'div[role="button"][componentkey="job-card-component-ref-{job_id}"], '
                f'div.job-card-container[data-job-id="{job_id}"]'
            )
            outer_html = driver.execute_script(
                "const c = document.querySelector(arguments[0]); "
                "if (!c) return null; "
                "c.scrollIntoView({block: 'center'}); "
                "return c.outerHTML;",
                selector,
            )
            time.sleep(1.0)
            if outer_html is None:
                continue
            # re-read straight after the wait (not the outer_html captured
            # before it) so the pause has a chance to let the render finish
            outer_html = driver.execute_script(
                "const c = document.querySelector(arguments[0]); return c ? c.outerHTML : null;", selector
            )
            if outer_html:
                card_soup = BeautifulSoup(outer_html, "lxml").select_one(
                    'div[role="button"], div[data-job-id]'
                )
                if card_soup:
                    reparsed = (
                        parse_job_card(card_soup)
                        if card_soup.get("componentkey")
                        else parse_job_card_classic(card_soup)
                    )
                    cards_by_id[job_id]["title"] = reparsed["title"]
                    cards_by_id[job_id]["company"] = reparsed["company"]
                    cards_by_id[job_id]["workplace_hint"] = reparsed["workplace_hint"]

    still_missing = [c["job_id"] for c in cards if c["title"] is None or c["company"] is None]
    if still_missing:
        print(f"  WARNING: {len(still_missing)} card(s) still have no title/company after {max_attempts} retries: {still_missing}")
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


_TERTIARY_NOISE_PATTERN = re.compile(
    r"^promoted by hirer$|^responses managed off linkedin$|"
    r"^no response insights available yet$|^company review time is typically",
    re.IGNORECASE,
)


def parse_top_card_info(soup: BeautifulSoup) -> tuple[str | None, str | None, str | None]:
    """Pulls location, posted-date text, and applicant-count text out of the
    tertiary description container under the job title. Matched by keyword
    content (" ago", "clicked apply"/"applicant"), not fixed span position,
    since not every job shows every span (e.g. "Promoted by hirer" isn't
    always present). The first remaining span is location, since location
    always renders first among these spans.

    Found via DB audit 2026-08-14: hirer-responsiveness badges ("Promoted by
    hirer", "Responses managed off LinkedIn", "No response insights
    available yet", "Company review time is typically...") match neither the
    "ago" nor "applicant" pattern, so they were being misclassified as
    location — and since location was then no longer None, the *real*
    location span right after it was silently dropped. 107 already-scraped
    jobs affected; _TERTIARY_NOISE_PATTERN filters these out before the
    first-remaining-span-is-location fallback runs.
    """
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
        elif _TERTIARY_NOISE_PATTERN.search(text):
            continue
        elif location is None:                     # first remaining span = location
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


WORKPLACE_TYPES = {"Remote", "Hybrid", "On-site"}


def parse_workplace_type(soup: BeautifulSoup) -> str | None:
    """Same job-details-fit-level-preferences button row as parse_salary, but
    picking out the job's own displayed workplace-type badge ("Remote" /
    "Hybrid" / "On-site") instead of the salary button — this is LinkedIn's
    own labeling of the specific job, independent of which f_WT filter the
    search itself used to find it (e.g. a hybrid/on-site search can still
    surface a job LinkedIn tags "Remote")."""
    fit_level = soup.select_one("div.job-details-fit-level-preferences")
    if not fit_level:
        return None
    for btn in fit_level.select("button"):
        text = btn.get_text(strip=True)
        for workplace_type in WORKPLACE_TYPES:                  # substring, not equality — LinkedIn duplicates
            if workplace_type in text:                          # the label in a hidden a11y span within the button,
                return workplace_type                            # so exact-match against the raw get_text() never hits
    return None


def scrape_job_detail(
    driver: webdriver.Chrome,
    keyword: str,
    job_id: str,
    time_range: str = TIME_RANGE_DAY,
    geo_id: str | None = None,
    work_type: str = WORK_TYPE_REMOTE,
    broad_match: bool = False,
) -> dict:
    """Fetch everything for one job — title, company, location, industry,
    company size, full JD text, posted date, applicant stats, salary — from
    the /jobs/search/ two-pane view via a direct currentJobId deep link.
    That page has stable, semantic class names (job-details-jobs-unified-
    top-card__company-name, #job-details, jobs-company, etc.) — unlike the
    standalone /jobs/view/<id>/ page, which renders the same content with
    hashed/unstable CSS-module classes instead (e.g. "_3aef665a fd81105c
    ..." — reconfirmed live 2026-08-17 via direct inspection, zero <h1>
    anywhere on that page).

    broad_match jobs (found via /jobs/search-results/'s related-term
    matching, not literal keyword text — see get_job_cards_on_page) use
    this exact same /jobs/search/ page, just with `keywords` left out of
    the URL entirely: only currentJobId={job_id}&f_TPR={time_range}. Two
    other approaches were tried and ruled out live 2026-08-17:

    1. Deep-linking with `keywords` still attached (the literal-match
       shape below, unchanged) doesn't work for a broad-match-sourced job:
       /jobs/search/ runs a literal-text search against `keywords`
       regardless of currentJobId being present, and a job found by
       related-term matching (e.g. "AI Builder - Remote" at OneDigital,
       surfaced under the query "llm remote" without literally containing
       "llm") isn't part of that literal query's own result set — the
       detail pane simply never renders (confirmed: page body showed "llm
       remote in United States, 658 results" with h1/company-name/
       job-details all null, even though the URL still showed
       currentJobId=<the requested id>). Dropping `keywords` removes the
       literal-match filter entirely, so there's nothing left for the job
       to fail to match against.

    2. An earlier version of this function loaded the broad-match list
       page (/jobs/search-results/) and JS-clicked the matching card, since
       a deep link into that endpoint (with or without `keywords`) never
       exposes the stable classes either — confirmed both before and after
       this change. That click-based approach's own safeguard (poll the
       URL for currentJobId to update before trusting the extraction)
       turned out insufficient: a DB audit found job_id 4452234830 saved
       as "AI Agent Developer" / Hagerty with detail_fetched=True (a
       "successful" fetch by that safeguard's own criteria), while its
       real, live content — confirmed independently on /jobs/view/,
       /jobs/search-results/, and this /jobs/search/ deep link, all three
       agreeing — is "Software Engineer, AI/Agents" / Ladders. The URL
       updating to the right id is not proof the rendered pane's content
       actually caught up with it. A single direct navigation (this
       version, same as the literal-match path always used) has no
       click/route-timing race to get wrong in the first place.

    workplace_type is extracted the same way for both paths now
    (parse_workplace_type, from the fit-level button row) rather than
    passed through from the list card's workplace_hint tag — the earlier
    broad-match version relied on that hint only because its old
    extraction path had no reliable way to read the page itself.
    """
    if broad_match:
        url = f"https://www.linkedin.com/jobs/search/?currentJobId={job_id}&f_TPR={time_range}"
    else:
        url = (
            f"https://www.linkedin.com/jobs/search/?keywords={quote(keyword)}&f_WT={work_type}"
            f"&sortBy={SORT_BY_MOST_RECENT}&f_TPR={time_range}&currentJobId={job_id}"
        )
        if geo_id:
            url += f"&geoId={geo_id}"
    safe_get(driver, url)                                  # load the search page with this specific job pre-selected in the right-hand pane
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
    workplace_type = parse_workplace_type(soup)

    return {
        "job_id": job_id,
        "url": f"https://www.linkedin.com/jobs/view/{job_id}/",  # clean canonical URL, stable across scrapes (no tracking params)
        "title": title,
        "company": company,
        "location": location,
        "workplace_type": workplace_type,
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
