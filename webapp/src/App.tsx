import { Outlet } from "react-router-dom";

/** The board and the landing page bring their own chrome, so the shell is just
 *  the router's outlet. */
export function App() {
  return <Outlet />;
}
