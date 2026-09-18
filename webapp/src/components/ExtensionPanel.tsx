import { useState } from "react";

import { createCollectorToken, type CreatedToken } from "../api";

const API_BASE = import.meta.env.VITE_API_BASE;

/** Admins only: connect the Chrome extension to the cloud. Creates a
 *  collector token (captures only: it cannot change plans or mint tokens) and
 *  shows it once. */
export function ExtensionPanel() {
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      setCreated(await createCollectorToken(`extension ${new Date().toISOString().slice(0, 10)}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function copy(value: string, what: string) {
    await navigator.clipboard.writeText(value);
    setCopied(what);
    window.setTimeout(() => setCopied(null), 1500);
  }

  return (
    <div className="extension-panel">
      <h2>Connect the Chrome extension</h2>
      <p className="page-sub">
        The extension saves every capture to your local app, and a copy to the cloud when it has these two values.
      </p>

      <ol className="extension-steps">
        <li>
          In <code>chrome://extensions</code>, open the JoblyGo extension's <b>Details → Extension options</b>.
        </li>
        <li>
          Cloud API URL:
          <div className="copy-row">
            <code>{API_BASE}</code>
            <button className="btn btn-ghost" onClick={() => copy(API_BASE, "url")}>
              {copied === "url" ? "Copied" : "Copy"}
            </button>
          </div>
        </li>
        <li>
          Collector token:
          {created ? (
            <>
              <div className="copy-row">
                <code className="token">{created.token}</code>
                <button className="btn btn-primary" onClick={() => copy(created.token, "token")}>
                  {copied === "token" ? "Copied" : "Copy"}
                </button>
              </div>
              <p className="action-note left">Shown once. It can only send captures; create a new one if it's lost.</p>
            </>
          ) : (
            <div className="copy-row">
              <button className="btn btn-primary" onClick={create} disabled={busy}>
                {busy ? "Creating…" : "Create a collector token"}
              </button>
            </div>
          )}
        </li>
        <li>
          Click <b>Save</b> and allow access to <code>{new URL(API_BASE).host}</code> when Chrome asks.
        </li>
      </ol>
      {error && <div className="banner error">{error}</div>}
    </div>
  );
}
