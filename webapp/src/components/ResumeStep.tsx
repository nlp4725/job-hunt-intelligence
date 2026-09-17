import { useState } from "react";

import { uploadResume, type Resume } from "../api";

const ACCEPT = ".pdf,.docx,.txt,.md";

export function ResumeStep({ onUploaded }: { onUploaded: (resume: Resume) => void | Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
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
      <h2 className="step-title">Upload your resume</h2>
      <p className="lede">
        We'll find your skills automatically. PDF, Word or plain text, max 5 MB. Stored encrypted, only you can read
        it, and you can delete it any time.
      </p>
      {error && <div className="banner error">{error}</div>}
      <label
        className={`dropzone${busy ? " busy" : ""}${dragging ? " dragging" : ""}`}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); handle(event.dataTransfer.files[0]); }}
      >
        <input type="file" accept={ACCEPT} disabled={busy} onChange={(event) => handle(event.target.files?.[0])} />
        <span className="dropzone-icon">{busy ? "⏳" : "📄"}</span>
        <span className="dropzone-main">{busy ? "Reading your resume…" : "Drop your resume here"}</span>
        <span className="dropzone-note">or click to browse · PDF, DOCX, TXT, MD</span>
      </label>
    </>
  );
}
