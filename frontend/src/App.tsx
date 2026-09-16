import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchHealth, fetchJobs, patchJob, type Health, type Job, type JobPatch, type Track } from "./api";
import {
  DEFAULT_FILTERS, applySaved, compareJobs, defaultSortDir, matchesQuery, passesFilters, trackStats,
  type Filters, type Sort, type SortKey,
} from "./jobs";
import DetailPanel from "./components/DetailPanel";
import FilterBar from "./components/FilterBar";
import JobTable from "./components/JobTable";
import Sidebar, { type View } from "./components/Sidebar";

const TRACKS: { id: Track; label: string }[] = [
  { id: "ml_ai", label: "AI / ML" },
  { id: "pm", label: "Product" },
];

const HEALTH_POLL_MS = 5 * 60 * 1000;

export default function App() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [view, setView] = useState<View>("pipeline");
  const [track, setTrack] = useState<Track>("ml_ai");
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<Sort>({ key: "total_score", dir: -1 });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [savingIds, setSavingIds] = useState<ReadonlySet<number>>(new Set());
  const [toast, setToast] = useState<string | null>(null);

  const loadHealth = useCallback(() => {
    // Health must never break the dashboard.
    return fetchHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [loaded] = await Promise.all([fetchJobs(), loadHealth()]);
      setJobs(loaded);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }, [loadHealth]);

  useEffect(() => {
    refresh();
    const timer = setInterval(loadHealth, HEALTH_POLL_MS);
    return () => clearInterval(timer);
  }, [refresh, loadHealth]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timer);
  }, [toast]);

  const trackJobs = useMemo(() => (jobs ?? []).filter((j) => j.track === track), [jobs, track]);
  const stats = useMemo(() => trackStats(trackJobs), [trackJobs]);

  const inView = useCallback(
    (job: Job, now: number) =>
      view === "applied" ? job.applied && matchesQuery(job, filters.query) : passesFilters(job, filters, now),
    [view, filters],
  );

  const visibleJobs = useMemo(() => {
    const now = Date.now();
    return trackJobs.filter((j) => inView(j, now)).sort(compareJobs(sort));
  }, [trackJobs, inView, sort]);
  const trackCounts = useMemo(() => {
    const now = Date.now();
    const counts: Record<Track, number> = { ml_ai: 0, pm: 0 };
    for (const job of jobs ?? []) if (inView(job, now)) counts[job.track] += 1;
    return counts;
  }, [jobs, inView]);
  const pipelineCount = useMemo(() => {
    const now = Date.now();
    return trackJobs.filter((j) => passesFilters(j, filters, now)).length;
  }, [trackJobs, filters]);

  const selected = selectedId === null ? null : (jobs ?? []).find((j) => j.id === selectedId) ?? null;

  const updateJob = useCallback(async (job: Job, updates: JobPatch): Promise<boolean> => {
    setSavingIds((prev) => new Set(prev).add(job.id));
    try {
      const saved = await patchJob(job.id, updates);
      setJobs((prev) => (prev ? applySaved(prev, job, saved) : prev));
      return true;
    } catch (err) {
      console.error("Failed to save:", err);
      setToast("Couldn't save — check that the Flask server is running and try again.");
      return false;
    } finally {
      setSavingIds((prev) => {
        const next = new Set(prev);
        next.delete(job.id);
        return next;
      });
    }
  }, []);

  const handleSort = (key: SortKey) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: defaultSortDir(key) }));
  };

  const handleView = (next: View) => {
    setView(next);
    setSort(next === "applied" ? { key: "applied_at", dir: -1 } : { key: "total_score", dir: -1 });
  };

  const closePanel = useCallback(() => setSelectedId(null), []);

  // Remount the table (resetting its "show more" depth and scroll) whenever the
  // list itself changes shape — but not when a row is merely edited in place.
  const tableKey = [view, track, sort.key, sort.dir, JSON.stringify(filters)].join("|");

  return (
    <div className="shell">
      <Sidebar
        view={view}
        onView={handleView}
        pipelineCount={pipelineCount}
        appliedCount={stats.applied}
        health={health}
        refreshing={refreshing}
        onRefresh={refresh}
      />

      <div className="main">
        <header className="page-head">
          <div className="page-title-row">
            <h1>{view === "applied" ? "Applied" : "Job Pipeline"}</h1>
            <span className="page-sub">
              {TRACKS.find((t) => t.id === track)!.label} · {view === "applied" ? "most recent first" : "ranked by fit"}
            </span>
            <div className="tracks" role="tablist">
              {TRACKS.map((t) => (
                <button
                  key={t.id}
                  role="tab"
                  aria-selected={track === t.id}
                  className={`track${track === t.id ? " active" : ""}`}
                  onClick={() => { setTrack(t.id); setSelectedId(null); }}
                >
                  {t.label}
                  <span className="mono track-count">{trackCounts[t.id].toLocaleString()}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="kpis">
            <div className="kpi">
              <span className="kpi-value mono">{stats.screened.toLocaleString()}</span>
              <span className="kpi-label">Screened</span>
            </div>
            <div
              className="kpi"
              title={stats.unknownWorkplaceApplied
                ? `${stats.unknownWorkplaceApplied} non-remote application(s) have no workplace type recorded — counted as non-remote. Check extraction health if this is climbing.`
                : undefined}
            >
              <span className="kpi-value mono">{stats.applied.toLocaleString()}</span>
              <span className="kpi-label">
                Applied · {stats.remoteApplied} remote · {stats.nonRemoteApplied} non-remote
                {stats.unknownWorkplaceApplied ? ` (${stats.unknownWorkplaceApplied} unknown)` : ""}
              </span>
            </div>
            <div className="kpi">
              <span className="kpi-value mono">{stats.duplicates.toLocaleString()}</span>
              <span className="kpi-label">Duplicates</span>
            </div>
            <div className="kpi">
              <span className="kpi-value mono">{stats.expired.toLocaleString()}</span>
              <span className="kpi-label">Expired</span>
            </div>
            <div className="kpi">
              <span className="kpi-value mono">{stats.notInterested.toLocaleString()}</span>
              <span className="kpi-label">Not interested</span>
            </div>
          </div>
        </header>

        <FilterBar
          filters={filters}
          onChange={(patch) => setFilters((f) => ({ ...f, ...patch }))}
          track={track}
          resultCount={visibleJobs.length}
          searchOnly={view === "applied"}
        />

        <div className="body">
          {jobs === null ? (
            <div className="status-message">
              {loadError ? (
                <>
                  <p>Couldn't load jobs — is the Flask server running on port 5050?</p>
                  <p className="mono cell-muted">{loadError}</p>
                  <button className="btn btn-primary" onClick={refresh} disabled={refreshing}>Retry</button>
                </>
              ) : (
                <p>Loading jobs…</p>
              )}
            </div>
          ) : (
            <JobTable
              key={tableKey}
              jobs={visibleJobs}
              sort={sort}
              onSort={handleSort}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onToggleApplied={(job) => updateJob(job, { applied: !job.applied })}
              savingIds={savingIds}
              dimCrossedOff={view === "pipeline"}
              emptyMessage={view === "applied" ? "No applications on this track yet." : "No jobs match these filters."}
            />
          )}

          {selected && (
            <DetailPanel
              key={selected.id}
              job={selected}
              saving={savingIds.has(selected.id)}
              onClose={closePanel}
              onUpdate={(updates) => updateJob(selected, updates)}
            />
          )}
        </div>
      </div>

      {toast && <div className="toast" role="alert">{toast}</div>}
    </div>
  );
}
