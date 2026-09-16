// Offline regression tests for extension/content/extract.js.
//
// Every LinkedIn layout this extractor has ever had to handle is captured
// here as a fixture. Two sources: hand-written cases for layouts we solved
// before this harness existed, and real top-card snapshots recorded by
// db/models.py:ExtractionEvent whenever a live capture failed
// (tests_and_eval/fixtures/*.html — export them with
// `python -m tests_and_eval.export_fixtures`).
//
// The point is that adapting to a LinkedIn rebuild stops being risky. Adding
// a strategy to STRATEGIES is only safe if every previously-solved layout
// still passes, and no amount of care while editing a branchy classifier
// gives you that — the 2026-08 corruption (417 rows) was exactly a plausible
// fix that broke a case nobody could re-test. Run: node tests_and_eval/extraction_fixtures.js
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const EXTRACT_SRC = fs.readFileSync(
  path.join(__dirname, "..", "extension", "content", "extract.js"),
  "utf8"
);

function extract(html, url = "https://www.linkedin.com/jobs/view/4434839824/") {
  const dom = new JSDOM(`<!doctype html><html><body>${html}</body></html>`, {
    url,
    runScripts: "outside-only",  // gives window.eval a real window global, as the extension has
  });
  const { window } = dom;
  window.eval(EXTRACT_SRC);
  return window.extractJobDetail();
}

// --- fixtures -------------------------------------------------------------
// A "top card" here only needs the structural features the extractor keys on:
// a /jobs/view/<id>/ anchor for the title, a /company/<slug>/ anchor, and the
// meta leaves. Hashed class names are deliberately absent — the extractor must
// never depend on them again (see extract.js's header).

const JD = `<div><h2>About the job</h2><div>We are hiring an engineer to build and ship models into production systems at scale across the platform.</div></div>`;

const PRE_SEPT = `
  <div>
    <div>
      <a href="/jobs/view/4434839824/">Machine Learning Engineer</a>
      <a href="/company/conquer-ai/">Conquer AI</a>
      <div><span>United States</span></div>
      <div><span>Remote</span></div>
      <div><span>4 days ago</span></div>
      <div><span>32 applicants</span></div>
    </div>
    ${JD}
  </div>`;

const POST_SEPT = `
  <div>
    <div>
      <a href="/jobs/view/4434839824/">Machine Learning Engineer</a>
      <a href="/company/conquer-ai/">Conquer AI</a>
      <div><span>Conquer AI · United States (Remote) · 4 days ago · 32 applicants</span></div>
    </div>
    ${JD}
  </div>`;

// Sears, live 2026-09-08: the workplace type is in the TITLE, and matching a
// trailing "(REMOTE)" instead of the "·" separator picked the title element
// and stored "AI Engineer II" as the location.
const WORKPLACE_IN_TITLE = `
  <div>
    <div>
      <a href="/jobs/view/4434839824/">AI Engineer II (REMOTE)</a>
      <a href="/company/sears/">Sears</a>
      <div><span>Chicago, IL · 3 days ago · 12 applicants</span></div>
    </div>
    ${JD}
  </div>`;

// The /ago/i corruption: 251 rows stored "Chicago, IL (Remote)" as posted_date
// because the classifier tested /ago/i, which matches ChicAGO.
const CHICAGO_TRAP = `
  <div>
    <div>
      <a href="/jobs/view/4434839824/">AI Engineer</a>
      <a href="/company/acme/">Acme</a>
      <div><span>Chicago, IL (Remote)</span></div>
    </div>
    ${JD}
  </div>`;

