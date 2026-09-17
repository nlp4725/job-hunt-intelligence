import { signIn } from "../auth";
import { useMe } from "../useMe";

const STEPS = [
  ["resume", "Upload your resume"],
  ["skills_confirmed", "Confirm the skills we found"],
  ["level", "Pick your seniority level"],
  ["scores_confirmed", "Confirm your 0–5 score table"],
] as const;

export function Home() {
  const { me, error, loading } = useMe();

  if (loading) return <p className="muted">Loading…</p>;

  if (!me) {
    return (
      <>
        <p className="eyebrow">A screened job board</p>
        <h1>Jobs worth your time, scored against your resume</h1>
        <div className="panel">
          <p>
            Every posting here was collected and screened by hand: agencies and reposts removed, seniority classified
            once, skills extracted. You add a resume and a seniority target, and every job gets your own score.
          </p>
          <button onClick={() => signIn("signup")}>Create an account</button>{" "}
          <button className="secondary" onClick={() => signIn()}>Sign in</button>
        </div>
      </>
    );
  }

  const { onboarding } = me;
  return (
    <>
      <p className="eyebrow">Signed in as {me.email}</p>
      <h1>Your setup</h1>
      {error && <div className="panel error">{error}</div>}
      <div className="panel">
        <h2>Onboarding</h2>
        <ul className="steps">
          {STEPS.map(([key, label]) => (
            <li key={key}>
              <span className={`mark ${onboarding[key] ? "done" : "todo"}`}>{onboarding[key] ? "✓" : "○"}</span>
              <span>{label}</span>
            </li>
          ))}
          <li>
            <span className={`mark ${onboarding.expertise === "done" ? "done" : "todo"}`}>
              {onboarding.expertise === "done" ? "✓" : "○"}
            </span>
            <span>
              Expertise Match <span className="tag">paid</span>{" "}
              <span className="muted">
                {onboarding.expertise === "locked" ? "— on the paid plan; skippable" : `— ${onboarding.expertise}`}
              </span>
            </span>
          </li>
        </ul>
        <p className="muted">
          {onboarding.complete
            ? onboarding.board_scored
              ? "Your board is scored and ready."
              : "Scoring your board…"
            : "The next screens (resume upload, skills, level, score table) are being built."}
        </p>
      </div>
    </>
  );
}
