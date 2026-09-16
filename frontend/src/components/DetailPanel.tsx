import { useEffect, useState, type ReactNode } from "react";
import type { Job, JobPatch } from "../api";
import { formatDate, sizeLabel, totalColor } from "../jobs";
import { WorkplaceBadge, colorVar } from "./Badges";

const SIGNALS = [
  { key: "skill_score", label: "Skill match", color: "var(--indigo)" },
  { key: "seniority_score", label: "Seniority fit", color: "var(--green)" },
  { key: "expertise_score", label: "Expertise match", color: "var(--amber)" },
] as const;

type Props = {
  job: Job;
  saving: boolean;
  onClose: () => void;
  onUpdate: (updates: JobPatch) => Promise<boolean>;
};

// Keyed by job id in the parent, so drafts reset when the selection changes.
export default function DetailPanel({ job, saving, onClose, onUpdate }: Props) {
  const [note, setNote] = useState(job.note ?? "");
  const [reason, setReason] = useState(job.not_interested_note ?? "");
  const [askingReason, setAskingReason] = useState(false);
  const isDuplicate = job.duplicate_of_job_id !== null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const saveText = (field: "note" | "not_interested_note", draft: string) => {
    const value = draft.trim() ? draft : null;
    if (value !== job[field]) onUpdate({ [field]: value } as JobPatch);
  };

  const markNotInterested = async () => {
    if (await onUpdate({ not_interested: true, not_interested_note: reason.trim() ? reason : null })) setAskingReason(false);
  };

  const details: [string, ReactNode][] = [
    ["Industry", job.company_industry ?? "—"],
    ["Size", sizeLabel(job.company_size) ?? "—"],
    ["Workplace", <WorkplaceBadge workplace={job.workplace_type} />],
    ["Location", job.location ?? "—"],
    ["Posted", <span title={job.posted_date_raw ? `As scraped: "${job.posted_date_raw}"` : undefined}>{formatDate(job.posted_at)}</span>],
    ["Top 500 tech", job.top500_tech ? "Yes" : "No"],
    ...(job.track === "pm" ? [["To-C product", job.to_c_product_pm ? "Yes" : "No"] as [string, ReactNode]] : []),
    ["Applied at company", job.company_applied_count > 0 ? `${job.company_applied_count}×` : "Never"],
  ];

  return (
    <aside className="panel fadein">
      <div className="panel-head">
        <div className="panel-head-text">
          <div className="panel-title">{job.title || "(untitled)"}</div>
          <div className="panel-company">{job.company_name}</div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close details">×</button>
      </div>

      <div className="panel-body">
        {isDuplicate ? (
          <div className="callout">
            <strong>Not screened — duplicate listing.</strong> Near-exact repost of “{job.duplicate_of_title || "(untitled)"}”
            {job.duplicate_of_total_score !== null ? `, which scored ${job.duplicate_of_total_score}/15` : ""}
            {job.duplicate_of_applied ? " and is already applied to" : ""}.
          </div>
        ) : (
          <section>
            {/* total_score is a plain unweighted sum for default ranking, not a
                verdict (CONTEXT.md) — the three signals stay visible beneath it. */}
            <p className="section-label">Total score</p>
            <div className="big-score" style={colorVar(totalColor(job.total_score))}>
              <span className="mono">{job.total_score ?? "—"}</span>
              <span className="big-score-of">/ 15 · sum of three signals</span>
            </div>
            {SIGNALS.map((s) => {
              const value = job[s.key];
              return (
                <div key={s.key} className="bar-row" style={colorVar(s.color)}>
                  <div className="bar-label">
                    <span>{s.label}</span>
                    <span className="mono bar-value">{value ?? "—"}/5</span>
                  </div>
                  <div className="bar"><div style={{ width: `${((value ?? 0) / 5) * 100}%` }} /></div>
                </div>
              );
            })}
          </section>
        )}

        <section>
          <p className="section-label">Details</p>
          {details.map(([label, value]) => (
            <div key={label} className="detail-row">
              <span>{label}</span>
              <span>{value}</span>
            </div>
          ))}
        </section>

        <section>
          <p className="section-label">Notes</p>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} onBlur={() => saveText("note", note)} placeholder="Add a note…" />
        </section>

        {(job.not_interested || askingReason) && (
          <section>
            <p className="section-label">Why not interested</p>
            <input
              type="text"
              className="full"
              value={reason}
              autoFocus={askingReason}
              onChange={(e) => setReason(e.target.value)}
              onBlur={() => job.not_interested && saveText("not_interested_note", reason)}
              onKeyDown={(e) => e.key === "Enter" && askingReason && markNotInterested()}
              placeholder="e.g. requires clearance"
            />
            {askingReason && (
              <div className="row-actions">
                <button className="btn btn-primary" onClick={markNotInterested} disabled={saving}>Mark not interested</button>
                <button className="btn btn-ghost" onClick={() => setAskingReason(false)}>Cancel</button>
              </div>
            )}
          </section>
        )}

        <section className="actions">
          <button className="btn btn-primary btn-block" disabled={saving} onClick={() => onUpdate({ applied: !job.applied })}>
            {job.applied ? "✓ Applied — undo" : "Mark as applied"}
          </button>
          {job.applied && job.applied_at && (
            <p className="action-note">
              Applied {formatDate(job.applied_at)}
              {job.applied_resume_version ? ` with resume ${job.applied_resume_version}` : ""}
            </p>
          )}
          <div className="row-actions">
            <button className="btn btn-ghost grow" disabled={saving} onClick={() => onUpdate({ expired: !job.expired })}>
              {job.expired ? "↩ Undo expired" : "Mark expired"}
            </button>
            {job.not_interested ? (
              <button className="btn btn-ghost grow" disabled={saving} onClick={() => onUpdate({ not_interested: false })}>↩ Undo not interested</button>
            ) : (
              <button className="btn btn-ghost grow" disabled={saving || askingReason} onClick={() => setAskingReason(true)}>Not interested</button>
            )}
          </div>
          {job.url && (
            <a className="btn btn-subtle btn-block" href={job.url} target="_blank" rel="noopener noreferrer">View on LinkedIn ↗</a>
          )}
        </section>
      </div>
    </aside>
  );
}
