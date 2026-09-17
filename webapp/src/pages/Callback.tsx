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

  if (!error) {
    return (
      <div className="status-message">
        <span className="pulse">Signing you in…</span>
      </div>
    );
  }
  return (
    <div className="centered">
      <div className="card">
        <h1>Sign-in failed</h1>
        <p className="lede">{error}</p>
        <button className="btn btn-primary" onClick={() => navigate("/")}>
          Back
        </button>
      </div>
    </div>
  );
}
