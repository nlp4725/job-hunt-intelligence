import { useState } from "react";
import type { Track } from "../api";
import { DEFAULT_FILTERS, POSTED_WITHIN_OPTIONS, SENIORITY_OPTIONS, type Filters } from "../jobs";

type ToggleKey = "softwareOnly" | "top500Only" | "hideConsulting" | "toCOnly" | "showCrossedOff" | "hideDuplicates";

const TOGGLES: { key: ToggleKey; label: string; title?: string; pmOnly?: boolean }[] = [
  { key: "softwareOnly", label: "Software companies only" },
  { key: "top500Only", label: "Top 500 tech companies only" },
  {
    key: "hideConsulting",
    label: "Hide consulting / IT services firms",
    title:
      'Hides companies whose LinkedIn industry is "Business Consulting and Services" or "IT Services and IT Consulting" — where most staffing intermediaries sit. Blunt: it also hides genuine employers with those tags.',
  },
  { key: "toCOnly", label: "Customer-facing (to-C) product only", pmOnly: true },
  { key: "showCrossedOff", label: "Show crossed off (applied / expired / not interested)" },
  { key: "hideDuplicates", label: "Hide duplicates" },
];

type Props = {
  filters: Filters;
  onChange: (patch: Partial<Filters>) => void;
  track: Track;
  resultCount: number;
  searchOnly: boolean;
};

export default function FilterBar({ filters, onChange, track, resultCount, searchOnly }: Props) {
  const [showMore, setShowMore] = useState(false);
  const toggles = TOGGLES.filter((t) => !t.pmOnly || track === "pm");
  const changedToggles = toggles.filter((t) => filters[t.key] !== DEFAULT_FILTERS[t.key]).length;

  return (
    <>
      <div className="filter-bar">
        <input
          type="text"
          className="search"
          placeholder="Filter by title or company…"
          value={filters.query}
          onChange={(e) => onChange({ query: e.target.value })}
        />
        {!searchOnly && (
          <>
            <select
              value={filters.postedWithinDays}
              onChange={(e) => onChange({ postedWithinDays: Number(e.target.value) })}
              title="Jobs older than 2 weeks are always hidden"
            >
              {POSTED_WITHIN_OPTIONS.map(([days, label]) => (
                <option key={days} value={days}>{label}</option>
              ))}
            </select>
            <select value={filters.workplace} onChange={(e) => onChange({ workplace: e.target.value })}>
              <option value="">Workplace: any</option>
              <option value="Remote">Remote</option>
              <option value="Hybrid">Hybrid</option>
              <option value="On-site">On-site</option>
              <option value="unknown">Unknown</option>
            </select>
            <select value={filters.seniority} onChange={(e) => onChange({ seniority: e.target.value })}>
              {SENIORITY_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            <select value={filters.companySize} onChange={(e) => onChange({ companySize: e.target.value })}>
              <option value="">Company size: any</option>
              <option value="500+">500+ employees</option>
              <option value="20-500">20–500 employees</option>
              <option value="<20">&lt;20 employees</option>
            </select>
            <button className={`btn btn-ghost${showMore ? " pressed" : ""}`} onClick={() => setShowMore((s) => !s)}>
              More filters{changedToggles > 0 ? ` · ${changedToggles}` : ""}
            </button>
          </>
        )}
        <span className="result-count mono">{resultCount.toLocaleString()} results</span>
      </div>

      {!searchOnly && showMore && (
        <div className="filter-more">
          {toggles.map((t) => (
            <label key={t.key} className="check" title={t.title}>
              <input type="checkbox" checked={filters[t.key]} onChange={(e) => onChange({ [t.key]: e.target.checked } as Partial<Filters>)} />
              {t.label}
            </label>
          ))}
          <button className="btn btn-ghost" onClick={() => onChange(DEFAULT_FILTERS)}>Reset all filters</button>
        </div>
      )}
    </>
  );
}
