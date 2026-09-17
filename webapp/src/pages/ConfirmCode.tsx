import { useState } from "react";

import { asAuthError, confirmSignUp, resendConfirmationCode, signInWithPassword } from "../auth";
import { Field } from "./AuthLayout";

/** The emailed six-digit code. Both /signup and /signin land here — signing in
 *  to an account that was never confirmed is the same dead end as signing up
 *  and closing the tab, and in both cases the way out is this form, not an
 *  error message. When we still hold the password (straight after sign-up) we
 *  sign the user in ourselves, so confirming is the last thing they do. */
export function ConfirmCodeForm({ email, password, onDone }: { email: string; password?: string; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await confirmSignUp(email, code);
      if (password) await signInWithPassword(email, password);
      onDone();
    } catch (err) {
      setError(asAuthError(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setError(null);
    try {
      await resendConfirmationCode(email);
      setNote(`A new code is on its way to ${email}.`);
    } catch (err) {
      setError(asAuthError(err).message);
    }
  }

  return (
    <form className="auth-form" onSubmit={submit}>
      {error && <div className="auth-error">{error}</div>}
      {note && <div className="auth-note">{note}</div>}
      <Field
        label="Confirmation code"
        // The code is digits, so ask phones for the number pad, and let the
        // browser fill it from the email where it can.
        inputMode="numeric"
        autoComplete="one-time-code"
        placeholder="123456"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        required
        autoFocus
      />
      <button className="auth-submit" type="submit" disabled={busy || code.trim().length === 0}>
        {busy ? "Confirming…" : "Confirm my email"}
      </button>
      <p className="auth-links">
        <button className="auth-link" type="button" onClick={() => void resend()}>
          Send the code again
        </button>
      </p>
    </form>
  );
}
