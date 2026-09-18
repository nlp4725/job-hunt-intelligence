import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getPublicStats, getRecentJobs, type PublicJob, type PublicStats } from "../api";

/** Recent postings, open to anyone: the real board without scores. A score is
 *  per person, so it appears only once you sign in and add a resume.
 *  Collection runs daily in the afternoon, and some days it doesn't run at
 *  all, so the window is "recent", never "today". */
const WORKPLACE: Record<string, string> = { Remote: "#7c3aed", Hybrid: "#2563eb", "On-site": "#059669" };

/** LinkedIn's own "posted" text is relative ("2 weeks ago"), which goes stale
 *  the moment we store it. Show the date we collected the posting instead. */
function addedOn(iso: string | null): string {
  if (!iso) return "—";
  const when = new Date(iso + (iso.endsWith("Z") ? "" : "Z"));
  return when.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
const WINDOWS = [
  { days: 3, label: "Last 3 days" },
  { days: 7, label: "Last week" },
  { days: 14, label: "Last 2 weeks" },
  { days: 30, label: "Last 30 days" },
];

function collectedLabel(stats: PublicStats | null): string {
  if (!stats?.last_collected_at) return "New postings usually arrive in the afternoon.";
  const iso = stats.last_collected_at;
  const hours = (Date.now() - new Date(iso + (iso.endsWith("Z") ? "" : "Z")).getTime()) / 3_600_000;
  const ago = hours < 1 ? "under an hour ago" : hours < 48 ? `${Math.round(hours)} hours ago` : `${Math.round(hours / 24)} days ago`;
  return `Last collected ${ago} · new postings usually arrive around 3 PM.`;
}

export function PublicBoard() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<PublicJob[] | null>(null);
  const [stats, setStats] = useState<PublicStats | null>(null);
  const [days, setDays] = useState(14);
  const [search, setSearch] = useState("");
  const [workplace, setWorkplace] = useState("any");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPublicStats().then(setStats).catch(() => setStats(null));
  }, []);

  useEffect(() => {
    setJobs(null);
    getRecentJobs(days, 200)
      .then(setJobs)
      .catch((err: Error) => setError(err.message));
  }, [days]);

  const visible = (jobs ?? []).filter((job) => {
    if (workplace !== "any" && job.workplace_type !== workplace) return false;
    if (search && !`${job.title} ${job.company ?? ""}`.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="hero-page">
      <nav className="hero-nav">
        <div className="hero-nav-inner">
          <Link className="hero-brand" to="/">
            <span className="hero-mark">J</span>
            <span className="hero-name">JoblyGo</span>
          </Link>
          <div className="hero-nav-actions">
            <button className="hero-link" onClick={() => navigate("/signin")}>
              Sign in
            </button>
            <button className="hero-cta" onClick={() => navigate("/signup")}>
              Upload your resume
            </button>
          </div>
        </div>
      </nav>

      <div className="public-board">
        <div className="public-head">
          <h1>Recent postings</h1>
          <p className="page-sub">
            Every posting here was collected and screened by hand: agencies and reposts removed, seniority classified.
            The first three columns are scored against your own resume — upload one to unlock them.
          </p>
          <p className="freshness">{collectedLabel(stats)}</p>
          <div className="filter-bar plain">
            <input
              type="text"
              className="search"
              placeholder="🔍  Filter by title or company…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {WINDOWS.map((window) => (
                <option key={window.days} value={window.days}>
                  {window.label}
                </option>
              ))}
            </select>
            <select value={workplace} onChange={(event) => setWorkplace(event.target.value)}>
              <option value="any">Workplace: any</option>
              <option>Remote</option>
              <option>Hybrid</option>
              <option>On-site</option>
            </select>
            <span className="result-count mono">{visible.length} jobs</span>
          </div>
        </div>

        {error && <div className="banner error">{error}</div>}

        <div className="demo">
          <div className="demo-scroll">
            <table className="demo-table public">
              <thead>
                <tr>
                  {["#", "Score", "Senr.", "Exp.", "Job", "Industry", "Size", "Workplace", "Location", "Added"].map((label) => (
                    <th key={label}>{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((job, index) => (
                  <tr key={`${job.title}-${job.company}-${index}`}>
                    <td className="mono demo-index">{index + 1}</td>
                    {["Total score", "Seniority fit", "Expertise"].map((what) => (
                      <td key={what}>
                        <button className="score-locked" onClick={() => navigate("/signup")} title={`${what} is computed against your resume — sign up to see it`}>
                          🔒
                        </button>
                      </td>
                    ))}
                    <td className="demo-job">
                      <a className="demo-title link" href={job.url} target="_blank" rel="noreferrer">
                        {job.title} <span className="job-link">↗</span>
                      </a>
                      <div className="demo-company">{job.company}</div>
                    </td>
                    <td className="demo-industry">{job.industry ?? "—"}</td>
                    <td className="mono demo-muted">{job.size ?? "—"}</td>
                    <td>
                      {job.workplace_type ? (
                        <span className="demo-pill" style={{ "--c": WORKPLACE[job.workplace_type] ?? "#7a6248" } as React.CSSProperties}>
                          {job.workplace_type}
                        </span>
                      ) : (
                        <span className="demo-pill neutral">Unknown</span>
                      )}
                    </td>
                    <td className="demo-industry">{job.location ?? "—"}</td>
                    <td className="mono demo-muted">{addedOn(job.first_seen_at)}</td>
                  </tr>
                ))}
                {jobs !== null && visible.length === 0 && (
                  <tr>
                    <td className="empty" colSpan={10}>
                      No postings match. Try a longer window.
                    </td>
                  </tr>
                )}
                {jobs === null && (
                  <tr>
                    <td className="empty" colSpan={10}>
                      <span className="pulse">Loading the board…</span>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="public-foot">
          <button className="hero-cta large" onClick={() => navigate("/signup")}>
            Upload your resume to score these →
          </button>
        </div>
      </div>
    </div>
  );
}
