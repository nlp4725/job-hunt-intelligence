import type { BoardJob } from "../api";

/** The board table, ported from the Figma Make dashboard. Score columns show
 *  this user's own scores; Expertise only appears on the paid plan. */
export type SortKey = "total" | "skill" | "seniority" | "expertise";

const WORKPLACE: Record<string, string> = { Remote: "#7c3aed", Hybrid: "#2563eb", "On-site": "#059669" };

/** LinkedIn's "posted" text is relative and goes stale in storage; show the
 *  date we collected the posting. */
function addedOn(iso: string | null): string {
  if (!iso) return "—";
  const when = new Date(iso + (iso.endsWith("Z") ? "" : "Z"));
  return when.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export const totalColor = (value: number) => (value >= 9 ? "#16a34a" : value >= 7 ? "#65a30d" : value >= 5 ? "#d97706" : "#dc2626");
const signalColor = (value: number) => (value >= 5 ? "#16a34a" : value >= 4 ? "#65a30d" : value >= 3 ? "#d97706" : "#dc2626");

function Score({ value }: { value: number | null }) {
  if (value === null) return <span className="demo-dash">—</span>;
  return <span className="mono demo-signal" style={{ color: signalColor(value) }}>{value}</span>;
}

export function StatusBadge({ job }: { job: BoardJob }) {
  if (job.tracking?.applied) return <span className="demo-pill" style={{ "--c": "#2563eb" } as React.CSSProperties}>Applied</span>;
  if (job.tracking?.not_interested) return <span className="demo-pill" style={{ "--c": "#dc2626" } as React.CSSProperties}>Skipped</span>;
  if (job.is_contract) return <span className="demo-pill" style={{ "--c": "#ea580c" } as React.CSSProperties}>Contract</span>;
  return <span className="demo-pill neutral">New</span>;
}

type Props = {
  jobs: BoardJob[];
  paid: boolean;
  selectedId: number | null;
  sort: { key: SortKey; dir: "asc" | "desc" };
  onSort: (key: SortKey) => void;
  onSelect: (job: BoardJob) => void;
  onApplied: (job: BoardJob) => void;
};

export function JobTable({ jobs, paid, selectedId, sort, onSort, onSelect, onApplied }: Props) {
  const header = (label: string, key?: SortKey) => (
    <th
      key={label}
      className={key ? `sortable${sort.key === key ? " sorted" : ""}` : undefined}
      onClick={key ? () => onSort(key) : undefined}
    >
      {label}
      {key && <span className="sort-icon">{sort.key === key ? (sort.dir === "desc" ? "↓" : "↑") : "↕"}</span>}
    </th>
  );

  return (
    <table>
      <thead>
        <tr>
          {header("#")}
          {header("Score", "total")}
          {header("Skill", "skill")}
          {header("Senr.", "seniority")}
          {paid && header("Exp.", "expertise")}
          {header("Job")}
          {header("Location")}
          {header("Workplace")}
          {header("Added")}
          {header("Applied")}
          {header("Status")}
        </tr>
      </thead>
      <tbody>
        {jobs.map((job, index) => {
          const scores = job.scores;
          const dim = job.tracking?.not_interested;
          return (
            <tr
              key={job.id}
              className={`${selectedId === job.id ? "selected" : ""}${dim ? " dim" : ""}`}
              onClick={() => onSelect(job)}
            >
              <td className="mono cell-muted">{index + 1}</td>
              <td>
                {scores?.total_score === null || scores === null ? (
                  <span className="demo-dash">—</span>
                ) : (
                  <span className="total-pill mono" style={{ "--c": totalColor(scores.total_score!) } as React.CSSProperties}>
                    {scores.total_score}
                  </span>
                )}
              </td>
              <td><Score value={scores?.skill_score ?? null} /></td>
              <td><Score value={scores?.seniority_fit ?? null} /></td>
              {paid && <td><Score value={job.expertise ? Math.round(job.expertise.expertise_score) : null} /></td>}
              <td>
                <div className="job-title-row">
                  <div style={{ minWidth: 0 }}>
                    <a
                      className="job-title link"
                      href={job.url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(event) => event.stopPropagation()}
                      title="Open the posting on LinkedIn"
                    >
                      {job.title}
                    </a>
                    <div className="job-company">
                      {job.company}
                      {job.company_applied_count > 0 && (
                        <span className={`applied-count${job.company_applied_count > 1 ? " repeat" : ""}`}>
                          · {job.company_applied_count} applied
                        </span>
                      )}
                    </div>
                  </div>
                  <a className="job-link" href={job.url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()} aria-label="Open on LinkedIn">
                    ↗
                  </a>
                </div>
              </td>
              <td className="cell-industry">{job.location ?? "—"}</td>
              <td>
                {job.workplace_type ? (
                  <span className="demo-pill" style={{ "--c": WORKPLACE[job.workplace_type] ?? "#7a6248" } as React.CSSProperties}>
                    {job.workplace_type}
                  </span>
                ) : (
                  <span className="demo-pill neutral">Unknown</span>
                )}
              </td>
              <td className="mono cell-muted">{addedOn(job.first_seen_at)}</td>
              <td className="center" onClick={(event) => event.stopPropagation()}>
                <input type="checkbox" checked={job.tracking?.applied ?? false} onChange={() => onApplied(job)} />
              </td>
              <td><StatusBadge job={job} /></td>
            </tr>
          );
        })}
        {jobs.length === 0 && (
          <tr className="empty-row">
            <td className="empty" colSpan={paid ? 11 : 10}>
              No jobs match these filters.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
