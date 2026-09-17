import { useEffect, useState } from "react";

import { confirmScores, getProfile, type ScoreTable } from "../api";

type Row = { key: keyof ScoreTable; label: string; note: string };

const LEVEL_ROWS: Row[] = [
  { key: "intern", label: "Intern", note: "internship or placement" },
  { key: "entry", label: "Entry", note: "0–2 years" },
  { key: "mid_senior", label: "Mid–senior", note: "2–5 years" },
  { key: "senior", label: "Senior", note: "5–9 years" },
  { key: "staff_principal", label: "Staff / principal", note: "9+ years" },
];

/** Neither of these is a rung on the ladder, so they stay pinned below the
 *  levels however they're scored. */
const OTHER_ROWS: Row[] = [
  { key: "not_a_fit", label: "Not a fit", note: "contract, temporary or part-time freelance" },
  { key: "unknown", label: "Level unclear", note: "the posting doesn't say" },
];

/** Best fit first: the levels are ordered by the score we propose, so the ones
 *  you're aiming at sit at the top of the table instead of whichever level
 *  happens to come first on the ladder. Ties keep ladder order. */
export function orderedRows(scores: ScoreTable): Row[] {
  const levels = LEVEL_ROWS.map((row, ladder) => ({ row, ladder }))
    .sort((a, b) => scores[b.row.key] - scores[a.row.key] || a.ladder - b.ladder)
    .map(({ row }) => row);
  return [...levels, ...OTHER_ROWS];
}

/** We score seniority 0-5. These are our proposals from the levels you picked;
 *  change any of them, then confirm. Locked to this profile version afterwards. */
export function ScoresStep({ onConfirmed }: { onConfirmed: () => void }) {
  const [scores, setScores] = useState<ScoreTable | null>(null);
  // Fixed when the proposal arrives: re-sorting under the cursor as you edit
  // would move the buttons you are aiming at.
  const [rows, setRows] = useState<Row[]>([...LEVEL_ROWS, ...OTHER_ROWS]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getProfile()
      .then(({ profile }) => {
        if (!profile) return;
        const table = profile.seniority_scores ?? profile.proposed_scores;
        setScores(table);
        setRows(orderedRows(table));
      })
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
        Score every level 0–5, where <strong>5 is the role you most want</strong> and 0 is one you'd never take. Best fit
        first: here's what we propose from the levels you picked — change anything that doesn't match how you'd rate a
        job, then confirm.
      </p>
      {error && <div className="banner error">{error}</div>}
      <table className="score-table">
        <tbody>
          {rows.map((row) => (
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
      <p className="score-legend">
        <span>0 — not for me</span>
        <span>5 — exactly what I want</span>
      </p>
      <div className="row-actions">
        <button className="btn btn-primary grow" onClick={save} disabled={busy}>
          {busy ? "Scoring…" : "Launch my board →"}
        </button>
      </div>
      <p className="action-note">Editable later in settings; your board is rescored when you change it.</p>
    </>
  );
}
