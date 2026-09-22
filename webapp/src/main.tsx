import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { App } from "./App";
import { AdminExtension } from "./pages/AdminExtension";
import { Callback } from "./pages/Callback";
import { Forgot } from "./pages/Forgot";
import { Home } from "./pages/Home";
import { PublicBoard } from "./pages/PublicBoard";
import { SignIn } from "./pages/SignIn";
import { SignUp } from "./pages/SignUp";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route path="/" element={<Home />} />
          {/* Our own sign-in pages. /auth/callback stays for a federated
              (Google/Apple) sign-in, which can only come back through it. */}
          <Route path="/signin" element={<SignIn />} />
          <Route path="/signup" element={<SignUp />} />
          <Route path="/forgot" element={<Forgot />} />
          <Route path="/jobs" element={<PublicBoard />} />
          {/* Collector-only errand, kept off the sidebar so users never see it. */}
          <Route path="/admin/extension" element={<AdminExtension />} />
          <Route path="/auth/callback" element={<Callback />} />
          <Route path="*" element={<Home />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
