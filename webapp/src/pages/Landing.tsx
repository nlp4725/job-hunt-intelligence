import { DemoAnimation } from "../components/DemoAnimation";
import { signIn } from "../auth";

/** The signed-out landing page, ported from the Figma Make file "Job Hunting
 *  Board Productization" (HeroPage): grid-paper canvas, one-line headline,
 *  dark CTA, the scoring demo, and the stats ticker. */
const TICKER = [
  { label: "Scraped today", value: "2,717", accent: true },
  { label: "Scraped at", value: "2:00 PM" },
  { label: "Total scraped", value: "15,782" },
  { label: "Roles scored", value: "15,782" },
  { label: "With seniority", value: "11,189" },
  { label: "Avg. score", value: "8.4 / 15" },
  { label: "Top match", value: "14 / 15" },
  { label: "Remote roles", value: "1,104" },
];

export function Landing() {
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
          AI engineer jobs scraped, ranked and tracked for you daily. Scored against your resume across three signals.
        </p>
        <button className="hero-cta large" onClick={() => signIn("signup")}>
          Upload your resume →
        </button>
      </header>

      <section className="hero-demo">
        <DemoAnimation />
      </section>

      <section className="hero-ticker-wrap">
        <div className="hero-ticker">
          <div className="hero-ticker-track">
            {[0, 1].map((copy) => (
              <span className="hero-ticker-copy" key={copy}>
                {TICKER.map((item) => (
                  <span className="hero-ticker-item" key={`${copy}-${item.label}`}>
                    <span className="mono hero-ticker-label">{item.label}</span>
                    <span className={`mono hero-ticker-value${item.accent ? " accent" : ""}`}>{item.value}</span>
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
