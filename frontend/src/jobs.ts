// Filtering, sorting and display rules for the job list. Ported from the
// original dashboard (backend/templates/index.html) — the filter semantics
// there each encode a decision, so they are carried over rather than redesigned.

import type { Job, SavedJob } from "./api";

export type Filters = {
  query: string;
  postedWithinDays: number;
  workplace: string;
  seniority: string;
  companySize: string;
  softwareOnly: boolean;
  top500Only: boolean;
  hideConsulting: boolean;
  toCOnly: boolean;
  showCrossedOff: boolean;
  hideDuplicates: boolean;
};

export const DEFAULT_FILTERS: Filters = {
  query: "",
  postedWithinDays: 14,
  workplace: "",
  seniority: "",
  companySize: "",
  softwareOnly: false,
  top500Only: false,
  hideConsulting: true,
  toCOnly: false,
  showCrossedOff: false,
  hideDuplicates: false,
};

export const POSTED_WITHIN_OPTIONS: [number, string][] = [
  [14, "Posted: within 2 weeks"],
  [7, "Within 1 week"],
  [3, "Within 3 days"],
  [1, "Within 1 day"],
];

// Buckets by seniority_score per judge/seniority_fit.py's bands.
export const SENIORITY_OPTIONS: [string, string][] = [
  ["", "Seniority: any"],
  ["5", "Entry / New Grad (<2 yrs)"],
  ["4", "Mid-level (2-5 yrs)"],
  ["3", "Senior, lower (5-7 yrs)"],
  ["2", "Senior, upper (7-9 yrs)"],
  ["1", "Staff / Lead (9-12 yrs)"],
  ["0", "Principal+ (12+ yrs) / disqualified"],
];

// Where staffing and body-shop intermediaries cluster. Blunt on purpose — real
// employers (Oracle, Cognizant, Deloitte) carry these tags too — hence a toggle,
// so nothing is lost, only hidden.
const CONSULTING_INDUSTRIES = ["Business Consulting and Services", "IT Services and IT Consulting"];

// LinkedIn's employee-count bands, keyed with commas and the " employees"
// suffix stripped: the DB holds both "1,001-5,000 employees" and
// "1001-5000 employees" for the same band.
const SIZE_BANDS: Record<string, { bucket: "<20" | "20-500" | "500+"; rank: number; label: string }> = {
  "0-1": { bucket: "<20", rank: 0, label: "0–1" },
  "2-10": { bucket: "<20", rank: 1, label: "2–10" },
  "11-50": { bucket: "20-500", rank: 2, label: "11–50" },
  "51-200": { bucket: "20-500", rank: 3, label: "51–200" },
  "201-500": { bucket: "20-500", rank: 4, label: "201–500" },
  "501-1000": { bucket: "500+", rank: 5, label: "501–1,000" },
  "1001-5000": { bucket: "500+", rank: 6, label: "1,001–5,000" },
  "5001-10000": { bucket: "500+", rank: 7, label: "5,001–10,000" },
  "10001+": { bucket: "500+", rank: 8, label: "10,001+" },
};

function sizeBand(size: string | null) {
  if (!size) return undefined;
  return SIZE_BANDS[size.replace(/,/g, "").replace(/\s*employees$/i, "").trim()];
}

export function sizeLabel(size: string | null): string | null {
  return sizeBand(size)?.label ?? size;
}

export function matchesQuery(job: Job, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (job.title ?? "").toLowerCase().includes(q) || (job.company_name ?? "").toLowerCase().includes(q);
}

export function isCrossedOff(job: Job): boolean {
  return job.applied || job.expired || job.not_interested;
}

export function passesFilters(job: Job, f: Filters, now: number): boolean {
  if (!f.showCrossedOff && isCrossedOff(job)) return false;
  if (f.hideDuplicates && job.duplicate_of_job_id) return false;
  if (f.top500Only && !job.top500_tech) return false;
  if (f.softwareOnly && !/software/i.test(job.company_industry ?? "")) return false;
  if (f.hideConsulting && CONSULTING_INDUSTRIES.includes(job.company_industry ?? "")) return false;
  if (f.seniority && job.seniority_score !== Number(f.seniority)) return false;
  if (f.companySize && sizeBand(job.company_size)?.bucket !== f.companySize) return false;
  if (f.workplace === "unknown" ? job.workplace_type : f.workplace && job.workplace_type !== f.workplace) return false;
  // to-C only means anything on the pm track (see the Job model's to_c_product_pm).
  if (job.track === "pm" && f.toCOnly && !job.to_c_product_pm) return false;
  // Jobs older than the window — or with an unknown posted date — never show;
  // the dropdown only narrows within the 2-week cap, it can't widen past it.
  if (!job.posted_at) return false;
  if (new Date(job.posted_at).getTime() < now - f.postedWithinDays * 86_400_000) return false;
  return matchesQuery(job, f.query);
}

