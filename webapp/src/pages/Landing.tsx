import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { DemoAnimation } from "../components/DemoAnimation";
import { getPublicStats, type PublicStats } from "../api";

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
  const navigate = useNavigate();
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
          <Link className="hero-brand" to="/">
            <span className="hero-mark">J</span>
            <span className="hero-name">JoblyGo</span>
          </Link>
          <div className="hero-nav-actions">
            <Link className="hero-link" to="/jobs">
              Recent postings
            </Link>
            <button className="hero-link" onClick={() => navigate("/signin")}>
              Sign in
            </button>
            <button className="hero-cta" onClick={() => navigate("/signup")}>
              Upload your resume
            </button>
          </div>
        </div>
      </nav>

      <header className="hero">
        <h1 className="hero-headline">Less browsing. More applying.</h1>
        <p className="hero-sub">
          AI engineer jobs scraped, ranked and tracked for you daily. Scored against your resume across three signals.
        </p>
        <div className="hero-actions">
          <button className="hero-cta large" onClick={() => navigate("/signup")}>
            Upload your resume →
          </button>
          <Link className="hero-secondary" to="/jobs">
            Browse recent postings →
          </Link>
        </div>
      </header>

      <section className="hero-demo">
        <DemoAnimation />
        <p className="demo-caption">
          Demo only: example listings and scores, to show how ranking works. <Link to="/jobs">See the real postings →</Link>
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
