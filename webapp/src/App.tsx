import { Outlet } from "react-router-dom";

import { Brand } from "./components/Brand";
import { signOut } from "./auth";
import { useMe } from "./useMe";

/** The shell: the dashboard's sidebar appears once there is a board to show;
 *  until then the screens sit on the plain canvas. */
export function App() {
  const { me } = useMe();
  return (
    <div className="shell">
      <div className="main">
        {me && (
          <div className="topline">
            <Brand />
            <span className="who">
              {me.email} · {me.plan}
              {me.role === "admin" ? " · admin" : ""}
            </span>
            <button className="btn btn-ghost" onClick={signOut}>
              Sign out
            </button>
          </div>
        )}
        <Outlet />
      </div>
    </div>
  );
}
