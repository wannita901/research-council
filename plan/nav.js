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
  { n: 5, name: "Implementation (v1)",      status: "done" },
  { n: 6, name: "Re-grounding (v2 design)", status: "done" },
  { n: 7, name: "Implementation (v2)",      status: "wip" },
];
// Note: standalone "Evaluation" demoted to optional after the product pivot (see plan/5, plan/12).

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
  { file: "11_runner-service-arch.html",  n: 11, phase: 5, title: "Runner & Service Architecture" },
  { file: "12_product-regrounding.html",  n: 12, phase: 6, title: "Product Re-grounding (Agentic v2)" },
  { file: "13_research-lifecycle.html",   n: 13, phase: 6, title: "Macro Lifecycle — 3 Stages" },
  { file: "14_build-stack-v2.html",       n: 14, phase: 6, title: "Build Stack v2 — Frameworks" },
  { file: "15_design-backlog.html",       n: 15, phase: 6, title: "Design Backlog (open decisions)" },
  { file: "16_llmwiki-spec-revisit.html", n: 16, phase: 7, title: "LLM-Wiki Spec Revisit (grounded)" },
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
        <div class="rm-title">Roadmap — ${PHASES.length} phases</div>
        ${roadmap}
      </div>

      <div class="nav-title">Plan docs — newest first (${PAGES.length})</div>
      <nav>${links}</nav>

      <div class="sidebar-foot">P1–6 done · P7 v2 build in progress — agentic ideation runs end-to-end.</div>
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
