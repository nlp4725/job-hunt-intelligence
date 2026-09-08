// Orchestration: detects which LinkedIn job the user is looking at (SPA —
// no full page reload between jobs), runs a 6s dwell timer before scoring,
// renders the panel, and prompts an active "did you apply?" confirmation
// when the user moves off a job they viewed (rather than relying on them
// to remember to click a button).
//
// Guards against double-injection since background.js's webNavigation
// fallback (SPA soft-nav re-injection) may run alongside the manifest's own
// declarative content_scripts injection.
(function () {
  if (window.__jhiInjected) return;
  window.__jhiInjected = true;

  const DWELL_MS = 6000;

  const state = {
    lastJobId: null,
    dwellTimer: null,
    currentJobRowId: null, // numeric Job.id, once scored/blocked
    currentJobTitle: null,
    currentJobApplied: false,
    askedApplyForJobId: new Set(), // linkedin job_id strings already asked this tab session
    lastRenderState: "idle",
    lastRenderData: {},
  };

  function sendMessage(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (resp) => resolve(resp));
      } catch (e) {
        resolve({ ok: false, error: String(e) });
      }
    });
  }

  function renderPanel(panelState, data) {
    state.lastRenderState = panelState;
    state.lastRenderData = data || {};
    JHIPanel.render(panelState, data);
  }

  function maybeConfirmApply(prevRowId, prevJobId, prevTitle, prevApplied) {
    if (prevRowId == null || prevJobId == null) return;
    if (prevApplied) return;
    if (state.askedApplyForJobId.has(prevJobId)) return;
    state.askedApplyForJobId.add(prevJobId);
    // version is "v1"/"v2" (applied, with that resume) or null (didn't
    // apply) — see CONTEXT.md "Resume A/B Test".
    JHIPanel.showApplyConfirm(prevTitle || "this job", async (version) => {
      if (version) {
        await sendMessage({ type: "MARK_APPLIED", payload: { jobId: prevRowId, version } });
        JHIPanel.showAppliedConfirmation();
      }
    });
  }

  function handleScoreResponse(jobId, resp) {
    if (getJobIdFromLocation() !== jobId) return; // user already moved on — stale response

    if (!resp || !resp.ok) {
      const message = (resp && resp.error) || (resp && resp.data && resp.data.error) || "Request failed.";
      renderPanel("error", { message });
      return;
    }

    const data = resp.data;
    if (data.status === "needs_track") {
      state.currentJobRowId = null;
      renderPanel("needs_track", data);
    } else if (data.status === "blocked") {
      state.currentJobRowId = data.job.id;
      state.currentJobTitle = data.job.title;
      state.currentJobApplied = data.job.applied;
      renderPanel("blocked", data);
    } else if (data.status === "scored") {
      state.currentJobRowId = data.job.id;
      state.currentJobTitle = data.job.title;
      state.currentJobApplied = data.job.applied;
      renderPanel("scored", data);
    }
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function captureAndScore(jobId, trackOverride) {
    let detail = extractJobDetail();
    if (!detail.job_id || !detail.title || !detail.raw_text) {
      renderPanel("error", { message: "Extraction incomplete — see console for which field." });
      return;
    }

    // The JD body (from the "About the job" heading) tends to render before
    // the top-card's company/location/date bits finish hydrating on some
    // listings — a real race observed live (National Debt Relief posting:
    // raw_text was ready at 6s, company link wasn't yet). One bounded
    // re-extract after a short pause catches a still-loading top card
    // without risking an infinite wait on a listing that never shows it.
    // Also covers posted_date: same top-card, same hydration race — found
    // 2026-08-18 that the vast majority of extension-captured jobs shipped
    // with a null posted_date because only `company` triggered the retry.
    if ((!detail.company || !detail.posted_date) && getJobIdFromLocation() === jobId) {
      await sleep(1200);
      if (getJobIdFromLocation() !== jobId) return; // moved on during the pause
      const retried = extractJobDetail();
      if (retried.company) detail.company = retried.company;
      if (retried.posted_date) detail.posted_date = retried.posted_date;

      // The telemetry has to describe the values actually being shipped, not
      // the pre-hydration read. Without this every successful retry still
      // reports company/posted_date as failed, and the drift alarm in
      // tests_and_eval/extraction_health.py cries wolf on a perfectly healthy
      // session — worse than no alarm, because it teaches you to ignore it.
      const meta = detail.extraction_meta;
      for (const field of ["company", "posted_date"]) {
        if (retried[field]) {
          meta.strategies[field] = retried.extraction_meta.strategies[field];
          meta.failed_fields = meta.failed_fields.filter((f) => f !== field);
        }
      }
      if (!meta.failed_fields.length) meta.snapshot_html = null;
    }

    renderPanel("loading", detail);
    const resp = await sendMessage({
      type: "SCORE_JOB",
      payload: { ...detail, track_override: trackOverride || null },
    });
    handleScoreResponse(jobId, resp);
  }

  async function onPossibleJobChange() {
    const jobId = getJobIdFromLocation();
    if (jobId === state.lastJobId) return;

    // Leaving the previous job (if any) — fire the active apply-confirm
    // before switching, non-blocking with respect to the new job below.
    maybeConfirmApply(state.currentJobRowId, state.lastJobId, state.currentJobTitle, state.currentJobApplied);

    clearTimeout(state.dwellTimer);
    state.lastJobId = jobId;
    state.currentJobRowId = null;
    state.currentJobTitle = null;
    state.currentJobApplied = false;

    if (jobId === null) {
      renderPanel("idle", {});
      return;
    }

    const detail = extractJobDetail();
    state.currentJobTitle = detail.title;
    renderPanel("dwelling", detail);

    // Instant DB check — a read costs nothing (no LLM call), so a job we
    // already know about (applied, scored, or agency/duplicate-blocked)
    // skips the dwell gate entirely instead of waiting 6s to re-learn
    // something already on record. The dwell timer exists to hold off
    // *new*, LLM-costing scoring — not to re-check what's already known.
    const lookup = await sendMessage({ type: "LOOKUP_JOB", payload: { jobId } });
    if (getJobIdFromLocation() !== jobId) return; // moved on while the lookup was in flight

    if (lookup && lookup.ok && lookup.data && lookup.data.status !== "not_found") {
      handleScoreResponse(jobId, lookup);
      return;
    }

    state.dwellTimer = setTimeout(() => {
      if (getJobIdFromLocation() === jobId) captureAndScore(jobId, null);
    }, DWELL_MS);
  }

  // Job-change detection signals — LinkedIn updates the page without a full
  // reload when the user clicks a different job in the list. <title>
  // updating per selection and a 1s href poll are unverified assumptions
  // (see plan §6 risk 3); the poll is a deliberate backstop for whichever
  // one doesn't hold.
  const titleEl = document.querySelector("title");
  if (titleEl) {
    new MutationObserver(onPossibleJobChange).observe(titleEl, { childList: true, characterData: true, subtree: true });
  }
  setInterval(onPossibleJobChange, 1000);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      maybeConfirmApply(state.currentJobRowId, state.lastJobId, state.currentJobTitle, state.currentJobApplied);
    }
  });

  JHIPanel.onApply = (version) => {
    if (state.currentJobRowId == null) return;
    sendMessage({ type: "MARK_APPLIED", payload: { jobId: state.currentJobRowId, version } }).then((resp) => {
      if (!resp || !resp.ok) return;
      state.currentJobApplied = true;
      if (state.lastJobId != null) state.askedApplyForJobId.add(state.lastJobId);
      if (state.lastRenderData.job) {
        state.lastRenderData.job.applied = true;
        state.lastRenderData.job.applied_resume_version = version;
      }
      JHIPanel.showAppliedConfirmation();
      renderPanel(state.lastRenderState, state.lastRenderData);
    });
  };

  JHIPanel.onTrackPick = (track) => {
    if (state.lastJobId != null) captureAndScore(state.lastJobId, track);
  };

  JHIPanel.onRetry = () => {
    if (state.lastJobId != null) captureAndScore(state.lastJobId, null);
  };

  JHIPanel.mount();
  onPossibleJobChange(); // handles the case where this script loads directly onto a job already selected
})();