// Until this date the pipeline only surfaced remote roles, so every earlier
// application is remote by construction (workplace_type on those rows is
// unreliable). A FIXED date, not a rolling one: a job applied to on-site today
// must not recount itself as remote tomorrow.
const ONSITE_COLLECTION_START = "2026-09-08";

export function isRemoteApplication(job: Job): boolean {
  if (!job.applied_at) return true; // applied before applied_at existed — predates the cutover
  if (job.applied_at.slice(0, 10) < ONSITE_COLLECTION_START) return true;
  return job.workplace_type === "Remote";
}

export function trackStats(trackJobs: Job[]) {
  const applied = trackJobs.filter((j) => j.applied);
  const remoteApplied = applied.filter(isRemoteApplication).length;
  return {
    screened: trackJobs.length,
    applied: applied.length,
    remoteApplied,
    nonRemoteApplied: applied.length - remoteApplied,
    // Counted as non-remote, surfaced separately so a capture that failed to
    // read workplace_type doesn't quietly inflate that number.
    unknownWorkplaceApplied: applied.filter((j) => !isRemoteApplication(j) && !j.workplace_type).length,
    duplicates: trackJobs.filter((j) => j.duplicate_of_job_id).length,
    expired: trackJobs.filter((j) => j.expired).length,
    notInterested: trackJobs.filter((j) => j.not_interested).length,
  };
}

export type SortKey =
  | "total_score" | "skill_score" | "seniority_score" | "expertise_score"
  | "title" | "company_industry" | "company_size" | "workplace_type" | "posted_at" | "applied_at";

export type Sort = { key: SortKey; dir: 1 | -1 };

export function defaultSortDir(key: SortKey): 1 | -1 {
  return key.endsWith("_score") || key.endsWith("_at") ? -1 : 1;
}

function sortValue(job: Job, key: SortKey): number | string {
  if (key === "company_size") return sizeBand(job.company_size)?.rank ?? -1;
  const value = job[key];
  if (value === null || value === undefined) return key.endsWith("_score") ? -1 : "";
  return typeof value === "string" ? value.toLowerCase() : value;
}

export function compareJobs(sort: Sort) {
  return (a: Job, b: Job) => {
    const av = sortValue(a, sort.key);
    const bv = sortValue(b, sort.key);
    if (av < bv) return -sort.dir;
    if (av > bv) return sort.dir;
    return 0;
  };
}

// Merges a saved PATCH into the list. company_applied_count is computed
// server-side across the whole DB, so every other row at the same company is
// adjusted too rather than re-fetching thousands of jobs on each click.
export function applySaved(all: Job[], job: Job, saved: SavedJob): Job[] {
  const companyKey = (job.company_name ?? "").trim().toLowerCase();
  const delta = saved.applied === job.applied ? 0 : saved.applied ? 1 : -1;
  return all.map((j) => {
    const sameCompany = delta !== 0 && companyKey && (j.company_name ?? "").trim().toLowerCase() === companyKey;
    if (j.id !== job.id && !sameCompany) return j;
    const next = j.id === job.id ? { ...j, ...saved } : { ...j };
    if (sameCompany) next.company_applied_count = Math.max(0, j.company_applied_count + delta);
    return next;
  });
}

export function totalColor(total: number | null): string {
  if (total === null) return "var(--text-tertiary)";
  if (total >= 12) return "var(--green)";
  if (total >= 8) return "var(--amber)";
  if (total >= 4) return "var(--orange)";
  return "var(--red)";
}

export function signalColor(value: number | null): string {
  if (value === null) return "var(--text-tertiary)";
  if (value >= 5) return "var(--green)";
  if (value >= 4) return "var(--lime)";
  if (value >= 3) return "var(--amber)";
  return "var(--red)";
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}
