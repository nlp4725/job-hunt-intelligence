import { useEffect, useState } from "react";

/** The landing page's scoring demo, ported from the Figma Make file: rows are
 *  scanned one at a time and their scores revealed, then it loops. */
type DemoJob = {
  title: string; company: string; industry: string; size: string;
  workplace: string; posted: string; skill: number; seniority: number;
  expertise: number; total: number; applied: boolean;
};

const DEMO_JOBS: DemoJob[] = [
  { title: "AI Engineer", company: "Airbnb", industry: "Travel Tech", size: "1,001–5,000", workplace: "Remote", posted: "Sep 6", skill: 5, seniority: 5, expertise: 4, total: 14, applied: true },
  { title: "ML Engineer", company: "Stripe", industry: "FinTech", size: "5,001–10,000", workplace: "Hybrid", posted: "Sep 7", skill: 4, seniority: 4, expertise: 4, total: 12, applied: false },
  { title: "AI Engineer", company: "ElevenLabs", industry: "Research", size: "51–200", workplace: "Remote", posted: "Sep 6", skill: 5, seniority: 5, expertise: 4, total: 14, applied: true },
  { title: "Applied Scientist", company: "Amazon", industry: "E-commerce", size: "10,001+", workplace: "On-site", posted: "Sep 9", skill: 4, seniority: 3, expertise: 4, total: 11, applied: false },
  { title: "Forward Deployed Engineer", company: "Palantir", industry: "Gov. Services", size: "1,001–5,000", workplace: "On-site", posted: "Sep 8", skill: 4, seniority: 5, expertise: 4, total: 13, applied: false },
  { title: "AI Engineer", company: "Vercel", industry: "Developer Tools", size: "201–500", workplace: "Remote", posted: "Sep 10", skill: 5, seniority: 5, expertise: 4, total: 14, applied: false },
];

const COLUMNS = ["#", "Score", "Skill", "Senr.", "Exp.", "Job", "Industry", "Size", "Workplace", "Posted", "Applied", "Status"];

const scoreColor = (s: number) => (s >= 14 ? "#16a34a" : s >= 13 ? "#65a30d" : s >= 11 ? "#d97706" : "#dc2626");
const signalColor = (v: number) => (v >= 5 ? "#16a34a" : v >= 4 ? "#65a30d" : v >= 3 ? "#d97706" : "#dc2626");
const WORKPLACE: Record<string, string> = { Remote: "#7c3aed", Hybrid: "#2563eb", "On-site": "#059669" };

const Dash = () => <span className="demo-dash">—</span>;
const Dots = () => (
  <span className="demo-dots">
    {[0, 1, 2].map((d) => (
      <span key={d} style={{ animationDelay: `${d * 0.16}s` }} />
    ))}
  </span>
);

export function DemoAnimation() {
  const [revealed, setRevealed] = useState<boolean[]>(DEMO_JOBS.map(() => false));
  const [scanning, setScanning] = useState(-1);

  useEffect(() => {
    let index = 0;
    const timers: number[] = [];
    const tick = () => {
      if (index >= DEMO_JOBS.length) {
        timers.push(
          window.setTimeout(() => {
            setRevealed(DEMO_JOBS.map(() => false));
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
  }, []);

  return (
    <div className="demo">
      <div className="demo-chrome">
        <span className="dot red" />
        <span className="dot amber" />
        <span className="dot green" />
        <span className="demo-live">
          <span className="live-dot pulse" /> Live
        </span>
      </div>
      <div className="demo-scroll">
        <table className="demo-table">
          <colgroup>
            {[32, 60, 44, 44, 44, 200, 110, 80, 80, 62, 55, 65].map((width, i) => (
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
            {DEMO_JOBS.map((job, i) => {
              const isScanning = scanning === i && !revealed[i];
              const done = revealed[i];
              const cell = (value: React.ReactNode) => (done ? value : isScanning ? <Dots /> : <Dash />);
              return (
                <tr key={job.company} className={isScanning ? "scanning" : ""}>
                  <td className="mono demo-index">{i + 1}</td>
                  <td>
                    {done ? (
                      <span className="mono demo-total" style={{ "--c": scoreColor(job.total) } as React.CSSProperties}>
                        {job.total}/15
                      </span>
                    ) : isScanning ? (
                      <Dots />
                    ) : (
                      <Dash />
                    )}
                  </td>
                  {(["skill", "seniority", "expertise"] as const).map((key) => (
                    <td key={key} className="mono demo-signal">
                      {cell(<span style={{ color: signalColor(job[key]) }}>{job[key]}</span>)}
                    </td>
                  ))}
                  <td className="demo-job">
                    <div className="demo-title">{job.title}</div>
                    <div className="demo-company">{job.company}</div>
                  </td>
                  <td className="demo-industry">{cell(job.industry)}</td>
                  <td className="mono demo-muted">{cell(job.size)}</td>
                  <td>
                    {cell(
                      <span
                        className="demo-pill"
                        style={{ "--c": WORKPLACE[job.workplace] ?? "#7a6248" } as React.CSSProperties}
                      >
                        {job.workplace}
                      </span>,
                    )}
                  </td>
                  <td className="mono demo-muted">{cell(job.posted)}</td>
                  <td className="center">{cell(<span className={`demo-check${job.applied ? " on" : ""}`} />)}</td>
                  <td>
                    {cell(
                      job.applied ? (
                        <span className="demo-pill" style={{ "--c": "#2563eb" } as React.CSSProperties}>
                          Applied
                        </span>
                      ) : (
                        <span className="demo-pill neutral">New</span>
                      ),
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
