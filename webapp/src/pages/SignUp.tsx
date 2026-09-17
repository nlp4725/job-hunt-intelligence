import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { asAuthError, passwordProblem, PASSWORD_RULES, signInWithPassword, signUpWithPassword } from "../auth";
import { AuthLayout, Field } from "./AuthLayout";
import { ConfirmCodeForm } from "./ConfirmCode";

/** /signup — create the account, then spend the code Cognito emails. The two
 *  steps are one page: a user who is sent somewhere else for the code loses the
 *  password they just typed, and with it the chance to be signed in
 *  automatically once the code checks out. */
export function SignUp() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsCode, setNeedsCode] = useState(false);

  // Shown as a hint while typing, not as an error after submitting — the pool's
  // policy should never be a surprise.
  const problem = password ? passwordProblem(password) : null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { confirmed } = await signUpWithPassword(email, password);
      if (confirmed) {
        // Only possible if the pool is later changed to auto-confirm; sign them
        // straight in rather than asking for a code that will never arrive.
        await signInWithPassword(email, password);
        navigate("/", { replace: true });
      } else {
        setNeedsCode(true);
      }
    } catch (err) {
      setError(asAuthError(err).message);
    } finally {
      setBusy(false);
    }
  }

  if (needsCode) {
    return (
      <AuthLayout heading="Check your email" lede={<>We sent a six-digit code to <strong>{email}</strong>.</>}>
        <ConfirmCodeForm email={email} password={password} onDone={() => navigate("/", { replace: true })} />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      heading="Create your account"
      lede="Upload your resume once, then every job on the board is scored against it."
      social
      footer={
        <>
          Already have an account? <Link className="auth-link" to="/signin">Log in</Link>
        </>
      }
    >
      <form className="auth-form" onSubmit={submit}>
        {error && <div className="auth-error">{error}</div>}
        <Field
          label="Email"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
          autoFocus
        />
        <Field
          label="Password"
          type={reveal ? "text" : "password"}
          autoComplete="new-password"
          placeholder="Create a password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
          reveal={reveal}
          onReveal={() => setReveal(!reveal)}
          hint={problem ? <span className="auth-hint-warn">{problem}</span> : PASSWORD_RULES}
        />
        <button className="auth-submit" type="submit" disabled={busy || !email || problem !== null || !password}>
          {busy ? "Creating your account…" : "Create account"}
        </button>
      </form>
    </AuthLayout>
  );
}
