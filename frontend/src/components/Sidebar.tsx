import { useState } from "react";
import type { Health, HealthState } from "../api";

export type View = "pipeline" | "applied";

const HEALTH_LABELS: Record<HealthState, string> = {
  ok: "Pipeline healthy",
  stale: "Pipeline stalled",
  drift: "Extraction drift",
  failed: "Last run failed",
  incomplete: "Run stopped early",
  unknown: "No runs recorded",
};

function activityAge(hours: number | null): string {
  if (hours === null) return "";
  return hours < 1 ? "Last activity under an hour ago." : `Last activity ${Math.round(hours)}h ago.`;
}

type Props = {
  view: View;
  onView: (view: View) => void;
  pipelineCount: number;
  appliedCount: number;
  health: Health | null;
  refreshing: boolean;
  onRefresh: () => void;
};

export default function Sidebar({ view, onView, pipelineCount, appliedCount, health, refreshing, onRefresh }: Props) {
  const [showDetail, setShowDetail] = useState(false);

  return (
    <aside className="sidebar">
      <div className="logo">
        <div className="logo-mark">J</div>
        <span className="logo-name">JoblyGo</span>
        <span className="logo-beta">β</span>
      </div>

      <button className={`nav-item${view === "pipeline" ? " active" : ""}`} onClick={() => onView("pipeline")}>
        <span className="nav-icon">⚡</span>
        <span className="nav-label">Pipeline</span>
        <span className="nav-count mono">{pipelineCount.toLocaleString()}</span>
      </button>
      <button className={`nav-item${view === "applied" ? " active" : ""}`} onClick={() => onView("applied")}>
        <span className="nav-icon">◎</span>
        <span className="nav-label">Applied</span>
        <span className="nav-count mono">{appliedCount.toLocaleString()}</span>
      </button>

      <div className="sidebar-foot">
        <div className="divider" />
        {/* Always visible, never behind a tab: the failure this exists to catch
            (22 consecutive failed scheduled runs, September 2026) is exactly
            the one nobody thinks to go looking for. */}
        <div className={`health ${health?.state ?? "loading"}`}>
          <div className="health-head">
            <span className={`health-dot${health && health.state !== "ok" ? " pulse" : ""}`} />
            <span className="health-title">{health ? HEALTH_LABELS[health.state] : "Checking pipeline…"}</span>
          </div>
          {health && (
            <p className="health-msg">
              {health.message}. {activityAge(health.hours_since_activity)}
            </p>
          )}
          {showDetail && health?.last_run && <pre className="health-detail">{JSON.stringify(health.last_run, null, 2)}</pre>}
          <div className="health-actions">
            <button className="btn btn-ghost" onClick={onRefresh} disabled={refreshing}>
              {refreshing ? "Refreshing…" : "↻ Refresh"}
            </button>
            {health?.last_run && (
              <button className="btn btn-ghost" onClick={() => setShowDetail((s) => !s)}>
                {showDetail ? "Hide run" : "Last run"}
              </button>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}
