import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { asAuthError, confirmPasswordReset, passwordProblem, PASSWORD_RULES, requestPasswordReset, signInWithPassword } from "../auth";
import { AuthLayout, Field } from "./AuthLayout";

/** /forgot — ask for the emailed reset code, then set the new password with it.
 *  Cognito's ForgotPassword and ConfirmForgotPassword are two calls, but to the
 *  user it is one errand, so it is one page with two states. */
export function Forgot() {
  const navigate = useNavigate();
  const [sent, setSent] = useState(false);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const problem = password ? passwordProblem(password) : null;

  async function send(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await requestPasswordReset(email);
      setSent(true);
    } catch (err) {
      setError(asAuthError(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function reset(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await confirmPasswordReset(email, code, password);
      // The new password is right here, so finish the job: sign them in rather
      // than sending them back to /signin to type it a second time.
      await signInWithPassword(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(asAuthError(err).message);
    } finally {
      setBusy(false);
    }
  }

  const footer = (
    <>
      Remembered it? <Link className="auth-link" to="/signin">Log in</Link>
    </>
  );

  if (!sent) {
    return (
      <AuthLayout heading="Reset your password" lede="We will email you a code to prove the address is yours." footer={footer}>
        <form className="auth-form" onSubmit={send}>
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
          <button className="auth-submit" type="submit" disabled={busy || !email}>
            {busy ? "Sending the code…" : "Email me a code"}
          </button>
        </form>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout heading="Set a new password" lede={<>Enter the code we sent to <strong>{email}</strong> and pick a new password.</>} footer={footer}>
      <form className="auth-form" onSubmit={reset}>
        {error && <div className="auth-error">{error}</div>}
        <Field
          label="Reset code"
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          required
          autoFocus
        />
        <Field
          label="New password"
          type={reveal ? "text" : "password"}
          autoComplete="new-password"
          placeholder="Your new password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
          reveal={reveal}
          onReveal={() => setReveal(!reveal)}
          hint={problem ? <span className="auth-hint-warn">{problem}</span> : PASSWORD_RULES}
        />
        <button className="auth-submit" type="submit" disabled={busy || !code || problem !== null || !password}>
          {busy ? "Saving…" : "Set new password"}
        </button>
        <p className="auth-links">
          <button className="auth-link" type="button" onClick={() => setSent(false)}>
            Use a different email
          </button>
        </p>
      </form>
    </AuthLayout>
  );
}
