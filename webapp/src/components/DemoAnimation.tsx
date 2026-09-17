import { useEffect, useState } from "react";

import { getRecentJobs, type PublicJob, type SeniorityLevel } from "../api";

/** The landing page's demo, ported from the Figma Make file but fed by the
 *  real board: the newest postings of the last two weeks are scanned one at a
 *  time and their details revealed. Scores stay locked, because a score only
 *  exists against someone's resume. */
const LEVELS: Record<SeniorityLevel, string> = {
  intern: "Intern",
  entry: "Entry",
  mid_senior: "Mid–senior",
  senior: "Senior",
  staff_principal: "Staff",
};

const COLUMNS = ["#", "Score", "Skill", "Senr.", "Job", "Industry", "Size", "Workplace", "Level", "Posted"];
const WORKPLACE: Record<string, string> = { Remote: "#7c3aed", Hybrid: "#2563eb", "On-site": "#059669" };

const Dash = () => <span className="demo-dash">—</span>;
const Lock = () => <span className="demo-lock" title="Scores are computed against your own resume">🔒</span>;
const Dots = () => (
  <span className="demo-dots">
    {[0, 1, 2].map((d) => (
      <span key={d} style={{ animationDelay: `${d * 0.16}s` }} />
    ))}
  </span>
);

export function DemoAnimation() {
  const [jobs, setJobs] = useState<PublicJob[] | null>(null);
  const [revealed, setRevealed] = useState<boolean[]>([]);
  const [scanning, setScanning] = useState(-1);

  useEffect(() => {
    getRecentJobs()
      .then((recent) => {
        setJobs(recent);
        setRevealed(recent.map(() => false));
      })
      .catch(() => setJobs([]));
  }, []);

  useEffect(() => {
    if (!jobs || jobs.length === 0) return;
    let index = 0;
    const timers: number[] = [];
    const tick = () => {
      if (index >= jobs.length) {
        timers.push(
          window.setTimeout(() => {
            setRevealed(jobs.map(() => false));
            setScanning(-1);
            index = 0;
            timers.push(window.setTimeout(tick, 600));
          }, 2600),
        );
        return;
      }
      setScanning(index);
      timers.push(
        window.setTimeout(() => {
          const current = index;
          setRevealed((prev) => prev.map((value, i) => (i === current ? true : value)));
          index += 1;
          timers.push(window.setTimeout(tick, 420));
        }, 540),
      );
    };
    timers.push(window.setTimeout(tick, 700));
    return () => timers.forEach(window.clearTimeout);
  }, [jobs]);

  return (
    <div className="demo">
      <div className="demo-chrome">
        <span className="dot red" />
        <span className="dot amber" />
        <span className="dot green" />
        <span className="demo-live">
          <span className="live-dot pulse" /> Live · collected in the last 2 weeks
        </span>
      </div>
      <div className="demo-scroll">
        <table className="demo-table">
          <colgroup>
            {[32, 56, 44, 44, 230, 130, 90, 84, 84, 66].map((width, i) => (
              <col key={i} style={{ width }} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {COLUMNS.map((label) => (
                <th key={label}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(jobs ?? Array.from({ length: 6 }, () => null)).map((job, i) => {
              const isScanning = scanning === i && !revealed[i];
              const done = revealed[i];
              const cell = (value: React.ReactNode) => (done ? value : isScanning ? <Dots /> : <Dash />);
              return (
                <tr key={i} className={isScanning ? "scanning" : ""}>
                  <td className="mono demo-index">{i + 1}</td>
                  <td>{done ? <Lock /> : isScanning ? <Dots /> : <Dash />}</td>
                  <td>{done ? <Lock /> : isScanning ? <Dots /> : <Dash />}</td>
                  <td>{done ? <Lock /> : isScanning ? <Dots /> : <Dash />}</td>
                  <td className="demo-job">
                    <div className="demo-title">{job?.title ?? "…"}</div>
                    <div className="demo-company">{job?.company ?? ""}</div>
                  </td>
                  <td className="demo-industry">{cell(job?.industry ?? "—")}</td>
                  <td className="mono demo-muted">{cell(job?.size ?? "—")}</td>
                  <td>
                    {cell(
                      job?.workplace_type ? (
                        <span className="demo-pill" style={{ "--c": WORKPLACE[job.workplace_type] ?? "#7a6248" } as React.CSSProperties}>
                          {job.workplace_type}
                        </span>
                      ) : (
                        <span className="demo-pill neutral">Unknown</span>
                      ),
                    )}
                  </td>
                  <td className="demo-industry">{cell(job?.level ? LEVELS[job.level] : "—")}</td>
                  <td className="mono demo-muted">{cell(job?.posted_date ?? "—")}</td>
                </tr>
              );
            })}
            {jobs?.length === 0 && (
              <tr>
                <td className="empty" colSpan={COLUMNS.length}>
                  No new postings in the last two weeks.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
