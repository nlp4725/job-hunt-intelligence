import { useEffect, useState } from "react";

import { confirmScores, getProfile, type ScoreTable } from "../api";

const ROWS: { key: keyof ScoreTable; label: string; note: string }[] = [
  { key: "intern", label: "Intern", note: "internship or placement" },
  { key: "entry", label: "Entry", note: "0–2 years" },
  { key: "mid_senior", label: "Mid–senior", note: "2–5 years" },
  { key: "senior", label: "Senior", note: "5–9 years" },
  { key: "staff_principal", label: "Staff / principal", note: "9+ years" },
  { key: "not_a_fit", label: "Not a fit", note: "contract, temporary or part-time freelance" },
  { key: "unknown", label: "Level unclear", note: "the posting doesn't say" },
];

/** We score seniority 0–5. These are our proposals from the level you picked;
 *  change any of them, then confirm. Locked to this profile version afterwards. */
export function ScoresStep({ onConfirmed }: { onConfirmed: () => void }) {
  const [scores, setScores] = useState<ScoreTable | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getProfile()
      .then(({ profile }) => profile && setScores(profile.seniority_scores ?? profile.proposed_scores))
      .catch((err: Error) => setError(err.message));
  }, []);

  async function save() {
    if (!scores) return;
    setBusy(true);
    setError(null);
    try {
      await confirmScores(scores);
      onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  if (!scores) return <p className="lede pulse">Loading your proposed scores…</p>;

  return (
    <>
      <h2 className="step-title">How much is each level worth?</h2>
      <p className="lede">
        We score seniority 0–5. Here's what we propose from the level you picked — change anything that doesn't match how
        you'd rate a job, then confirm.
      </p>
      {error && <div className="banner error">{error}</div>}
      <table className="score-table">
        <tbody>
          {ROWS.map((row) => (
            <tr key={row.key}>
              <td>
                {row.label}
                <span className="step-note">{row.note}</span>
              </td>
              <td className="center">
                <div className="score-choice" role="group" aria-label={row.label}>
                  {[0, 1, 2, 3, 4, 5].map((value) => (
                    <button
                      key={value}
                      className={`score-dot${scores[row.key] === value ? " chosen" : ""}`}
                      aria-pressed={scores[row.key] === value}
                      onClick={() => setScores({ ...scores, [row.key]: value })}
                    >
                      {value}
                    </button>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row-actions">
        <button className="btn btn-primary grow" onClick={save} disabled={busy}>
          {busy ? "Scoring…" : "Launch my board →"}
        </button>
      </div>
      <p className="action-note">Editable later in settings; your board is rescored when you change it.</p>
    </>
  );
}
