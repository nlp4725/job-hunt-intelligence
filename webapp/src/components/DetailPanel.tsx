import { useEffect, useState } from "react";

import { totalColor } from "./JobTable";
import type { BoardJob } from "../api";

/** The board's detail panel, ported from the Figma Make dashboard: score with
 *  bars per signal, details, a saved note, and the actions. */
export function DetailPanel({
  job,
  paid,
  onClose,
  onTracking,
}: {
  job: BoardJob;
  paid: boolean;
  onClose: () => void;
  onTracking: (changes: { applied?: boolean; not_interested?: boolean; note?: string }) => void;
}) {
  const [note, setNote] = useState(job.tracking?.note ?? "");
  useEffect(() => setNote(job.tracking?.note ?? ""), [job.id, job.tracking?.note]);

  const scores = job.scores;
  const total = scores?.total_score ?? null;
  const bars = [
    { label: "Skill Match", value: scores?.skill_score ?? null, max: 5, color: "#6366f1" },
    { label: "Seniority Fit", value: scores?.seniority_fit ?? null, max: 5, color: "#16a34a" },
    ...(paid ? [{ label: "Expertise", value: job.expertise?.expertise_score ?? null, max: 5, color: "#d97706" }] : []),
  ];

  return (
    <aside className="panel fadein">
      <div className="panel-head">
        <div className="panel-head-text">
          <div className="panel-title">{job.title}</div>
          <div className="panel-company">{job.company}</div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <div className="panel-body">
        <div>
          <p className="section-label">Your score</p>
          <div className="big-score" style={{ "--c": total === null ? "var(--text-tertiary)" : totalColor(total) } as React.CSSProperties}>
            <span className="mono">{total ?? "—"}</span>
            <span className="big-score-of">out of {paid ? 15 : 10}</span>
          </div>
          {bars.map((bar) => (
            <div className="bar-row" key={bar.label} style={{ "--c": bar.color } as React.CSSProperties}>
              <div className="bar-label">
                <span>{bar.label}</span>
                <span className="mono bar-value">{bar.value === null ? "—" : `${Math.round(bar.value)}/${bar.max}`}</span>
              </div>
              <div className="bar">
                <div style={{ width: `${((bar.value ?? 0) / bar.max) * 100}%` }} />
              </div>
            </div>
          ))}
          {job.expertise?.stale && <p className="callout">Expertise is from an older profile; it will be rescored shortly.</p>}
        </div>

        {scores && (scores.skill_matched?.length || scores.skill_missing?.length) && (
          <div>
            <p className="section-label">Skills</p>
            <div className="chips compact">
              {(scores.skill_matched ?? []).map((skill) => (
                <span className="pill" key={skill} style={{ "--c": "#16a34a" } as React.CSSProperties}>
                  {skill}
                </span>
              ))}
              {(scores.skill_missing ?? []).slice(0, 8).map((skill) => (
                <span className="pill neutral" key={skill}>
                  {skill}
                </span>
              ))}
            </div>
          </div>
        )}

        <div>
          <p className="section-label">Details</p>
          {[
            ["Level", job.level ?? "unknown"],
            ["Location", job.location ?? "—"],
            ["Workplace", job.workplace_type ?? "—"],
            ["Added", job.first_seen_at ? new Date(job.first_seen_at + (job.first_seen_at.endsWith("Z") ? "" : "Z")).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—"],
            ["Contract", job.is_contract ? "Yes" : "No"],
          ].map(([key, value]) => (
            <div className="detail-row" key={key}>
              <span>{key}</span>
              <span>{value}</span>
            </div>
          ))}
        </div>

        <div>
          <p className="section-label">Notes</p>
          <textarea
            value={note}
            placeholder="Add a note…"
            onChange={(event) => setNote(event.target.value)}
            onBlur={() => note !== (job.tracking?.note ?? "") && onTracking({ note })}
          />
        </div>

        <div className="actions">
          <button className="btn btn-primary" onClick={() => onTracking({ applied: !job.tracking?.applied })}>
            {job.tracking?.applied ? "✓ Applied" : "Mark as applied"}
          </button>
          <button className="btn btn-ghost" onClick={() => onTracking({ not_interested: !job.tracking?.not_interested })}>
            {job.tracking?.not_interested ? "↩ Undo dismiss" : "Dismiss role"}
          </button>
          <a className="btn btn-subtle" href={job.url} target="_blank" rel="noreferrer" style={{ justifyContent: "center" }}>
            View on LinkedIn ↗
          </a>
        </div>
      </div>
    </aside>
  );
}
