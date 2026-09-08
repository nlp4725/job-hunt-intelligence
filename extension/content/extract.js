// DOM extraction for a LinkedIn job detail page.
//
// LinkedIn rebuilt this page with fully hashed/obfuscated CSS class names
// (e.g. "c7f3f3a7", "_10fbd90b") sometime after the old Selenium scraper's
// selectors (scraper/linkedin_scraper.py, BEM-style classes like
// "job-details-jobs-unified-top-card__company-name") were written — verified
// live (2026-08-13): zero elements match those old selectors, zero <h1>
// tags exist at all. Rebuilt from scratch against two things that survive a
// frontend rebuild much better than class names do:
//   1. Semantic <a href> paths — /jobs/view/<id>/ for the title link,
//      /company/<slug>/ for the company link.
//   2. Real <h2> section headings ("About the job", "About the company") as
//      structural boundaries — content between one heading and the next is
//      read via DOM Range.toString(), so nesting depth doesn't matter.
//
// Every function here returns null on failure instead of throwing — one
// broken selector (LinkedIn DOM drift, again, inevitably) must not kill the
// whole extraction. The aggregated dict shape in extractJobDetail() is a
// hand-maintained contract with backend/app.py's _EXTENSION_DETAIL_FIELDS
// and db/job_writer.py:save_new_job()'s detail[...] accesses — keep in sync.
//
// Guarded like content_script.js's __jhiInjected check and panel.js's
// window.JHIPanel check: LinkedIn's SPA navigation can trigger
// background.js's webNavigation fallback to re-inject this file on top of
// the original declarative injection. Top-level `const`s would otherwise
// throw a redeclaration SyntaxError on the second injection.
if (!window.__jhiExtractLoaded) {
window.__jhiExtractLoaded = true;

function getJobIdFromLocation() {
  try {
    const url = new URL(location.href);
    const fromQuery = url.searchParams.get("currentJobId");
    if (fromQuery) return fromQuery;
    const match = url.pathname.match(/\/jobs\/view\/(\d+)\//);
    return match ? match[1] : null;
  } catch (e) {
    console.warn("[jhi] getJobIdFromLocation failed", e);
    return null;
  }
}

function findHeading(text) {
  const pattern = new RegExp("^" + text + "$", "i");
  return [...document.querySelectorAll("h2")].find((el) => pattern.test(el.textContent.trim())) || null;
}

// True once an ancestor has grown wide enough to contain the left-hand
// results list as well as the open job's detail pane. Two independent
// markers, since only one holds per endpoint: on the literal endpoint
// (/jobs/search/) the list cards are themselves /jobs/view/ anchors, so a
// second such anchor means we've climbed out of the detail pane; on both
// endpoints every list card carries a "Dismiss ... job" button, which the
// detail pane never does.
function containsResultsList(el, startEl) {
  if (el.querySelector('button[aria-label^="Dismiss"]')) return true;
  return [...el.querySelectorAll('a[href*="/jobs/view/"]')].some((a) => a !== startEl);
}

// The tightest ancestor of `startEl` that does NOT yet contain `boundaryText`
// — used to scope the top-card (title/company/location/etc.) to exclude the
// JD body and everything after it, without depending on a fixed nesting
// depth (LinkedIn's wrapper-div depth here has already proven to vary).
//
// The boundary alone is not enough. In the two-pane layout the climb can
// reach a container holding BOTH panes before "about the job" ever matches
// (the JD renders lazily, and the shared parent sits below the boundary
// ancestor), and every leaf in the left-hand list and the search chrome
// then reads as top-card meta. Observed live: a row storing
// location="llm remote in United States" (that's the search box) and a
// title carrying a list card's "with verification" suffix. Stop before any
// such ancestor and keep the last good one.
function findScopeExcluding(startEl, boundaryText, maxLevels = 10) {
  let el = startEl;
  let prev = el;
  for (let i = 0; i < maxLevels && el.parentElement; i++) {
    el = el.parentElement;
    if (new RegExp(boundaryText, "i").test(el.textContent)) return prev;
    if (containsResultsList(el, startEl)) return prev;
    prev = el;
  }
  return prev;
}

// All leaf-element text between `startHeading` and the next <h2> in document
// order, within a generously-sized ancestor container. Climbing a fixed
// number of levels is safe here (unlike findScopeExcluding's job) because
// we bound the far end by the next heading, not by depth.
function collectLeavesUntilNextHeading(startHeading, climbLevels = 4) {
  let container = startHeading;
  for (let i = 0; i < climbLevels && container.parentElement; i++) container = container.parentElement;
  const all = [...container.querySelectorAll("*")];
  const startIdx = all.indexOf(startHeading);
  if (startIdx === -1) return [];
  const leaves = [];
  for (let i = startIdx + 1; i < all.length; i++) {
    const el = all[i];
    if (el.tagName === "H2") break;
    if (el.children.length === 0 && el.textContent.trim()) leaves.push(el.textContent.trim());
  }
  return leaves;
}

function findTopCardScope() {
  const anchors = [...document.querySelectorAll('a[href*="/jobs/view/"]')].filter(
    (el) => el.textContent.trim().length > 0
  );
  if (!anchors.length) return null;
  // On the semantic endpoint (/jobs/search-results/) the left-hand list cards are
  // plain divs, so the first /jobs/view/ anchor in the document IS the open job's
  // top card. On the literal endpoint (/jobs/search/) the list cards are themselves
  // /jobs/view/ anchors, so the first match is list card 0 — which silently stamped
  // card 0's title onto every job scored from that endpoint (confirmed 2026-08-31:
  // Prelim and Nexera both stored as "Senior Embedded Software Engineer, DSP").
  // Prefer the anchor carrying the currently-selected job id; fall back to first.
  // Matching on the id alone is not enough either: the open job's own LIST
  // card carries the same /jobs/view/<id> href as the detail pane, and being
  // rendered first it always won .find(). Verified live 2026-09-08 — three
  // anchors matched currentJobId, the first inside a list card (with the
  // doubled accessibility text visibleText has to strip) and two in the
  // detail pane. Picking the list card is what pinned the top-card scope
  // inside the results list, which is why the September layout change wiped
  // workplace_type/applicant_stats and filled location with title fragments.
  // Prefer the last match that is not inside a results card; a list card is
  // identifiable by its own "Dismiss ... job" button, which the detail pane
  // never has. Falls back to the previous behaviour when the detail pane has
  // not rendered yet (it is still spinning) so nothing regresses.
  const currentJobId = new URLSearchParams(location.search).get("currentJobId");
  const matching = currentJobId
    ? anchors.filter((el) =>
        (el.getAttribute("href") || "").includes(`/jobs/view/${currentJobId}`)
      )
    : [];
  const inResultsCard = (el) => {
    for (let node = el, i = 0; node && i < 8; node = node.parentElement, i++) {
      if (node.querySelector && node.querySelector('button[aria-label^="Dismiss"]')) return true;
    }
    return false;
  };
  const detailMatches = matching.filter((el) => !inResultsCard(el));

  // The detail pane renders the open job's title link TWICE: once in the real
  // top card, and once in a condensed sticky header that appears on scroll
  // (class job-details-jobs-unified-top-card__sticky-header). The sticky one
  // comes LATER in document order, so taking the last match landed the scope
  // on it — and it carries only "Company · Location (Remote)": no date, no
  // applicant count anywhere inside it. Verified against three live captures
  // 2026-09-08 (MeeBoss, Carbon Mapper, ForgeMission): posted_date and
  // applicant_stats null on all three, after which db/job_writer.py stamped
  // them with the "0 hours ago" sentinel — dating jobs as posted today when
  // they were not. No strategy can recover a value that is not in scope.
  //
  // So pick the candidate by what its region actually contains rather than by
  // position. Deliberately content-based, not a class-name test: the hashed
  // class names this file was rebuilt to avoid change every LinkedIn release,
  // whereas "the top card is the one with the posting date in it" stays true.
  // Ties keep the last match, so behaviour is unchanged wherever no candidate
  // has a date (a genuinely date-less posting scopes exactly as before).
  const candidates = detailMatches.length ? detailMatches : [matching[0] || anchors[0]].filter(Boolean);
  let best = null;
  let bestScore = -1;
  for (const el of candidates) {
    const candidate = { titleLink: el, scope: findScopeExcluding(el, "about the job") };
    let text = "";
    try {
      const leaves = topCardMetaLeaves(candidate) || [];
      text = leaves.map((n) => n.textContent).join(" \u00b7 ");
      if (candidate.scope) text += " " + candidate.scope.textContent;
    } catch (e) {
      text = "";
    }
    let score = 0;
    if (RELATIVE_DATE.test(text)) score += 2;          // the decisive signal
    if (/clicked apply|applicant/i.test(text)) score += 1;
    if (score >= bestScore) {
      bestScore = score;
      best = candidate;
    }
  }
  return best;
}

// LinkedIn's newer layout renders link text twice for accessibility — a
// visually-hidden copy plus an aria-hidden="true" copy — so a naive
// .textContent returns the string concatenated to itself with no separator
// ("AI EngineerAI Engineer"). Confirmed live 2026-08-27 on SWAKIO, stored as
// "Machine Learning Researcher (Remote)Machine Learning Researcher (Remote)".
// Prefer the aria-hidden copy when present, then defensively collapse an
// exactly-doubled string in case the markup changes again.
function visibleText(el) {
  if (!el) return "";
  // Only trust the aria-hidden copy if it actually carries text — LinkedIn also
  // marks decorative <svg> icons aria-hidden, and blindly reading the first match
  // would return "" and null out the title.
  const ariaCopy = [...el.querySelectorAll('[aria-hidden="true"]')].find(
    (n) => n.textContent.trim().length > 0
  );
  let text = (ariaCopy ? ariaCopy.textContent : el.textContent).trim();
  const half = text.length / 2;
  if (text.length % 2 === 0 && text.slice(0, half) === text.slice(half)) {
    text = text.slice(0, half);
  }
  return text;
}

function extractTitleAndCompany(topCard) {
  if (!topCard) return { title: null, company: null };
  const title = visibleText(topCard.titleLink) || null;
  // The company link sits outside the top-card scope in some layout variants,
  // which stored company_name as NULL — including the highest-scoring job of
  // the 2026-08-27 pass (14/15). Fall back to the first /company/ link on the
  // page before giving up.
  const findCompanyLink = (root) =>
    [...root.querySelectorAll('a[href*="/company/"]')].find((el) => visibleText(el).length > 0);
  const companyLink = findCompanyLink(topCard.scope) || findCompanyLink(document);
  return { title, company: companyLink ? visibleText(companyLink) : null };
}

function extractRawText() {
  const h2 = findHeading("about the job");
  if (!h2) return null;
  const nextH2 = [...document.querySelectorAll("h2")].find(
    (el) => el !== h2 && h2.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING
  );
  const range = document.createRange();
  range.setStartBefore(h2);
  if (nextH2) range.setEndBefore(nextH2);
  else range.setEndAfter(document.body.lastChild);
  const text = range.toString().replace(/\s+/g, " ").trim();
  return text || null;
}

function extractIndustryAndSize() {
  const h2 = findHeading("about the company");
  if (!h2) return { industry: null, company_size: null };
  const leaves = collectLeavesUntilNextHeading(h2);

  const company_size = leaves.find((t) => /employee/i.test(t)) || null;
  const noise = /employee|follower|on linkedin|^(follow|more|show more|…|•)$/i;
  const industry = leaves.find((t, i) => i > 0 && !noise.test(t) && t.length < 60) || null;

  return { industry, company_size };
}

// Top-card pill/meta elements render as <span>s on some listings (e.g.
// BioRender) and as <a href="/jobs/search-results/...">-wrapped filter
// links on others (e.g. third-party-syndicated postings like National Debt
// Relief's) — verified live against both. Query both tags, but exclude the
// title (/jobs/view/) and company (/company/) links themselves, which are
// also childless leaves and would otherwise get misclassified as location.
function topCardLeaves(topCard) {
  return [...topCard.scope.querySelectorAll("span, a")].filter((el) => {
    if (el.children.length !== 0) return false;
    if (el.tagName === "A") {
      const href = el.getAttribute("href") || "";
      if (href.includes("/jobs/view/") || href.includes("/company/")) return false;
    }
    return true;
  });
}

// LinkedIn's September 2026 layout ("we're gradually retiring classic job
// search") broke findScopeExcluding for the top card. The detail-pane title
// is still a /jobs/view/ anchor, so titleLink still resolves correctly (title
// and company_name stayed clean), but every ancestor of it now also contains
// the left-hand results list, so containsResultsList fires at the first climb
// and the scope collapses onto the title anchor itself. With nothing else in
// scope, location fell back to title fragments ("AI/ML Engineer I with
// verification") and workplace_type/applicant_stats went null on 97%/86% of
// rows captured after 2026-09-01.
//
// Depth-based climbing can't fix this — the two panes genuinely share their
// nearest common ancestor now. So bound the region by document order instead,
// the same technique extractRawText already uses: everything strictly between
// the title link and the "About the job" heading is the detail-pane top card,
// and nothing else. The results list renders earlier in document order, so it
// cannot leak in; the Dismiss-button check is a belt-and-braces guard for the
// case where titleLink fell back to a list card (detail pane still spinning).
// Returns null when either landmark is missing, so callers fall back to the
// old scope-based leaves and the pre-September layout keeps working.
function topCardMetaLeaves(topCard) {
  const about = findHeading("about the job");
  if (!topCard || !topCard.titleLink || !about) return null;
  const all = [...document.querySelectorAll("*")];
  const start = all.indexOf(topCard.titleLink);
  const end = all.indexOf(about);
  if (start === -1 || end === -1 || end <= start) return null;
  const region = all.slice(start + 1, end);
  if (region.some((el) => el.matches && el.matches('button[aria-label^="Dismiss"]'))) return null;
  return region.filter((el) => el.children.length === 0 && el.textContent.trim());
}

// The same layout change also collapsed company / location / workplace-type
// into a single text node — "Revolutional · McLean, VA (Remote)" — where the
// old markup rendered each as its own leaf. Verified live 2026-09-08 across
// four postings (Revolutional, RemoteHunter, Jobright.ai, Sears); every one
// had this exact shape and no standalone "Remote"/"Hybrid"/"On-site" pill
// anywhere in the detail pane. Splitting on the "·" separator turns one blob
// back into the parts the existing content classifiers already understand.
// Note this is U+00B7 MIDDLE DOT, not the U+2022 BULLET that extractTertiary
// treats as an unsplittable company+location blob.
function splitMetaParts(text) {
  return text
    .split("·")
    .map((part) => part.trim())
    .filter(Boolean);
}

const WORKPLACE_IN_PARENS = /\((Remote|Hybrid|On-site)\)\s*$/i;

// The meta line is identified by the "·" separator alone, never by a trailing
// "(Remote)". Plenty of titles carry the workplace type themselves — "AI
// Engineer II (REMOTE)" (Sears, live 2026-09-08) — and matching on the
// parenthesis picked the title element, then stored "AI Engineer II" as the
// location. Requiring the separator can only under-match (falling back to the
// old per-leaf classifier), never mis-attribute.
function topCardMetaLine(topCard) {
  const leaves = topCardMetaLeaves(topCard);
  if (!leaves) return null;
  return leaves.find((el) => el.textContent.replace(/\s+/g, " ").trim().includes("·")) || null;
}

// A posted_date leaf must look like an actual relative time, not merely
// contain the letters "ago". The classifier used to test /ago/i, which
// matches ChicAGO, DrAGOs, DecAGOn, AGOra and DrAGOnfly — every one of the
// 417 rows corrupted between 2026-08-17 and 2026-09-04 tripped on exactly
// that, 251 of them stamped "Chicago, IL (Remote)". Not cosmetic: the field
// then holds a location, backend/templates/index.html drops any row whose
// date won't parse, and those jobs disappeared from the dashboard.
// Deliberately mirrors analysis/posted_date_parser.py's own pattern (minus
// its optional "reposted" prefix, which this only needs to tolerate, not
// capture) so the extension can't store a shape the parser will reject.
const RELATIVE_DATE = /\d+\s+(second|minute|hour|day|week|month|year)s?\s+ago/i;

const WORKPLACE_TYPES = ["Remote", "Hybrid", "On-site"];

// ---------------------------------------------------------------- strategies
//
// Each drifting field is a VALIDATOR plus an ordered list of STRATEGIES. A
// strategy's output is only accepted if it passes the field's validator, and
// the first one that passes wins; the winner's name is recorded so a session
// can see which layout it is actually reading (see runField / lastRunMeta).
//
// Two things this buys, both learned the hard way:
//
//   1. A field can never be populated with another field's value. The
//      2026-08-17..09-04 corruption (417 rows, 251 of them stamped
//      "Chicago, IL (Remote)" into posted_date, which then failed to parse and
//      dropped those jobs off the dashboard entirely) was a classifier
//      accepting whatever text it happened to match. A validator makes that
//      class of bug structurally impossible rather than merely fixed.
//
//   2. Adapting to a LinkedIn rebuild is adding one entry to a list, not
//      surgery on a branchy function — and layouts already solved keep
//      working, because their strategy stays in the list. When LinkedIn
//      flips markup again, a strategy already present simply starts winning
//      with no code change at all.
//
// Order matters: most specific / most trustworthy first, inference last.

const FIELD_VALIDATORS = {
  // Must be a shape analysis/posted_date_parser.py will actually accept —
  // anything else silently hides the job on the dashboard.
  posted_date: (v) => RELATIVE_DATE.test(v),
  workplace_type: (v) => WORKPLACE_TYPES.includes(v),
  applicant_stats: (v) => /clicked apply|applicant/i.test(v),
  // A location is short, isn't a date, and isn't a company+location blob (the
  // "•" case, seen on syndicated postings — "Centraprise • Cary, NC (On-site)").
  location: (v) => v.length < 80 && !v.includes("\u2022") && !RELATIVE_DATE.test(v),
  title: (v) => v.length > 1 && v.length < 200,
  company: (v) => v.length > 0 && v.length < 120,
  raw_text: (v) => v.length > 50,
};

// Records which strategy won each field for the current extractJobDetail()
// call. Read by content_script.js and shipped to the backend, which is what
// turns a silent break into a visible one.
let lastRunMeta = { strategies: {}, failed: [] };

function runField(field, strategies) {
  const validate = FIELD_VALIDATORS[field] || ((v) => true);
  for (const { name, fn } of strategies) {
    let value = null;
    try {
      value = fn();
    } catch (e) {
      // A strategy written against markup that no longer exists must not kill
      // the capture — that's the whole reason the next strategy exists.
      console.warn(`[jhi] strategy ${field}/${name} threw`, e);
      continue;
    }
    if (typeof value === "string") value = value.replace(/\s+/g, " ").trim();
    if (value && validate(value)) {
      lastRunMeta.strategies[field] = name;
      return value;
    }
  }
  lastRunMeta.strategies[field] = null;
  lastRunMeta.failed.push(field);
  return null;
}

// Shared by every meta-line strategy below: the "·"-joined blob LinkedIn
// collapsed the top card into in September 2026.
function metaParts(topCard) {
  const el = topCardMetaLine(topCard);
  if (!el) return [];
  return splitMetaParts(el.textContent.replace(/\s+/g, " ").trim());
}

// The non-date, non-applicant, non-pill parts of the meta line, in order.
// "Company · Location (Remote)" puts location last; "Location · 3 days ago ·
// 12 applicants" puts it first, but the other parts are excluded here, so the
// last survivor is right in both shapes. An explicit "(Remote)" suffix is
// decisive whenever present.
function metaLocationCandidates(topCard) {
  return metaParts(topCard).filter(
    (t) =>
      !RELATIVE_DATE.test(t) &&
      !/clicked apply|applicant/i.test(t) &&
      !/^(remote|hybrid|on-site|full-time|part-time|contract)$/i.test(t)
  );
}

function topCardTexts(topCard) {
  if (!topCard) return [];
  return topCardLeaves(topCard)
    .map((el) => el.textContent.trim())
    .filter((t) => t && t !== "\u00b7" && !["Apply", "Save", "Follow"].includes(t));
}

const STRATEGIES = {
  posted_date: (topCard) => [
    { name: "meta-line", fn: () => metaParts(topCard).find((t) => RELATIVE_DATE.test(t)) },
    { name: "leaf-scan", fn: () => topCardTexts(topCard).find((t) => RELATIVE_DATE.test(t)) },
  ],
  applicant_stats: (topCard) => [
    { name: "meta-line", fn: () => metaParts(topCard).find((t) => /clicked apply|applicant/i.test(t)) },
    { name: "leaf-scan", fn: () => topCardTexts(topCard).find((t) => /clicked apply|applicant/i.test(t)) },
  ],
  location: (topCard) => [
    {
      name: "meta-line",
      fn: () => {
        const c = metaLocationCandidates(topCard);
        const loc = c.find((t) => WORKPLACE_IN_PARENS.test(t)) || c[c.length - 1];
        return loc ? loc.replace(WORKPLACE_IN_PARENS, "").trim() : null;
      },
    },
    {
      name: "leaf-scan",
      fn: () => {
        const t = topCardTexts(topCard).find(
          (x) =>
            !RELATIVE_DATE.test(x) &&
            !/clicked apply|applicant/i.test(x) &&
            !/^(remote|hybrid|on-site|full-time|part-time|contract)$/i.test(x) &&
            // A leaf carrying "\u2022" is a company+location blob this can't
            // safely split ("Centraprise \u2022 Cary, NC (On-site)"); storing it
            // whole would silently put the company name in the location field.
            !x.includes("\u2022")
        );
        // Same trailing-"(Remote)" strip the meta-line path does, so the two
        // strategies can't disagree about what a location string looks like.
        return t ? t.replace(WORKPLACE_IN_PARENS, "").trim() : null;
      },
    },
  ],
  workplace_type: (topCard) => [
    // Pre-September markup: a standalone pill leaf reading exactly "Remote".
    { name: "pill-leaf", fn: () => topCardTexts(topCard).find((t) => WORKPLACE_TYPES.includes(t)) },
    // Post-September markup: no pill exists anywhere in the detail pane; the
    // type only survives as a parenthesised suffix on the meta line. Case is
    // normalised so the stored value stays one of WORKPLACE_TYPES and the
    // dashboard's existing filters keep matching.
    {
      name: "meta-parens",
      fn: () => {
        for (const part of metaParts(topCard)) {
          const m = part.match(WORKPLACE_IN_PARENS);
          if (m) return WORKPLACE_TYPES.find((t) => t.toLowerCase() === m[1].toLowerCase()) || null;
        }
        return null;
      },
    },
    // No "\u00b7" meta line, but some leaf still carries the parenthesised
    // suffix ("Chicago, IL (Remote)"). Safe to read off the title element too:
    // unlike location, a workplace type found in the title IS the job's real
    // workplace type ("AI Engineer II (REMOTE)", Sears) — and the validator
    // guarantees only Remote/Hybrid/On-site can ever land here.
    {
      name: "leaf-parens",
      fn: () => {
        for (const t of topCardTexts(topCard)) {
          const m = t.match(WORKPLACE_IN_PARENS);
          if (m) return WORKPLACE_TYPES.find((w) => w.toLowerCase() === m[1].toLowerCase()) || null;
        }
        return null;
      },
    },
    // Last resort, and the reason a LinkedIn rebuild costs precision rather
    // than data: the title often carries it when the top card no longer does.
    // Deliberately NOT a raw_text scan — that inference is precision-tuned in
    // analysis/workplace_from_raw_text.py and runs server-side in
    // db/job_writer.py, where it can be evaluated against LinkedIn's own tags.
    {
      name: "title-parens",
      fn: () => {
        const t = document.title || "";
        const m = t.match(/\((Remote|Hybrid|On-site)\)/i);
        return m ? WORKPLACE_TYPES.find((w) => w.toLowerCase() === m[1].toLowerCase()) || null : null;
      },
    },
  ],
};

function extractTertiary(topCard) {
  return {
    location: runField("location", STRATEGIES.location(topCard)),
    posted_date: runField("posted_date", STRATEGIES.posted_date(topCard)),
    applicant_stats: runField("applicant_stats", STRATEGIES.applicant_stats(topCard)),
  };
}

function extractWorkplaceType(topCard) {
  return runField("workplace_type", STRATEGIES.workplace_type(topCard));
}

// The top card's markup, trimmed and stripped of attributes that are pure
// noise for diagnosis (hashed class names change every rebuild; inline styles
// and svg paths are bulk). Sent only when a field actually failed — see
// db/models.py:ExtractionEvent for why keeping it matters.
function topCardSnapshot(topCard) {
  if (!topCard || !topCard.scope) return null;
  try {
    // Capture a GENEROUS region, not just the chosen scope. Learned 2026-09-08:
    // when the failure IS a mis-scope (the sticky-header duplicate stealing the
    // top card), a snapshot of the chosen scope contains everything except the
    // markup you need to diagnose it — the three captures saved that day could
    // never be turned into a passing fixture, because the real top card was
    // outside the region we kept. Climb a few levels so a wrong scope is still
    // visible in its surroundings.
    let region = topCard.scope;
    for (let i = 0; i < 3 && region.parentElement; i++) {
      region = region.parentElement;
      if (/about the job/i.test(region.textContent)) break;
    }
    const clone = region.cloneNode(true);
    clone.querySelectorAll("svg, style, script, noscript").forEach((el) => el.remove());
    clone.querySelectorAll("*").forEach((el) => {
      for (const attr of [...el.attributes]) {
        if (!["href", "aria-label", "role", "id"].includes(attr.name)) el.removeAttribute(attr.name);
      }
    });
    return clone.outerHTML.slice(0, 20000);
  } catch (e) {
    return null;
  }
}

// Aggregates every field into the shape save_new_job() / the extension
// backend endpoint expects. Warns (by field name) on anything null, which
// is the signal the manual verification phase reads directly. salary_text
// is deliberately left null for now — not required, and the naive
// "$...-$..." pattern false-matched LinkedIn's own "Retry Premium for $0"
// upsell text in testing.
function extractJobDetail() {
  lastRunMeta = { strategies: {}, failed: [] };
  const job_id = getJobIdFromLocation();
  const topCard = findTopCardScope();
  const { title, company } = extractTitleAndCompany(topCard);
  const raw_text = extractRawText();
  const { industry, company_size } = extractIndustryAndSize();
  const { location, posted_date, applicant_stats } = extractTertiary(topCard);
  const workplace_type = extractWorkplaceType(topCard);

  // These three don't (yet) go through runField — they have their own
  // multi-signal logic above — but their health still has to show up in the
  // telemetry, or a break in the most important fields of all is the one
  // thing the report can't see.
  for (const [field, value] of [["title", title], ["company", company], ["raw_text", raw_text]]) {
    const ok = value && (FIELD_VALIDATORS[field] || (() => true))(value);
    lastRunMeta.strategies[field] = ok ? "builtin" : null;
    if (!ok) lastRunMeta.failed.push(field);
  }

  const detail = {
    job_id,
    url: job_id ? `https://www.linkedin.com/jobs/view/${job_id}/` : null,
    title,
    company,
    location,
    workplace_type,
    raw_text,
    industry,
    company_size,
    posted_date,
    applicant_stats,
    salary_text: null,
  };

  for (const [field, value] of Object.entries(detail)) {
    if (value === null && field !== "salary_text") console.warn(`[jhi] extraction: ${field} is null`);
  }

  // Shipped to backend/app.py, which persists it as an ExtractionEvent. The
  // snapshot rides along only on a failure — that is the one moment the
  // markup that defeated us still exists, and without it a later repair means
  // re-visiting LinkedIn and hoping to hit the same layout again.
  detail.extraction_meta = {
    strategies: lastRunMeta.strategies,
    failed_fields: lastRunMeta.failed,
    snapshot_html: lastRunMeta.failed.length ? topCardSnapshot(topCard) : null,
  };

  return detail;
}

window.getJobIdFromLocation = getJobIdFromLocation;
window.extractJobDetail = extractJobDetail;

} // end __jhiExtractLoaded guard
