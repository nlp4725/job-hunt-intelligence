import { useEffect, useState } from "react";

import { DemoAnimation } from "../components/DemoAnimation";
import { getPublicStats, type PublicStats } from "../api";
import { signIn } from "../auth";

/** The signed-out landing page, ported from the Figma Make file "Job Hunting
 *  Board Productization" (HeroPage): grid-paper canvas, one-line headline,
 *  dark CTA, the scoring demo, and the stats ticker. */
function agoLabel(iso: string | null): string {
  if (!iso) return "—";
  const hours = (Date.now() - new Date(iso + (iso.endsWith("Z") ? "" : "Z")).getTime()) / 3_600_000;
  if (hours < 1) return "under an hour ago";
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function tickerItems(stats: PublicStats | null) {
  if (!stats) return [{ label: "Loading", value: "…" }];
  const number = (value: number) => value.toLocaleString();
  return [
    { label: "Collected today", value: number(stats.collected_today), accent: stats.collected_today > 0 },
    { label: "Jobs on the board", value: number(stats.jobs) },
    { label: "Seniority classified", value: number(stats.jobs_with_level) },
    { label: "Remote roles", value: number(stats.remote_jobs) },
    { label: "Companies", value: number(stats.companies) },
    { label: "Last collected", value: agoLabel(stats.last_collected_at) },
  ];
}

export function Landing() {
  const [stats, setStats] = useState<PublicStats | null>(null);
  useEffect(() => {
    getPublicStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);
  const items = tickerItems(stats);

  return (
    <div className="hero-page">
      <nav className="hero-nav">
        <div className="hero-nav-inner">
          <div className="hero-brand">
            <span className="hero-mark">J</span>
            <span className="hero-name">JoblyGo</span>
          </div>
          <div className="hero-nav-actions">
            <button className="hero-link" onClick={() => signIn()}>
              Sign in
            </button>
            <button className="hero-cta" onClick={() => signIn("signup")}>
              Upload your resume
            </button>
          </div>
        </div>
      </nav>

      <header className="hero">
        <h1 className="hero-headline">Less browsing. More applying.</h1>
        <p className="hero-sub">
          {stats ? `${stats.jobs.toLocaleString()} AI engineer jobs, screened and ranked for you.` : "AI engineer jobs, screened and ranked for you."}{" "}
          Scored against your resume, updated daily.
        </p>
        <button className="hero-cta large" onClick={() => signIn("signup")}>
          Upload your resume →
        </button>
      </header>

      <section className="hero-demo">
        <DemoAnimation />
        <p className="demo-caption">
          Real postings from the last two weeks. Scores unlock when you upload your resume — every score is computed
          against it.
        </p>
      </section>

      <section className="hero-ticker-wrap">
        <div className="hero-ticker">
          <div className="hero-ticker-track">
            {[0, 1].map((copy) => (
              <span className="hero-ticker-copy" key={copy}>
                {items.map((item) => (
                  <span className="hero-ticker-item" key={`${copy}-${item.label}`}>
                    <span className="mono hero-ticker-label">{item.label}</span>
                    <span className={`mono hero-ticker-value${"accent" in item && item.accent ? " accent" : ""}`}>{item.value}</span>
                  </span>
                ))}
              </span>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
