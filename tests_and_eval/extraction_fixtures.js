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
    name: "'Chicago' must never be accepted as a posted_date",
    html: CHICAGO_TRAP,
    expect: { posted_date: null, workplace_type: "Remote", location: "Chicago, IL" },
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
