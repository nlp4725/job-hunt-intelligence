import { useEffect, useState } from "react";

import { confirmSkills, listResumes, type Resume } from "../api";

/** The skills found in the resume. Remove anything you don't want matched on,
 *  and add what we missed; the API only accepts names from its skill list. */
export function SkillsStep({ resume, onConfirmed }: { resume: Resume | null; onConfirmed: () => void }) {
  const [current, setCurrent] = useState<Resume | null>(resume);
  const [skills, setSkills] = useState<string[]>(resume?.skills_extracted ?? []);
  const [added, setAdded] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (current) return;
    listResumes()
      .then(({ versions }) => {
        const latest = versions.at(-1) ?? null;
        setCurrent(latest);
        setSkills(latest?.skills_confirmed ?? latest?.skills_extracted ?? []);
      })
      .catch((err: Error) => setError(err.message));
  }, [current]);

  function add() {
    const name = added.trim();
    if (name && !skills.includes(name)) setSkills([...skills, name]);
    setAdded("");
  }

  async function save() {
    if (!current) return;
    setBusy(true);
    setError(null);
    try {
      await confirmSkills(current.id, skills);
      onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Confirm your skills</h1>
      <p className="lede">
        {skills.length} skill{skills.length === 1 ? "" : "s"} found in {current?.filename ?? "your resume"}. Every job is
        matched against this list, so remove anything you'd rather not be matched on.
      </p>
      {error && <div className="banner error">{error}</div>}
      <div className="chips">
        {skills.map((skill) => (
          <button key={skill} className="chip" onClick={() => setSkills(skills.filter((s) => s !== skill))} title="Remove">
            {skill} <span className="chip-x">×</span>
          </button>
        ))}
        {skills.length === 0 && <p className="lede">No skills yet — add the ones that matter most.</p>}
      </div>
      <div className="row-actions">
        <input
          type="text"
          className="full"
          placeholder="Add a skill, e.g. PyTorch"
          value={added}
          onChange={(event) => setAdded(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && add()}
        />
        <button className="btn btn-ghost" onClick={add} disabled={!added.trim()}>
          Add
        </button>
      </div>
      <div className="row-actions">
        <button className="btn btn-primary grow" onClick={save} disabled={busy || skills.length === 0 || !current}>
          {busy ? "Saving…" : "Confirm skills"}
        </button>
      </div>
    </>
  );
}