// Live 2026-09-08 (MeeBoss, Carbon Mapper, ForgeMission): the detail pane
// renders the title link twice — real top card, then a condensed sticky header
// carrying ONLY "Company · Location (Remote)". Scoping to the later (sticky)
// one made posted_date and applicant_stats unrecoverable, and job_writer then
// stamped those jobs "0 hours ago" — a false posting date, not a null.
const STICKY_HEADER_DUPLICATE = `
  <div>
    <div>
      <a href="/jobs/view/4464637636/">Founding Engineer</a>
      <a href="/company/meeboss/">MeeBoss</a>
      <div><span>MeeBoss · United States (Remote) · 6 days ago · 41 applicants</span></div>
    </div>
    <div class="job-details-jobs-unified-top-card__sticky-header">
      <a href="/jobs/view/4464637636/"><h2>Founding Engineer</h2></a>
      <div><span>MeeBoss · United States (Remote)</span></div>
    </div>
    ${JD}
  </div>`;

// The About-the-company card, in both orderings seen in the wild. The extractor
// used to skip leaf index 0 on the assumption that the company name came first;
// when industry leads, that discarded the industry and returned null while
// company_size still resolved — the 2026-09-08 signature (8.4% -> 32% missing).
const COMPANY_CARD_NAME_FIRST = `
  <div>
    <div>
      <a href="/jobs/view/4464637636/">AI Engineer</a>
      <a href="/company/talenthop/">TalentHop</a>
      <div><span>United States (Remote) &middot; 2 days ago &middot; 8 applicants</span></div>
    </div>
    ${JD}
    <div>
      <h2>About the company</h2>
      <div><span>TalentHop</span></div>
      <div><span>Staffing and Recruiting</span></div>
      <div><span>11-50 employees</span></div>
      <div><span>4,102 followers</span></div>
    </div>
  </div>`;

const COMPANY_CARD_INDUSTRY_FIRST = `
  <div>
    <div>
      <a href="/jobs/view/4464637636/">AI Engineer</a>
      <a href="/company/talenthop/">TalentHop</a>
      <div><span>United States (Remote) &middot; 2 days ago &middot; 8 applicants</span></div>
    </div>
    ${JD}
    <div>
      <h2>About the company</h2>
      <div><span>Staffing and Recruiting</span></div>
      <div><span>11-50 employees</span></div>
      <div><span>4,102 followers</span></div>
    </div>
  </div>`;

// Live 2026-09-09 (blcks AI, MakeMeCure, PPT Consulting): smaller companies
// render the industry as a BARE TEXT NODE with sibling spans, not as its own
// element. A childless-element scan cannot see it — the wrapping div has
// children — so industry came back null while company_size resolved. This is
// the real cause of the 8.4% -> 32% jump, NOT the leaf-index rule that was
// blamed first.
const COMPANY_CARD_TEXT_NODE = `
  <div>
    <div>
      <a href="/jobs/view/4463588242/">AI Engineer (m/w/d)</a>
      <a href="/company/blcksai/">blcks AI</a>
      <div><span>United States (Remote) &middot; 8 hours ago &middot; 42 applicants</span></div>
    </div>
    ${JD}
    <div>
      <h2>About the company</h2>
      <div><a href="/company/blcksai/">blcks AI</a></div>
      <div>111 followers</div>
      <button><span>Follow</span></button>
      <div>
        Software Development
        <span>2-10 employees</span>
        <span>1 on LinkedIn</span>
      </div>
    </div>
  </div>`;

// raw_text used to come from range.toString() with all whitespace collapsed, so
// adjacent blocks glued together ("RequirementsPythonAWS") and 70% of September
// captures had no line breaks at all. Each block (p, li, h2, br) is now its own
// line; inline markup like <strong> stays inside its line.
const JD_BLOCKS = `
  <div>
    <div>
      <a href="/jobs/view/4434839824/">Machine Learning Engineer</a>
      <a href="/company/conquer-ai/">Conquer AI</a>
      <div><span>United States</span></div>
    </div>
    <div>
      <h2>About the job</h2>
      <div>
        <p>We build <strong>LLM</strong> products for   hospitals.</p>
        <p><strong>Requirements</strong></p>
        <ul><li>Python</li><li>AWS</li></ul>
        <p>Remote first.<br>Apply now.</p>
      </div>
    </div>
  </div>`;

