// Apply saved theme early (before paint) to avoid a flash.
(function () {
  try { var t = localStorage.getItem("rc-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
})();

const PROJECT = "research-council";
const TAGLINE = "Multi-agent AI4SE research copilot";

// The project has SIX phases. Update status as we progress.
const PHASES = [
  { n: 1, name: "Research",                status: "done" },
  { n: 2, name: "Architecture & Design",   status: "done" },
  { n: 3, name: "Research Framing & Eval", status: "done" },
  { n: 4, name: "Build Specs",             status: "done" },
  { n: 5, name: "Implementation",          status: "wip" },
  { n: 6, name: "Evaluation",              status: "todo" },
];

// Plan documents, in creation order. `phase` maps each doc to a phase above.
const PAGES = [
  { file: "1_sota-gap-analysis.html",     n: 1, phase: 1, title: "SOTA & Gap Analysis" },
  { file: "2_architecture-design.html",   n: 2, phase: 2, title: "Architecture & Debate Protocol" },
  { file: "3_design-decisions.html",      n: 3, phase: 2, title: "Open Questions Resolved" },
  { file: "4_scenario-flow.html",         n: 4, phase: 2, title: "Debate Flow Walkthrough" },
  { file: "5_research-framing-eval.html", n: 5, phase: 3, title: "Research Framing & Eval Design" },
  { file: "6_vertical-slice-spec.html",   n: 6, phase: 4, title: "Vertical Slice Spec" },
  { file: "7_observability.html",         n: 7, phase: 4, title: "Observability & Telemetry" },
  { file: "8_knowledge-layer.html",       n: 8, phase: 4, title: "Knowledge Layer (LLM Wiki)" },
  { file: "9_librarian-design.html",      n: 9, phase: 4, title: "Wiki Librarian Design" },
  { file: "10_build-log.html",            n: 10, phase: 5, title: "Build Log — Increment 1" },
];

const STATUS_ICON = { done: "✓", wip: "◐", next: "▸", todo: "○" };
const pad = n => String(n).padStart(2, "0");

function buildSidebar() {
  const current = location.pathname.split("/").pop() || PAGES[0].file;

  const roadmap = PHASES.map(p => `
    <div class="rm-row ${p.status}">
      <span class="rm-ic">${STATUS_ICON[p.status]}</span>
      <span class="rm-n">${p.n}</span>
      <span class="rm-name">${p.name}</span>
    </div>`).join("");

  // newest doc on top
  const links = PAGES.slice().reverse().map(p => {
    const active = p.file === current ? " active" : "";
    return `<a class="doc${active}" href="${p.file}">
        <span class="docnum">${pad(p.n)}</span>
        <span class="doctitle">${p.title}</span>
        <span class="phasechip">P${p.phase}</span>
      </a>`;
  }).join("");

  return `
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-row">
          <div>
            <div class="brand-name">${PROJECT}</div>
            <div class="brand-tag">${TAGLINE}</div>
          </div>
          <button class="theme-toggle" id="rcTheme" title="Toggle light / dark" aria-label="Toggle theme"></button>
        </div>
      </div>

      <div class="roadmap">
        <div class="rm-title">Roadmap — 6 phases</div>
        ${roadmap}
      </div>

      <div class="nav-title">Plan docs — newest first (${PAGES.length})</div>
      <nav>${links}</nav>

      <div class="sidebar-foot">Phases 1–4 done · phase 5 (Implementation) is next.</div>
    </aside>`;
}

function setThemeLabel() {
  const btn = document.getElementById("rcTheme");
  if (!btn) return;
  btn.textContent = document.documentElement.dataset.theme === "light" ? "☀ Light" : "☾ Dark";
}

document.addEventListener("DOMContentLoaded", () => {
  document.body.insertAdjacentHTML("afterbegin", buildSidebar());
  setThemeLabel();
  const btn = document.getElementById("rcTheme");
  if (btn) btn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("rc-theme", next); } catch (e) {}
    setThemeLabel();
  });
});
