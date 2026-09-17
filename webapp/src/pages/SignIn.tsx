import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { asAuthError, resendConfirmationCode, signInWithPassword } from "../auth";
import { AuthLayout, Field } from "./AuthLayout";
import { ConfirmCodeForm } from "./ConfirmCode";

/** /signin — our own page, not the Cognito hosted UI. The password goes to the
 *  user pool over SRP from here. */
export function SignIn() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signInWithPassword(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      const failure = asAuthError(err);
      if (failure.code === "UserNotConfirmedException") {
        // The account exists but was never confirmed. Get a fresh code on its
        // way before showing the form: the first one may be weeks old.
        await resendConfirmationCode(email).catch(() => undefined);
        setUnconfirmed(true);
      } else {
        setError(failure.message);
      }
    } finally {
      setBusy(false);
    }
  }

  if (unconfirmed) {
    return (
      <AuthLayout heading="Confirm your email" lede={<>We emailed a code to <strong>{email}</strong>. Enter it to finish signing in.</>}>
        <ConfirmCodeForm email={email} password={password} onDone={() => navigate("/", { replace: true })} />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      heading="Welcome to JoblyGo"
      lede="AI engineer jobs, screened and ranked against your resume."
      social
      footer={
        <>
          Don’t have an account? <Link className="auth-link" to="/signup">Create account</Link>
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
          autoComplete="current-password"
          placeholder="Your password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
          reveal={reveal}
          onReveal={() => setReveal(!reveal)}
        />
        <button className="auth-submit" type="submit" disabled={busy || !email || !password}>
          {busy ? "Signing you in…" : "Log In"}
        </button>
        <p className="auth-links">
          <Link className="auth-link" to="/forgot">
            Forgot password?
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}
