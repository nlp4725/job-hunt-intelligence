import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { confirmSkills, listResumes, listSkillVocabulary, type Resume } from "../api";

const MAX_SUGGESTIONS = 8;

/** Rank the vocabulary against what's been typed: names that start with it
 *  first, then names that merely contain it, alphabetical within each group.
 *  Typing "sql" should offer SQL before PostgreSQL, not the other way round. */
export function suggest(vocabulary: string[], typed: string, chosen: string[]): string[] {
  const needle = typed.trim().toLowerCase();
  if (!needle) return [];
  const taken = new Set(chosen.map((skill) => skill.toLowerCase()));
  const starts: string[] = [];
  const contains: string[] = [];
  for (const name of vocabulary) {
    if (taken.has(name.toLowerCase())) continue;
    const at = name.toLowerCase().indexOf(needle);
    if (at === 0) starts.push(name);
    else if (at > 0) contains.push(name);
  }
  return [...starts, ...contains].slice(0, MAX_SUGGESTIONS);
}

/** The skills found in the resume. Remove anything you don't want matched on,
 *  and add what we missed. The API only accepts names from its skill list, so
 *  the box suggests from that list as you type instead of letting you spell
 *  something it would reject. */
export function SkillsStep({ resume, onConfirmed }: { resume: Resume | null; onConfirmed: () => void }) {
  const [current, setCurrent] = useState<Resume | null>(resume);
  const [skills, setSkills] = useState<string[]>(resume?.skills_extracted ?? []);
  const [added, setAdded] = useState("");
  const [vocabulary, setVocabulary] = useState<string[]>([]);
  const [highlighted, setHighlighted] = useState(0);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

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

  // A failure here costs the suggestions, not the step: you can still remove
  // skills and continue, so it is deliberately not shown as an error.
  useEffect(() => {
    listSkillVocabulary()
      .then(({ skills: names }) => setVocabulary(names))
      .catch(() => setVocabulary([]));
  }, []);

  const suggestions = useMemo(() => suggest(vocabulary, added, skills), [vocabulary, added, skills]);
  const exact = vocabulary.find((name) => name.toLowerCase() === added.trim().toLowerCase());

  function add(name: string | undefined) {
    if (!name || skills.includes(name)) return;
    setSkills([...skills, name]);
    setAdded("");
    setHighlighted(0);
    setOpen(false);
    inputRef.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (suggestions.length === 0) return;
      const step = event.key === "ArrowDown" ? 1 : suggestions.length - 1;
      setHighlighted((at) => (at + step) % suggestions.length);
      setOpen(true);
    } else if (event.key === "Enter") {
      // Enter takes the highlighted suggestion, or an exactly typed name when
      // the list is closed; it never invents a name the API would reject.
      add(open && suggestions.length > 0 ? suggestions[highlighted] : exact);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
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
      <h2 className="step-title">Review your skills</h2>
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
        <div className="typeahead">
          <input
            ref={inputRef}
            type="text"
            className="full"
            placeholder="Add a skill, e.g. PyTorch"
            value={added}
            role="combobox"
            aria-expanded={open && suggestions.length > 0}
            aria-autocomplete="list"
            aria-controls="skill-suggestions"
            autoComplete="off"
            onChange={(event) => {
              setAdded(event.target.value);
              setHighlighted(0);
              setOpen(true);
            }}
            onKeyDown={onKeyDown}
            // A click on a suggestion blurs the input first, so closing waits a
            // tick for that click to land.
            onBlur={() => setTimeout(() => setOpen(false), 120)}
            onFocus={() => setOpen(true)}
          />
          {open && suggestions.length > 0 && (
            <ul className="typeahead-list" id="skill-suggestions" role="listbox">
              {suggestions.map((name, index) => (
                <li key={name} role="option" aria-selected={index === highlighted}>
                  <button
                    className={`typeahead-option${index === highlighted ? " highlighted" : ""}`}
                    onMouseEnter={() => setHighlighted(index)}
                    onClick={() => add(name)}
                  >
                    {name}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {added.trim() && suggestions.length === 0 && !exact && (
            <p className="action-note left">No skill by that name — we can only match names we know.</p>
          )}
        </div>
        <button className="btn btn-ghost" onClick={() => add(exact ?? suggestions[0])} disabled={!exact && suggestions.length === 0}>
          Add
        </button>
      </div>
      <div className="row-actions">
        <button className="btn btn-primary grow" onClick={save} disabled={busy || skills.length === 0 || !current}>
          {busy ? "Saving…" : "Continue →"}
        </button>
      </div>
    </>
  );
}
