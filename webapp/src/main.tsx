import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { App } from "./App";
import { Callback } from "./pages/Callback";
import { Home } from "./pages/Home";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route path="/" element={<Home />} />
          <Route path="/auth/callback" element={<Callback />} />
          <Route path="*" element={<p>Nothing here.</p>} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
