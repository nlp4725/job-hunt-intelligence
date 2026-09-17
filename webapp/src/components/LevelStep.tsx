import { useState } from "react";

import { MAX_SENIORITY_TARGETS, pickLevels, SENIORITY_LEVELS, type SeniorityLevel } from "../api";

const LEVELS: { id: SeniorityLevel; label: string; years: string }[] = [
  { id: "intern", label: "Intern", years: "internship or placement" },
  { id: "entry", label: "Entry", years: "0–2 years" },
  { id: "mid_senior", label: "Mid–senior", years: "2–5 years" },
  { id: "senior", label: "Senior", years: "5–9 years" },
  { id: "staff_principal", label: "Staff / principal", years: "9+ years" },
];

/** What you're aiming for, not what a resume implies: your choices decide the
 *  proposed score table on the next screen. Up to three, because plenty of
 *  people would take a senior or a staff role and mean it — every level you
 *  pick scores full marks, and the rest fall away by distance from the
 *  nearest one. */
export function LevelStep({ onPicked }: { onPicked: () => void }) {
  const [chosen, setChosen] = useState<SeniorityLevel[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const full = chosen.length >= MAX_SENIORITY_TARGETS;

  /** Keep the picks in level order, so the next screen's proposal and the
   *  summary line read the way the ladder does rather than in click order. */
  function toggle(level: SeniorityLevel) {
    if (chosen.includes(level)) return setChosen(chosen.filter((id) => id !== level));
    if (full) return;
    setChosen(SENIORITY_LEVELS.filter((id) => id === level || chosen.includes(id)));
  }

  async function save() {
    if (chosen.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      await pickLevels(chosen);
      onPicked();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <>
      <h2 className="step-title">What seniority are you aiming for?</h2>
      <p className="lede">
        Pick up to {MAX_SENIORITY_TARGETS} levels you'd genuinely take. We weight the seniority signal on them: jobs at
        any level you pick score highest.
      </p>
      {error && <div className="banner error">{error}</div>}
      <div className="choices">
        {LEVELS.map((level) => {
          const picked = chosen.includes(level.id);
          return (
            <button
              key={level.id}
              className={`choice${picked ? " chosen" : ""}${!picked && full ? " muted" : ""}`}
              onClick={() => toggle(level.id)}
              aria-pressed={picked}
              disabled={!picked && full}
              title={!picked && full ? `Deselect one first — ${MAX_SENIORITY_TARGETS} is the most you can pick` : undefined}
            >
              <span className="choice-box" aria-hidden="true">{picked ? "✓" : ""}</span>
              <span className="choice-label">{level.label}</span>
              <span className="choice-note">{level.years}</span>
            </button>
          );
        })}
      </div>
      <div className="row-actions">
        <button className="btn btn-primary grow" onClick={save} disabled={chosen.length === 0 || busy}>
          {busy ? "Saving…" : "Continue →"}
        </button>
      </div>
      <p className="action-note">
        {chosen.length === 0
          ? `Pick at least one · up to ${MAX_SENIORITY_TARGETS}`
          : `${chosen.length} of ${MAX_SENIORITY_TARGETS} picked · you can change this later in settings`}
      </p>
    </>
  );
}
