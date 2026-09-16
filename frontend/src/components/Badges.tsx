import type { CSSProperties } from "react";
import type { Job } from "../api";

export function colorVar(color: string): CSSProperties {
  return { "--c": color } as CSSProperties;
}

const WORKPLACE_COLORS: Record<string, string> = {
  Remote: "var(--purple)",
  Hybrid: "var(--blue)",
  "On-site": "var(--teal)",
};

export function WorkplaceBadge({ workplace }: { workplace: string | null }) {
  if (!workplace) {
    return <span className="pill neutral" title="No workplace type captured for this job">Unknown</span>;
  }
  return <span className="pill" style={colorVar(WORKPLACE_COLORS[workplace] ?? "var(--text-secondary)")}>{workplace}</span>;
}

export function StatusBadge({ job }: { job: Job }) {
  if (job.applied) return <span className="pill" style={colorVar("var(--blue)")}>Applied</span>;
  if (job.not_interested) return <span className="pill" style={colorVar("var(--red)")}>Not interested</span>;
  if (job.expired) return <span className="pill" style={colorVar("var(--orange)")}>Expired</span>;
  if (job.duplicate_of_job_id) return <span className="pill neutral">Duplicate</span>;
  return <span className="pill neutral">Open</span>;
}
