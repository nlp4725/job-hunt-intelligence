import { useState } from "react";

import { uploadResume, type Resume } from "../api";

const ACCEPT = ".pdf,.docx,.txt,.md";

export function ResumeStep({ onUploaded }: { onUploaded: (resume: Resume) => void | Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handle(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      onUploaded(await uploadResume(file));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Upload your resume</h1>
      <p className="lede">
        PDF, Word, or plain text, up to 5 MB. We read the text to find your skills, then store it encrypted; nobody
        else can see it, and you can delete it any time.
      </p>
      {error && <div className="banner error">{error}</div>}
      <label className={`dropzone${busy ? " busy" : ""}`}>
        <input type="file" accept={ACCEPT} disabled={busy} onChange={(event) => handle(event.target.files?.[0])} />
        <span className="dropzone-main">{busy ? "Reading your resume…" : "Choose a file"}</span>
        <span className="dropzone-note">PDF · DOCX · TXT · MD</span>
      </label>
    </>
  );
}
