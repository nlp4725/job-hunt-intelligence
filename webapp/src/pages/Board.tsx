import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { DetailPanel } from "../components/DetailPanel";
import { ExtensionPanel } from "../components/ExtensionPanel";
import { JobTable, type SortKey } from "../components/JobTable";
import { listJobs, saveTracking, type BoardJob, type Me } from "../api";
import { signOut } from "../auth";

type View = "pipeline" | "applied" | "extension";

const SORT_VALUE: Record<SortKey, (job: BoardJob) => number> = {
  total: (job) => job.scores?.total_score ?? -1,
  skill: (job) => job.scores?.skill_score ?? -1,
  seniority: (job) => job.scores?.seniority_fit ?? -1,
  expertise: (job) => job.expertise?.expertise_score ?? -1,
};

/** The board, ported from the Figma Make dashboard and wired to the API:
 *  sidebar, KPI strip, filters, table and detail panel. */
export function Board({ me }: { me: Me }) {
  const [jobs, setJobs] = useState<BoardJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("pipeline");
  const [search, setSearch] = useState("");
  const [workplace, setWorkplace] = useState("any");
  const [days, setDays] = useState<number | null>(14);
  const [hideDismissed, setHideDismissed] = useState(true);
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "total", dir: "desc" });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const paid = me.plan === "paid";

  useEffect(() => {
    setJobs(null);
    listJobs(days)
      .then((page) => setJobs(page.jobs))
      .catch((err: Error) => setError(err.message));
  }, [days]);

  async function track(job: BoardJob, changes: { applied?: boolean; not_interested?: boolean; note?: string }) {
    const previous = jobs;
    setJobs((current) =>
      (current ?? []).map((row) => (row.id === job.id ? { ...row, tracking: { ...(row.tracking ?? emptyTracking()), ...changes } } : row)),
    );
    try {
      const tracking = await saveTracking(job.id, changes);
      setJobs((current) => (current ?? []).map((row) => (row.id === job.id ? { ...row, tracking } : row)));
    } catch (err) {
      setJobs(previous);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const visible = useMemo(() => {
    const rows = (jobs ?? []).filter((job) => {
      if (view === "applied" && !job.tracking?.applied) return false;
      if (view === "pipeline" && hideDismissed && job.tracking?.not_interested) return false;
      if (workplace !== "any" && job.workplace_type !== workplace) return false;
      if (search) {
        const text = `${job.title} ${job.company ?? ""}`.toLowerCase();
        if (!text.includes(search.toLowerCase())) return false;
      }
      return true;
    });
    const direction = sort.dir === "desc" ? -1 : 1;
    return rows.sort((a, b) => direction * (SORT_VALUE[sort.key](a) - SORT_VALUE[sort.key](b)));
  }, [jobs, view, hideDismissed, workplace, search, sort]);

  const appliedCount = (jobs ?? []).filter((job) => job.tracking?.applied).length;
  const dismissed = (jobs ?? []).filter((job) => job.tracking?.not_interested).length;
  const scored = (jobs ?? []).filter((job) => job.scores?.total_score !== null && job.scores !== null).length;
  const selected = visible.find((job) => job.id === selectedId) ?? null;

  const kpis = [
    { value: (jobs?.length ?? 0).toLocaleString(), label: "On your board" },
    { value: scored.toLocaleString(), label: "Scored for you" },
    { value: appliedCount.toString(), label: "Applied" },
    { value: dismissed.toString(), label: "Dismissed" },
  ];

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link className="logo" to="/" title="Home">
          <div className="logo-mark">J</div>
          <span className="logo-name">JoblyGo</span>
          <span className="logo-beta">β</span>
        </Link>
        <button className={`nav-item${view === "pipeline" ? " active" : ""}`} onClick={() => setView("pipeline")}>
          <span className="nav-icon">⚡</span>
          <span className="nav-label">Pipeline</span>
          <span className="nav-count mono">{(jobs?.length ?? 0).toLocaleString()}</span>
        </button>
        <button className={`nav-item${view === "applied" ? " active" : ""}`} onClick={() => setView("applied")}>
          <span className="nav-icon">◎</span>
          <span className="nav-label">Applied</span>
          <span className="nav-count mono">{appliedCount}</span>
        </button>
        {me.role === "admin" && (
          <button className={`nav-item${view === "extension" ? " active" : ""}`} onClick={() => setView("extension")}>
            <span className="nav-icon">⇄</span>
            <span className="nav-label">Extension</span>
          </button>
        )}

        <div className="sidebar-foot">
          <div className="divider" />
          {!me.onboarding.board_scored && (
            <div className="health drift">
              <div className="health-head">
                <span className="health-dot pulse" />
                <span className="health-title">Scoring your board</span>
              </div>
              <p className="health-msg">Your scores are being computed; refresh in a moment.</p>
            </div>
          )}
          <div className="divider" />
          <div className="account">
            <div className="avatar">{me.email.slice(0, 2).toUpperCase()}</div>
            <div>
              <div className="account-name">{me.email}</div>
              <div className="account-plan">{paid ? "Paid plan" : "Free plan"}</div>
            </div>
          </div>
          <button className="btn btn-ghost btn-block" onClick={signOut}>
            Sign out
          </button>
        </div>
      </aside>

      {view === "extension" ? (
        <div className="main">
          <ExtensionPanel />
        </div>
      ) : (
      <div className="main">
        <div className="page-head">
          <div className="page-title-row">
            <h1>{view === "pipeline" ? "Job Pipeline" : "Applications"}</h1>
            <span className="page-sub">ranked by your fit · {days ? `last ${days} days` : "all time"}</span>
          </div>
          <div className="kpis">
            {kpis.map((kpi) => (
              <div className="kpi" key={kpi.label}>
                <span className="kpi-value mono">{kpi.value}</span>
                <span className="kpi-label">{kpi.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="filter-bar">
          <input
            type="text"
            className="search"
            placeholder="🔍  Filter by title or company…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select value={days ?? "all"} onChange={(event) => setDays(event.target.value === "all" ? null : Number(event.target.value))}>
            <option value="14">Collected: last 2 weeks</option>
            <option value="7">Last week</option>
            <option value="30">Last 30 days</option>
            <option value="all">All time</option>
          </select>
          <select value={workplace} onChange={(event) => setWorkplace(event.target.value)}>
            <option value="any">Workplace: any</option>
            <option>Remote</option>
            <option>Hybrid</option>
            <option>On-site</option>
          </select>
          <label className="check">
            <input type="checkbox" checked={hideDismissed} onChange={() => setHideDismissed((value) => !value)} />
            Hide dismissed
          </label>
          <span className="result-count mono">{visible.length} results</span>
        </div>

        {error && <div className="banner error" style={{ margin: "10px 20px 0" }}>{error}</div>}

        <div className="body">
          {jobs === null ? (
            <div className="status-message">
              <span className="pulse">Loading your board…</span>
            </div>
          ) : (
            <div className="table-wrap">
              <JobTable
                jobs={visible}
                paid={paid}
                selectedId={selectedId}
                sort={sort}
                onSort={(key) =>
                  setSort((current) => (current.key === key ? { key, dir: current.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" }))
                }
                onSelect={(job) => setSelectedId((current) => (current === job.id ? null : job.id))}
                onApplied={(job) => track(job, { applied: !job.tracking?.applied })}
              />
            </div>
          )}
          {selected && (
            <DetailPanel job={selected} paid={paid} onClose={() => setSelectedId(null)} onTracking={(changes) => track(selected, changes)} />
          )}
        </div>
      </div>
      )}
    </div>
  );
}

const emptyTracking = () => ({
  applied: false,
  applied_at: null,
  applied_resume_version: null,
  not_interested: false,
  not_interested_note: null,
  note: null,
});
