import { useState } from "react";
import type { Job } from "../api";
import { formatDate, signalColor, sizeLabel, totalColor, type Sort, type SortKey } from "../jobs";
import { StatusBadge, WorkplaceBadge, colorVar } from "./Badges";

// Thousands of rows pass the filters on a wide window; rendering them all at
// once makes every keystroke in the search box lag. Grow on demand instead.
const PAGE_SIZE = 200;

function SortHeader({ label, sortKey, sort, onSort, title }: {
  label: string; sortKey?: SortKey; sort: Sort; onSort: (key: SortKey) => void; title?: string;
}) {
  if (!sortKey) return <th title={title}>{label}</th>;
  const active = sort.key === sortKey;
  return (
    <th className={`sortable${active ? " sorted" : ""}`} onClick={() => onSort(sortKey)} title={title}>
      {label}
      <span className="sort-icon">{active ? (sort.dir === -1 ? "↓" : "↑") : "↕"}</span>
    </th>
  );
}

type Props = {
  jobs: Job[];
  sort: Sort;
  onSort: (key: SortKey) => void;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onToggleApplied: (job: Job) => void;
  savingIds: ReadonlySet<number>;
  dimCrossedOff: boolean;
  emptyMessage: string;
};

export default function JobTable({ jobs, sort, onSort, selectedId, onSelect, onToggleApplied, savingIds, dimCrossedOff, emptyMessage }: Props) {
  const [limit, setLimit] = useState(PAGE_SIZE);
  const header = { sort, onSort };

  return (
    <div className="table-wrap">
      <table>
        <colgroup>
          <col style={{ width: 44 }} />
          <col style={{ width: 62 }} />
          <col style={{ width: 52 }} />
          <col style={{ width: 56 }} />
          <col style={{ width: 52 }} />
          <col />
          <col style={{ width: 150 }} />
          <col style={{ width: 112 }} />
          <col style={{ width: 96 }} />
          <col style={{ width: 80 }} />
          <col style={{ width: 70 }} />
          <col style={{ width: 110 }} />
        </colgroup>
        <thead>
          <tr>
            <SortHeader label="#" {...header} />
            <SortHeader label="Score" sortKey="total_score" {...header} />
            <SortHeader label="Skill" sortKey="skill_score" {...header} />
            <SortHeader label="Senr." sortKey="seniority_score" title="Seniority fit" {...header} />
            <SortHeader label="Exp." sortKey="expertise_score" title="Expertise match" {...header} />
            <SortHeader label="Job" sortKey="title" {...header} />
            <SortHeader label="Industry" sortKey="company_industry" {...header} />
            <SortHeader label="Size" sortKey="company_size" {...header} />
            <SortHeader label="Workplace" sortKey="workplace_type" {...header} />
            <SortHeader label="Posted" sortKey="posted_at" {...header} />
            <SortHeader label="Applied" sortKey="applied_at" {...header} />
            <SortHeader label="Status" {...header} />
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 && (
            <tr className="empty-row">
              <td colSpan={12} className="empty">{emptyMessage}</td>
            </tr>
          )}
          {jobs.slice(0, limit).map((job, i) => {
            const isSelected = job.id === selectedId;
            const isDuplicate = job.duplicate_of_job_id !== null;
            const dim = dimCrossedOff && (job.applied || job.expired || job.not_interested);
            const rowClass = [isSelected && "selected", dim && "dim"].filter(Boolean).join(" ");
            return (
              <tr key={job.id} className={rowClass} onClick={() => onSelect(isSelected ? null : job.id)}>
                <td className="mono cell-muted">{i + 1}</td>
                <td>
                  {isDuplicate ? (
                    <span className="total-pill mono" style={colorVar("var(--text-tertiary)")} title="Not screened — near-exact repost of an already-scraped job">DUP</span>
                  ) : (
                    <span className="total-pill mono" style={colorVar(totalColor(job.total_score))}>{job.total_score ?? "—"}</span>
                  )}
                </td>
                {[job.skill_score, job.seniority_score, job.expertise_score].map((value, k) => (
                  <td key={k}>
                    <span className="score mono" style={colorVar(signalColor(isDuplicate ? null : value))}>
                      {isDuplicate || value === null ? "—" : value}
                    </span>
                  </td>
                ))}
                <td className="cell-job">
                  <div className="job-title-row">
                    <span className="job-title" title={job.title ?? undefined}>{job.title || "(untitled)"}</span>
                    {job.url && (
                      <a className="job-link" href={job.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} title="Open on LinkedIn">↗</a>
                    )}
                  </div>
                  <div className="job-company">
                    <span>{job.company_name}</span>
                    {job.company_applied_count > 0 && (
                      // 3+ reads as "you keep applying here" — amber so it's noticed while scanning.
                      <span
                        className={`applied-count${job.company_applied_count >= 3 ? " repeat" : ""}`}
                        title={`You have marked ${job.company_applied_count} job(s) at ${job.company_name} as applied`}
                      >
                        applied {job.company_applied_count}×
                      </span>
                    )}
                  </div>
                  {isDuplicate && (
                    <div className="dup-note">
                      Duplicate of: {job.duplicate_of_title || "(untitled)"}
                      {job.duplicate_of_applied ? " — already applied" : ""}
                      {job.duplicate_of_total_score !== null ? ` (score ${job.duplicate_of_total_score})` : ""}
                    </div>
                  )}
                </td>
                <td className="cell-industry" title={job.company_industry ?? undefined}>{job.company_industry ?? "—"}</td>
                <td className="mono cell-muted">{sizeLabel(job.company_size) ?? "—"}</td>
                <td><WorkplaceBadge workplace={job.workplace_type} /></td>
                <td className="mono cell-muted" title={job.posted_date_raw ? `As scraped: "${job.posted_date_raw}"` : undefined}>
                  {formatDate(job.posted_at)}
                </td>
                <td className="center" onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={job.applied}
                    disabled={savingIds.has(job.id)}
                    onChange={() => onToggleApplied(job)}
                    aria-label={`Mark as applied: ${job.title ?? ""}`}
                  />
                </td>
                <td><StatusBadge job={job} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {jobs.length > limit && (
        <div className="show-more">
          <button className="btn btn-ghost" onClick={() => setLimit((l) => l + PAGE_SIZE)}>
            Show {Math.min(PAGE_SIZE, jobs.length - limit)} more · {(jobs.length - limit).toLocaleString()} hidden
          </button>
        </div>
      )}
    </div>
  );
}
