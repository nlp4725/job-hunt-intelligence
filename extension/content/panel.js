// Shadow-DOM injected panel + a separate apply-confirm toast. Mounted once
// as a sibling of <body>'s own children (not nested inside LinkedIn's own
// containers) so LinkedIn's SPA re-renders never touch/remove it.
//
// content_script.js drives this via the JHIPanel global: call mount() once,
// then render(state, data) on every state change. Button callbacks
// (onApply, onTrackPick, onRetry) are set by content_script.js before the
// first render.
//
// Guarded like content_script.js's own __jhiInjected check: LinkedIn's SPA
// navigation can trigger background.js's webNavigation fallback to
// re-inject all three content scripts on top of the original declarative
// injection. Re-declaring a top-level `const` on the second injection would
// otherwise throw (redeclaration is a SyntaxError, unlike a plain function
// redeclaration) and abort this script's evaluation.
if (!window.JHIPanel) {
window.JHIPanel = (() => {
  let root = null; // shadow root
  let panelEl = null;
  let toastEl = null;

  const callbacks = {
    onApply: () => {},
    onTrackPick: (_track) => {},
    onRetry: () => {},
  };

  const STYLE = `
    :host { all: initial; }
    .jhi-panel, .jhi-toast {
      position: fixed;
      right: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      color: #1a1a1a;
      background: #ffffff;
      border: 1px solid #d0d5dd;
      border-radius: 10px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.15);
      z-index: 2147483647;
      padding: 12px 14px;
    }
    .jhi-panel { bottom: 16px; width: 280px; }
    .jhi-toast { width: 260px; } /* bottom is set dynamically in JS, above the panel's actual rendered height */
    .jhi-title { font-weight: 600; margin-bottom: 2px; }
    .jhi-sub { color: #667085; margin-bottom: 8px; }
    .jhi-score { font-size: 28px; font-weight: 700; }
    .jhi-score-sub { color: #667085; margin-bottom: 8px; }
    .jhi-row { display: flex; justify-content: space-between; padding: 2px 0; }
    .jhi-missing { margin-top: 6px; color: #b42318; }
    .jhi-btn {
      display: inline-block; margin-top: 8px; margin-right: 6px;
      padding: 6px 10px; border-radius: 6px; border: 1px solid #d0d5dd;
      background: #f9fafb; cursor: pointer; font-size: 12px;
    }
    .jhi-btn:hover { background: #eef1f4; }
    .jhi-btn-primary { background: #1a56db; color: white; border-color: #1a56db; }
    .jhi-btn-primary:hover { background: #17479c; }
    .jhi-btn[disabled] { opacity: 0.6; cursor: default; }
    .jhi-spinner {
      width: 14px; height: 14px; border: 2px solid #d0d5dd; border-top-color: #1a56db;
      border-radius: 50%; display: inline-block; margin-right: 6px;
      animation: jhi-spin 0.8s linear infinite; vertical-align: middle;
    }
    @keyframes jhi-spin { to { transform: rotate(360deg); } }
    .jhi-badge {
      display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 10px;
      background: #eef1f4; color: #475467; margin-left: 6px;
    }
  `;

  function ensureMounted() {
    if (root) return;
    const host = document.createElement("div");
    host.id = "jhi-extension-root";
    document.body.appendChild(host);
    root = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = STYLE;
    root.appendChild(style);

    panelEl = document.createElement("div");
    panelEl.className = "jhi-panel";
    panelEl.style.display = "none";
    root.appendChild(panelEl);

    toastEl = document.createElement("div");
    toastEl.className = "jhi-toast";
    toastEl.style.display = "none";
    root.appendChild(toastEl);
  }

  // The panel's content (and therefore height) varies a lot by state — a
  // "scored" render with the full skill breakdown is much taller than
  // "dwelling". A fixed offset for the toast either wastes space or (as
  // observed live) overlaps the panel's buttons on a tall render. Measure
  // the panel's actual rendered height each time the toast is shown instead.
  function positionToastAbovePanel() {
    const panelVisible = panelEl.style.display !== "none";
    const panelHeight = panelVisible ? panelEl.getBoundingClientRect().height : 0;
    const gap = panelVisible ? 12 : 0;
    toastEl.style.bottom = 16 + panelHeight + gap + "px";
  }

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === "text") node.textContent = v;
      else if (k === "onclick") node.addEventListener("click", v);
      else node.setAttribute(k, v);
    });
    children.forEach((c) => node.appendChild(c));
    return node;
  }

  function renderDwelling(data) {
    panelEl.replaceChildren(
      el("div", { class: "jhi-title", text: data.title || "Untitled" }),
      el("div", { class: "jhi-sub", text: data.company || "" }),
      el("div", { text: "Watching this job…" })
    );
  }

  function renderLoading(data) {
    panelEl.replaceChildren(
      el("div", { class: "jhi-title", text: data.title || "Untitled" }),
      el("div", { class: "jhi-sub", text: data.company || "" }),
      el("div", {}, [el("span", { class: "jhi-spinner" }), el("span", { text: "Scoring against your resume…" })])
    );
  }

  // Shared by renderScored and renderBlocked: a block being skipped
  // (agency/duplicate/no-resume) is informational, not a decision the user
  // is locked into — they can still choose to apply to it, so every render
  // that has a real `job` row needs the same apply affordance, not just
  // "scored" ones.
  function applyButtons(job) {
    if (job.applied) {
      const label = job.applied_resume_version ? `Applied ✓ (${job.applied_resume_version})` : "Applied ✓";
      const appliedBtn = el("button", { class: "jhi-btn jhi-btn-primary", text: label });
      appliedBtn.setAttribute("disabled", "true");
      return [appliedBtn];
    }
    // Two versions, not one "I Applied" button — August 2026 resume A/B
    // test (see CONTEXT.md "Resume A/B Test"): v1 frames the tender
    // project as a Project, v2 as Experience/"Independent AI Engineer".
    // Recording which one went out per application is the whole point.
    return [
      el("button", { class: "jhi-btn jhi-btn-primary", text: "Applied w/ v1", onclick: () => callbacks.onApply("v1") }),
      el("button", { class: "jhi-btn jhi-btn-primary", text: "Applied w/ v2", onclick: () => callbacks.onApply("v2") }),
    ];
  }

  function renderScored(data) {
    const { job, score } = data;
    const children = [
      el("div", { class: "jhi-title", text: job.title || "Untitled" }),
      el(
        "div",
        { class: "jhi-sub" },
        [
          document.createTextNode(job.company_name || ""),
          el("span", { class: "jhi-badge", text: job.track }),
        ]
      ),
      el("div", { class: "jhi-score", text: String(score.total_score) + " / 15" }),
      el("div", { class: "jhi-score-sub", text: data.cached ? "cached score" : "just scored" }),
      el("div", { class: "jhi-row" }, [el("span", { text: "Skill" }), el("span", { text: String(score.skill_score) })]),
      el("div", { class: "jhi-row" }, [el("span", { text: "Seniority" }), el("span", { text: String(score.seniority_score) })]),
      el("div", { class: "jhi-row" }, [el("span", { text: "Expertise" }), el("span", { text: String(score.expertise_score) })]),
    ];

    if (score.skill_missing && score.skill_missing.length) {
      children.push(el("div", { class: "jhi-missing", text: "Missing: " + score.skill_missing.join(", ") }));
    }

    children.push(...applyButtons(job));

    const otherTrack = job.track === "ml_ai" ? "pm" : "ml_ai";
    children.push(
      el("button", {
        class: "jhi-btn",
        text: `Re-score as ${otherTrack}`,
        onclick: () => callbacks.onTrackPick(otherTrack),
      })
    );

    panelEl.replaceChildren(...children);
  }

  function renderBlocked(data) {
    const messages = {
      agency: "Skipped — this looks like an agency/staffing posting, not a direct employer.",
      duplicate: data.refreshed
        ? "Repost of a job already in your DB — original refreshed to today, back on the dashboard."
        : "Skipped — this is a repost of a job already in your DB.",
      no_resume_for_track: `Skipped — no resume on file for track "${data.job.track}".`,
    };
    const children = [
      el("div", { class: "jhi-title", text: data.job.title || "Untitled" }),
      el("div", { class: "jhi-sub", text: data.job.company_name || "" }),
      el("div", { text: messages[data.reason] || "Skipped." }),
    ];
    if (data.reason === "duplicate" && data.duplicate_of) {
      const d = data.duplicate_of;
      children.push(
        el("div", {
          class: "jhi-sub",
          text: `Original: "${d.title}"${d.applied ? " (already applied)" : ""}${
            d.total_score != null ? `, score ${d.total_score}/15` : ""
          }`,
        })
      );
    }
    // The block (agency/duplicate/no-resume) doesn't stop the user from
    // applying anyway — this row gets its own independent applied status,
    // separate from whatever the duplicate's original is marked as.
    children.push(...applyButtons(data.job));
    panelEl.replaceChildren(...children);
  }

  function renderNeedsTrack(data) {
    panelEl.replaceChildren(
      el("div", { class: "jhi-title", text: data.title || "Untitled" }),
      el("div", { class: "jhi-sub", text: data.company_name || "" }),
      el("div", { text: "Which track is this?" }),
      el("button", { class: "jhi-btn jhi-btn-primary", text: "ML/AI", onclick: () => callbacks.onTrackPick("ml_ai") }),
      el("button", { class: "jhi-btn jhi-btn-primary", text: "PM", onclick: () => callbacks.onTrackPick("pm") })
    );
  }

  function renderError(data) {
    panelEl.replaceChildren(
      el("div", { class: "jhi-title", text: "Something went wrong" }),
      el("div", { class: "jhi-sub", text: data.message || "" }),
      el("button", { class: "jhi-btn", text: "Retry", onclick: () => callbacks.onRetry() })
    );
  }

  function render(state, data = {}) {
    ensureMounted();
    if (state === "idle") {
      panelEl.style.display = "none";
      return;
    }
    panelEl.style.display = "block";
    if (state === "dwelling") renderDwelling(data);
    else if (state === "loading") renderLoading(data);
    else if (state === "scored") renderScored(data);
    else if (state === "blocked") renderBlocked(data);
    else if (state === "needs_track") renderNeedsTrack(data);
    else if (state === "error") renderError(data);
  }

  function showApplyConfirm(title, onAnswer) {
    ensureMounted();
    positionToastAbovePanel();
    toastEl.style.display = "block";
    // onAnswer receives "v1"/"v2" (applied, with that resume version) or
    // null (didn't apply) — same version tagging as the panel's own apply
    // buttons, so the passive prompt and the manual button stay consistent.
    const v1Btn = el("button", { class: "jhi-btn jhi-btn-primary", text: "v1" });
    const v2Btn = el("button", { class: "jhi-btn jhi-btn-primary", text: "v2" });
    const noBtn = el("button", { class: "jhi-btn", text: "No" });
    toastEl.replaceChildren(
      el("div", { class: "jhi-sub", text: `Did you apply to "${title}"?` }),
      v1Btn,
      v2Btn,
      noBtn
    );
    const dismiss = (version) => {
      onAnswer(version);
      toastEl.style.display = "none";
    };
    v1Btn.addEventListener("click", () => dismiss("v1"));
    v2Btn.addEventListener("click", () => dismiss("v2"));
    noBtn.addEventListener("click", () => dismiss(null));
  }

  function showAppliedConfirmation() {
    ensureMounted();
    positionToastAbovePanel();
    toastEl.style.display = "block";
    toastEl.replaceChildren(el("div", { text: "Recorded ✓" }));
    setTimeout(() => {
      toastEl.style.display = "none";
    }, 1500);
  }

  function hideToast() {
    if (toastEl) toastEl.style.display = "none";
  }

  return {
    mount: ensureMounted,
    render,
    showApplyConfirm,
    showAppliedConfirmation,
    hideToast,
    set onApply(fn) {
      callbacks.onApply = fn;
    },
    set onTrackPick(fn) {
      callbacks.onTrackPick = fn;
    },
    set onRetry(fn) {
      callbacks.onRetry = fn;
    },
  };
})();
}
