import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { completeSignIn } from "../auth";

/** Cognito sends the browser back here with ?code= (or ?error=). */
export function Callback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(params.get("error_description") ?? params.get("error"));

  useEffect(() => {
    const code = params.get("code");
    if (!code) return;
    completeSignIn(code)
      .then(() => navigate("/", { replace: true }))
      .catch((err: Error) => setError(err.message));
  }, [params, navigate]);

  return error ? (
    <div className="panel error">
      <h2>Sign-in failed</h2>
      <p>{error}</p>
      <button onClick={() => navigate("/")}>Back</button>
    </div>
  ) : (
    <p className="muted">Signing you in…</p>
  );
}