const CASES = [
  {
    name: "pre-September: separate pill + leaf nodes",
    html: PRE_SEPT,
    expect: { location: "United States", workplace_type: "Remote", posted_date: "4 days ago" },
    expectStrategy: { workplace_type: "pill-leaf" },
  },
  {
    name: "post-September: collapsed '·' meta line",
    html: POST_SEPT,
    expect: { location: "United States", workplace_type: "Remote", posted_date: "4 days ago" },
    expectStrategy: { workplace_type: "meta-parens" },
  },
  {
    name: "workplace type in the title must not become the location",
    html: WORKPLACE_IN_TITLE,
    expect: { location: "Chicago, IL", posted_date: "3 days ago" },
  },
  {
    name: "sticky-header duplicate must not steal the scope",
    html: STICKY_HEADER_DUPLICATE,
    expect: { posted_date: "6 days ago", applicant_stats: "41 applicants",
              location: "United States", workplace_type: "Remote" },
  },
  {
    name: "company card: name first, industry second",
    html: COMPANY_CARD_NAME_FIRST,
    expect: { industry: "Staffing and Recruiting", company_size: "11-50 employees" },
  },
  {
    name: "company card: industry first, no name leaf",
    html: COMPANY_CARD_INDUSTRY_FIRST,
    expect: { industry: "Staffing and Recruiting", company_size: "11-50 employees" },
  },
  {
    name: "company card: industry as a bare text node beside spans",
    html: COMPANY_CARD_TEXT_NODE,
    expect: { industry: "Software Development", company_size: "2-10 employees" },
  },
  {
    name: "'Chicago' must never be accepted as a posted_date",
    html: CHICAGO_TRAP,
    expect: { posted_date: null, workplace_type: "Remote", location: "Chicago, IL" },
  },
  {
    name: "raw_text keeps one line per block, inline markup stays inline",
    html: JD_BLOCKS,
    expect: {
      raw_text: "About the job\nWe build LLM products for hospitals.\nRequirements\nPython\nAWS\nRemote first.\nApply now.",
    },
  },
];

// Real snapshots recorded from failed live captures. These have no expected
// values (we don't know what was on the page) — they assert only that the
// extractor doesn't throw and, once a strategy is written for them, that the
// previously-failing field now resolves. Filename convention:
// <field>-<yyyymmdd>-<jobid>.html
const fixtureDir = path.join(__dirname, "fixtures");
const snapshots = fs.existsSync(fixtureDir)
  ? fs.readdirSync(fixtureDir).filter((f) => f.endsWith(".html"))
  : [];

let failed = 0;
for (const c of CASES) {
  const got = extract(c.html);
  for (const [field, want] of Object.entries(c.expect)) {
    const actual = got[field];
    if (actual !== want) {
      failed++;
      console.log(`FAIL  ${c.name}\n        ${field}: expected ${JSON.stringify(want)}, got ${JSON.stringify(actual)}`);
    }
  }
  for (const [field, want] of Object.entries(c.expectStrategy || {})) {
    const actual = got.extraction_meta.strategies[field];
    if (actual !== want) {
      failed++;
      console.log(`FAIL  ${c.name}\n        ${field} won via ${JSON.stringify(actual)}, expected ${JSON.stringify(want)}`);
    }
  }
  if (!failed) console.log(`ok    ${c.name}`);
}

for (const f of snapshots) {
  const html = fs.readFileSync(path.join(fixtureDir, f), "utf8");
  const field = f.split("-")[0];
  let got;
  try {
    got = extract(html);
  } catch (e) {
    failed++;
    console.log(`FAIL  snapshot ${f} threw: ${e.message}`);
    continue;
  }
  const resolved = got[field] != null;
  console.log(`${resolved ? "ok   " : "TODO "} snapshot ${f}: ${field}=${JSON.stringify(got[field])}`);
}

console.log(failed ? `\n${failed} failure(s)` : `\nall ${CASES.length} cases passed, ${snapshots.length} snapshot(s) loaded`);
process.exit(failed ? 1 : 0);
