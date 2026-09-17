import { Board } from "./Board";
import { Landing } from "./Landing";
import { Onboarding as OnboardingWizard } from "./Onboarding";
import { useMe } from "../useMe";

/** Which screen a visitor gets: the landing page when signed out, the
 *  onboarding steps until they are finished, then the board. */
export function Home() {
  const { me, error, loading } = useMe();

  if (loading) {
    return (
      <div className="status-message">
        <span className="pulse">Loading…</span>
      </div>
    );
  }

  if (!me) {
    return (
      <>
        {error && <div className="banner error">{error}</div>}
        <Landing />
      </>
    );
  }

  if (!me.onboarding.complete) {
    return <OnboardingWizard me={me} onDone={() => window.location.assign("/")} />;
  }

  return <Board me={me} />;
}
