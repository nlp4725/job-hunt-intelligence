import { type ReactNode } from "react";
import { Link } from "react-router-dom";

import { socialProviders, signInWithProvider, type SocialProvider } from "../auth";
import "../auth.css";

/** The frame every auth page shares: our own white full-bleed page with the
 *  logo at the top, a big heading, the social block, and then whatever form the
 *  page brought. Keeping it here means /signin, /signup and /forgot cannot
 *  drift apart, which is how the hosted UI ended up feeling like somebody
 *  else's product. */
export function AuthLayout({
  heading,
  lede,
  social = false,
  children,
  footer,
}: {
  heading: string;
  lede?: ReactNode;
  /** Sign-in and sign-up offer the providers; the reset flow does not, because
   *  a federated account has no password here to reset. */
  social?: boolean;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const providers = social ? socialProviders() : [];

  return (
    <div className="auth-page">
      <div className="auth-shell">
        <Link className="auth-brand" to="/" aria-label="JoblyGo home">
          <span className="auth-mark">J</span>
          <span className="auth-name">JoblyGo</span>
        </Link>

        <h1 className="auth-heading">{heading}</h1>
        {lede && <p className="auth-lede">{lede}</p>}

        {providers.length > 0 && (
          <>
            <div className="auth-social">
              {providers.map((provider) => (
                <ProviderButton key={provider.id} provider={provider} />
              ))}
            </div>
            <div className="auth-divider">
              <span>or</span>
            </div>
          </>
        )}

        {children}

        {footer && <p className="auth-footer">{footer}</p>}
      </div>
    </div>
  );
}

function ProviderButton({ provider }: { provider: SocialProvider }) {
  return (
    <button className="auth-social-btn" type="button" onClick={() => void signInWithProvider(provider)}>
      <span className={`auth-social-mark ${provider.id}`} aria-hidden="true" />
      {provider.label}
    </button>
  );
}

/** One labelled field. `reveal` turns it into a password box with the eye. */
export function Field({
  label,
  hint,
  reveal,
  onReveal,
  ...input
}: {
  label: string;
  hint?: ReactNode;
  reveal?: boolean;
  onReveal?: () => void;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = `auth-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <div className="auth-field">
      <label className="auth-label" htmlFor={id}>
        {label}
      </label>
      <div className="auth-input-wrap">
        <input id={id} className="auth-input" {...input} />
        {onReveal && (
          <button
            className="auth-eye"
            type="button"
            onClick={onReveal}
            aria-label={reveal ? "Hide password" : "Show password"}
            aria-pressed={reveal}
            title={reveal ? "Hide password" : "Show password"}
          >
            {reveal ? <EyeOff /> : <Eye />}
          </button>
        )}
      </div>
      {hint && <span className="auth-hint">{hint}</span>}
    </div>
  );
}

/* Inline so the pages pull in no icon dependency and the stroke follows
   currentColor with the rest of the form. */
function Eye() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z" />
      <circle cx="12" cy="12" r="2.6" />
    </svg>
  );
}

function EyeOff() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M3 3l18 18" />
      <path d="M10.6 6.1A9.9 9.9 0 0 1 12 6c6.4 0 10 6 10 6a17 17 0 0 1-2.8 3.5M6.6 7.6A16.6 16.6 0 0 0 2 12s3.6 6 10 6a9.6 9.6 0 0 0 3.6-.67" />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
    </svg>
  );
}
