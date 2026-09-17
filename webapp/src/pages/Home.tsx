import { Brand } from "../components/Brand";
import { signIn } from "../auth";
import { useMe } from "../useMe";
import type { Onboarding } from "../api";

const STEPS: { key: keyof Onboarding; label: string; note: string }[] = [
  { key: "resume", label: "Upload your resume", note: "PDF, DOCX or plain text. It is encrypted and only you can read it." },
  { key: "skills_confirmed", label: "Confirm the skills we found", note: "Add or remove any; this is what every job is matched against." },
  { key: "level", label: "Pick your seniority level", note: "Intern · Entry · Mid–senior · Senior · Staff / principal." },
  { key: "scores_confirmed", label: "Confirm your 0–5 score table", note: "How much each job level is worth to you. Adjustable later." },
];

const EXPERTISE_NOTE: Record<Onboarding["expertise"], string> = {
  locked: "On the paid plan. You can skip it.",
  pending: "Draft it from your resume, or skip it.",
  skipped: "Skipped. You can add it in settings.",
  done: "Ready.",
};

export function Home() {
  const { me, error, loading } = useMe();

  if (loading) {
    return (
      <div className="status-message">
        <span className="pulse">Loading…</span>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="centered">
        <div className="card fadein">
          <div className="brand-row">
            <Brand />
          </div>
          <h1>Jobs worth your time, scored against your resume</h1>
          <p className="lede">
            Every posting here is collected and screened by hand: agencies and reposts removed, seniority classified
            once, skills extracted. Add your resume and a seniority target, and each job gets your own score.
          </p>
          {error && <div className="banner error">{error}</div>}
          <div className="row-actions">
            <button className="btn btn-primary grow" onClick={() => signIn("signup")}>
              Create an account
            </button>
            <button className="btn btn-ghost grow" onClick={() => signIn()}>
              Sign in
            </button>
          </div>
        </div>
      </div>
    );
  }

  const { onboarding } = me;
  const nextStep = STEPS.find((step) => !onboarding[step.key]);

  return (
    <div className="centered">
      <div className="card fadein">
        <h1>{onboarding.complete ? "You're set up" : "Finish setting up"}</h1>
        <p className="lede">
          {onboarding.complete
            ? onboarding.board_scored
              ? "Your board is scored and ready."
              : "Scoring your board — this takes a few seconds."
            : "Four short steps, then every job gets your own score."}
        </p>
        {error && <div className="banner error">{error}</div>}

        <ul className="steps">
          {STEPS.map((step) => (
            <li key={step.key}>
              <span className={`step-mark${onboarding[step.key] ? " done" : ""}`}>{onboarding[step.key] ? "✓" : ""}</span>
              <span className="step-text">
                {step.label}
                <span className="step-note">{step.note}</span>
              </span>
            </li>
          ))}
          <li>
            <span className={`step-mark${onboarding.expertise === "done" ? " done" : ""}`}>
              {onboarding.expertise === "done" ? "✓" : ""}
            </span>
            <span className="step-text">
              Expertise Match <span className="pill neutral">paid</span>
              <span className="step-note">{EXPERTISE_NOTE[onboarding.expertise]}</span>
            </span>
          </li>
        </ul>

        <div className="banner info" style={{ marginTop: 16, marginBottom: 0 }}>
          {nextStep ? `Next: ${nextStep.label.toLowerCase()} — that screen is being built.` : "The board screen is being built."}
        </div>
      </div>
    </div>
  );
}
