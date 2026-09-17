import { useState } from "react";

import { pickLevel, SENIORITY_LEVELS, type SeniorityLevel } from "../api";

const LEVELS: { id: SeniorityLevel; label: string; years: string }[] = [
  { id: "intern", label: "Intern", years: "internship or placement" },
  { id: "entry", label: "Entry", years: "0–2 years" },
  { id: "mid_senior", label: "Mid–senior", years: "2–5 years" },
  { id: "senior", label: "Senior", years: "5–9 years" },
  { id: "staff_principal", label: "Staff / principal", years: "9+ years" },
];

/** What you're aiming for, not what a resume implies: your choice decides the
 *  proposed score table on the next screen. */
export function LevelStep({ onPicked }: { onPicked: () => void }) {
  const [chosen, setChosen] = useState<SeniorityLevel | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      await pickLevel(chosen);
      onPicked();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Which level are you aiming for?</h1>
      <p className="lede">Jobs at that level score highest; the further a job sits from it, the lower it scores.</p>
      {error && <div className="banner error">{error}</div>}
      <div className="choices">
        {LEVELS.map((level) => (
          <button
            key={level.id}
            className={`choice${chosen === level.id ? " chosen" : ""}`}
            onClick={() => setChosen(level.id)}
            aria-pressed={chosen === level.id}
          >
            <span className="choice-label">{level.label}</span>
            <span className="choice-note">{level.years}</span>
          </button>
        ))}
      </div>
      <div className="row-actions">
        <button className="btn btn-primary grow" onClick={save} disabled={!chosen || busy}>
          {busy ? "Saving…" : "Continue"}
        </button>
      </div>
      <p className="action-note">{SENIORITY_LEVELS.length} levels · you can change this later in settings</p>
    </>
  );
}
