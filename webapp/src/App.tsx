import { Outlet } from "react-router-dom";

import { useMe } from "./useMe";
import { signIn, signOut } from "./auth";

export function App() {
  const { me } = useMe();
  return (
    <>
      <header className="topbar">
        <span className="brand">JoblyGo</span>
        {me ? (
          <span className="who">
            {me.email} · {me.plan}
            {me.role === "admin" ? " · admin" : ""} <button className="secondary" onClick={signOut}>Sign out</button>
          </span>
        ) : (
          <button onClick={() => signIn()}>Sign in</button>
        )}
      </header>
      <main>
        <Outlet />
      </main>
    </>
  );
}
