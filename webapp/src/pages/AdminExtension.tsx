import { Link } from "react-router-dom";

import { ExtensionPanel } from "../components/ExtensionPanel";
import { useMe } from "../useMe";

/** Connecting the extension is the collector's own errand, not part of anyone's
 *  board, so it lives on its own address instead of in the sidebar. Anyone who
 *  is not an admin gets the same "nothing here" as a bad URL — the panel is
 *  never a hint that collection is something users could do. */
export function AdminExtension() {
  const { me, loading } = useMe();

  if (loading) {
    return (
      <div className="status-message">
        <span className="pulse">Loading…</span>
      </div>
    );
  }

  if (!me || me.role !== "admin") {
    return (
      <div className="status-message">
        <p>Nothing here.</p>
        <Link className="btn btn-ghost" to="/">
          Back to your board
        </Link>
      </div>
    );
  }

  return (
    <div className="main">
      <ExtensionPanel />
      <Link className="btn btn-ghost" to="/">
        Back to the board
      </Link>
    </div>
  );
}
