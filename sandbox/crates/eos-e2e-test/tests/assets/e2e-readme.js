(function () {
  "use strict";

  const pageKind = document.body.dataset.page || "home";

  function $(selector, root = document) {
    return root.querySelector(selector);
  }

  function $all(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function inlineMarkdown(value) {
    return String(value ?? "")
      .split(/(`[^`]*`)/g)
      .map((part) => {
        if (part.startsWith("`") && part.endsWith("`")) {
          return `<code>${escapeHtml(part.slice(1, -1))}</code>`;
        }
        return escapeHtml(part);
      })
      .join("");
  }

  function text(value) {
    return escapeHtml(value ?? "");
  }

  function readEmbeddedJson(id) {
    const node = document.getElementById(id);
    if (!node) return null;
    try {
      return JSON.parse(node.textContent || "null");
    } catch (error) {
      return { error: `Embedded JSON parse failed: ${error.message}` };
    }
  }

  async function fetchJson(path) {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`${path}: ${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  function unique(values) {
    return Array.from(new Set(values.filter(Boolean)));
  }

  function anchorFor(prefix, id) {
    return `${prefix}-${String(id).replace(/[^A-Za-z0-9_-]+/g, "-")}`;
  }

  function truncate(value, maxLength) {
    const clean = String(value ?? "").replace(/\s+/g, " ").trim();
    if (clean.length <= maxLength) return clean;
    return `${clean.slice(0, maxLength - 1).trim()}...`;
  }

  function moduleStats(moduleData) {
    const checklist = moduleData.checklist || [];
    const tests = moduleData.test || [];
    const ops = moduleData.overview?.ops || [];
    const coveredIds = unique(tests.flatMap((testCase) => testCase.checklist || []));
    const checklistIds = new Set(checklist.map((item) => item.id));
    const unknownRefs = coveredIds.filter((id) => !checklistIds.has(id));
    const uncovered = checklist.map((item) => item.id).filter((id) => !coveredIds.includes(id));
    return {
      checklist: checklist.length,
      tests: tests.length,
      ops: ops.length,
      covered: coveredIds.filter((id) => checklistIds.has(id)).length,
      unknownRefs,
      uncovered
    };
  }

  function coverageMap(moduleData) {
    const map = new Map();
    for (const item of moduleData.checklist || []) {
      map.set(item.id, []);
    }
    for (const testCase of moduleData.test || []) {
      for (const id of testCase.checklist || []) {
        if (!map.has(id)) map.set(id, []);
        map.get(id).push(testCase);
      }
    }
    return map;
  }

  function validationMessages(moduleData) {
    const checklist = moduleData.checklist || [];
    const tests = moduleData.test || [];
    const ids = checklist.map((item) => item.id);
    const duplicateIds = unique(ids.filter((id, index) => ids.indexOf(id) !== index));
    const known = new Set(ids);
    const unknownRefs = [];
    const testNames = tests.map((testCase) => testCase.name);
    const duplicateTests = unique(testNames.filter((name, index) => testNames.indexOf(name) !== index));
    for (const testCase of tests) {
      for (const id of testCase.checklist || []) {
        if (!known.has(id)) {
          unknownRefs.push(`${testCase.name} -> ${id}`);
        }
      }
    }
    const covered = new Set(tests.flatMap((testCase) => testCase.checklist || []));
    const uncovered = ids.filter((id) => !covered.has(id));
    return { duplicateIds, duplicateTests, unknownRefs, uncovered };
  }

  function validationPanel(moduleData) {
    const messages = validationMessages(moduleData);
    const rows = [];
    if (messages.duplicateIds.length) {
      rows.push(`<li>Duplicate checklist IDs: ${messages.duplicateIds.map((id) => `<code>${text(id)}</code>`).join(", ")}</li>`);
    }
    if (messages.duplicateTests.length) {
      rows.push(`<li>Duplicate test case names: ${messages.duplicateTests.map((id) => `<code>${text(id)}</code>`).join(", ")}</li>`);
    }
    if (messages.unknownRefs.length) {
      rows.push(`<li>Unknown checklist references: ${messages.unknownRefs.map((id) => `<code>${text(id)}</code>`).join(", ")}</li>`);
    }
    if (messages.uncovered.length) {
      rows.push(`<li>Checklist IDs without a mapped test: ${messages.uncovered.map((id) => `<code>${text(id)}</code>`).join(", ")}</li>`);
    }
    if (!rows.length) {
      return `
        <section class="panel good" id="validation">
          <h2>Validation</h2>
          <p>All checklist IDs are unique, all test references resolve, and each checklist ID is mapped by at least one test case.</p>
        </section>`;
    }
    return `
      <section class="panel note" id="validation">
        <h2>Validation</h2>
        <ul>${rows.join("")}</ul>
      </section>`;
  }

  function chipList(values, className = "chip") {
    if (!values || !values.length) return `<span class="muted small">None</span>`;
    return values.map((value) => `<span class="${className}">${inlineMarkdown(value)}</span>`).join("");
  }

  function linkedChecklistChips(values) {
    if (!values || !values.length) return `<span class="muted small">None</span>`;
    return values
      .map((id) => `<a class="id-badge" href="#${anchorFor("checklist", id)}">${text(id)}</a>`)
      .join("");
  }

  function linkedTestChips(testCases) {
    if (!testCases || !testCases.length) return `<span class="muted small">Unmapped</span>`;
    return testCases
      .map((testCase) => `<a class="chip" href="#${anchorFor("test", testCase.name)}">${text(testCase.name)}</a>`)
      .join("");
  }

  function toolbar(filters, searchLabel) {
    return `
      <nav class="toolbar">
        <div class="wrap">
          ${filters.map((filter, index) => `
            <button class="filter-btn${index === 0 ? " active" : ""}" type="button" data-filter="${text(filter.id)}">${text(filter.label)}</button>
          `).join("")}
          <span class="toolbar-spacer"></span>
          <input class="search" type="search" placeholder="${text(searchLabel)}" aria-label="${text(searchLabel)}" data-search>
        </div>
      </nav>`;
  }

  function setupFilters(selector) {
    const search = $("[data-search]");
    const buttons = $all("[data-filter]");
    const items = $all(selector);
    let activeFilter = buttons[0]?.dataset.filter || "all";

    function matchesFilter(item) {
      if (activeFilter === "all") return true;
      return item.dataset.kind === activeFilter || item.dataset[activeFilter] === "true";
    }

    function apply() {
      const query = (search?.value || "").trim().toLowerCase();
      for (const item of items) {
        const haystack = (item.dataset.search || item.textContent || "").toLowerCase();
        const visible = matchesFilter(item) && (!query || haystack.includes(query));
        item.classList.toggle("hidden", !visible);
      }
    }

    for (const button of buttons) {
      button.addEventListener("click", () => {
        activeFilter = button.dataset.filter || "all";
        for (const candidate of buttons) {
          candidate.classList.toggle("active", candidate === button);
        }
        apply();
      });
    }
    search?.addEventListener("input", apply);
    apply();
  }

  function setupCopyButtons() {
    for (const button of $all("[data-copy]")) {
      button.addEventListener("click", async () => {
        const value = button.dataset.copy || "";
        try {
          await navigator.clipboard.writeText(value);
          const old = button.textContent;
          button.textContent = "Copied";
          setTimeout(() => {
            button.textContent = old;
          }, 1200);
        } catch (_error) {
          button.textContent = "Select";
        }
      });
    }
  }

  function setupChecklistState(moduleName) {
    const storageKey = `eos-e2e-readme:${moduleName}:checklist`;
    let state = {};
    try {
      state = JSON.parse(localStorage.getItem(storageKey) || "{}");
    } catch (_error) {
      state = {};
    }
    for (const item of $all(".check-item")) {
      const id = item.dataset.id;
      const input = $("input[type='checkbox']", item);
      if (!id || !input) continue;
      input.checked = Boolean(state[id]);
      input.addEventListener("change", () => {
        state[id] = input.checked;
        localStorage.setItem(storageKey, JSON.stringify(state));
      });
    }
  }

  function renderHome(data) {
    const modules = data.modules || [];
    const totals = modules.reduce((acc, moduleData) => {
      const stats = moduleStats(moduleData);
      acc.checklist += stats.checklist;
      acc.tests += stats.tests;
      acc.ops += stats.ops;
      acc.unmapped += stats.uncovered.length + stats.unknownRefs.length;
      return acc;
    }, { checklist: 0, tests: 0, ops: 0, unmapped: 0 });

    document.title = "eos-e2e-test Readme Index";
    $("#app").innerHTML = `
      <header>
        <div class="wrap hero">
          <h1>eos-e2e-test module index</h1>
          <p class="lead">Generated from per-module <code>readme.json</code> data under <code>sandbox/crates/eos-e2e-test/tests</code>. The page renders module overview, checklist IDs, and test cases without hand-editing the HTML content.</p>
          <div class="meta-row">
            <span class="pill">Schema version: <code>${text(data.manifest?.schemaVersion || 1)}</code></span>
            <span class="pill">Source: <code>tests/*/readme.json</code></span>
            <span class="pill">Cargo package: <code>eos-e2e-test</code></span>
          </div>
          <div class="stats">
            <div class="stat"><strong>${modules.length}</strong><span>test modules</span></div>
            <div class="stat"><strong>${totals.checklist}</strong><span>checklist IDs</span></div>
            <div class="stat"><strong>${totals.tests}</strong><span>test cases</span></div>
            <div class="stat"><strong>${totals.unmapped}</strong><span>mapping warnings</span></div>
          </div>
        </div>
      </header>
      ${toolbar([
        { id: "all", label: "All" },
        { id: "unmapped", label: "Unmapped" },
        { id: "checklist", label: "Checklist" },
        { id: "tests", label: "Tests" }
      ], "Search modules, checklist IDs, tests, commands")}
      <main class="wrap">
        <section class="grid">
          <div class="panel good">
            <h2>Data Flow</h2>
            <p>The home page reads the manifest, loads each module JSON, and renders summary sections. Module pages use the same schema and shared renderer.</p>
          </div>
          <div class="panel note">
            <h2>Refresh Rule</h2>
            <p>Update module data in <code>readme.md</code> or <code>readme.json</code>, then regenerate the pages. The HTML should not receive manual checklist or test-case edits.</p>
          </div>
        </section>
        <section>
          <h2>Test Modules</h2>
          <div class="module-list">
            ${modules.map((moduleData) => renderModuleCard(moduleData)).join("")}
          </div>
        </section>
        <footer>
          <p>Rendered from <code>readme-manifest.json</code> and per-module <code>readme.json</code> files.</p>
        </footer>
      </main>`;
    setupFilters(".module-card");
  }

  function renderModuleCard(moduleData) {
    const stats = moduleStats(moduleData);
    const checklistIds = (moduleData.checklist || []).map((item) => item.id);
    const testNames = (moduleData.test || []).map((testCase) => testCase.name);
    const page = moduleData.page || `${moduleData.name}/index.html`;
    const search = [
      moduleData.name,
      moduleData.displayName,
      moduleData.overview?.summary,
      checklistIds.join(" "),
      testNames.join(" "),
      (moduleData.test || []).map((testCase) => testCase.command).join(" ")
    ].join(" ");
    return `
      <article class="module-card" data-kind="module" data-unmapped="${stats.uncovered.length || stats.unknownRefs.length ? "true" : "false"}" data-checklist="true" data-tests="true" data-search="${text(search)}">
        <div>
          <h3 class="module-title"><a href="${text(page)}">${text(moduleData.displayName || moduleData.name)}</a></h3>
          <div class="chip-row">
            <span class="pill">${stats.checklist} checklist</span>
            <span class="pill">${stats.tests} tests</span>
            <span class="pill">${stats.ops} ops</span>
          </div>
        </div>
        <div>
          <p class="summary-text">${inlineMarkdown(truncate(moduleData.overview?.summary || "", 280))}</p>
          <div class="chip-row">
            ${checklistIds.slice(0, 12).map((id) => `<a class="id-badge" href="${text(page)}#${anchorFor("checklist", id)}">${text(id)}</a>`).join("")}
            ${checklistIds.length > 12 ? `<span class="chip">+${checklistIds.length - 12} more</span>` : ""}
          </div>
        </div>
      </article>`;
  }

  function renderModulePage(moduleData) {
    const stats = moduleStats(moduleData);
    const config = moduleData.overview?.moduleConfig;
    document.title = `${moduleData.displayName || moduleData.name} - eos-e2e-test`;
    $("#app").innerHTML = `
      <header>
        <div class="wrap hero">
          <div class="crumbs"><a href="../index.html">E2E Tests</a> / ${text(moduleData.displayName || moduleData.name)}</div>
          <h1>${text(moduleData.displayName || moduleData.name)}</h1>
          <p class="lead">${inlineMarkdown(moduleData.overview?.summary || "")}</p>
          <div class="meta-row">
            <span class="pill">Schema version: <code>${text(moduleData.schemaVersion || 1)}</code></span>
            ${config ? `<span class="pill">Config: <code>${text(config)}</code></span>` : ""}
            <span class="pill">Source: <code>${text(moduleData.source?.markdown || "readme.md")}</code></span>
          </div>
          <div class="stats">
            <div class="stat"><strong>${stats.checklist}</strong><span>checklist IDs</span></div>
            <div class="stat"><strong>${stats.tests}</strong><span>test cases</span></div>
            <div class="stat"><strong>${stats.ops}</strong><span>daemon ops</span></div>
            <div class="stat"><strong>${stats.uncovered.length}</strong><span>unmapped IDs</span></div>
          </div>
        </div>
      </header>
      ${toolbar([
        { id: "all", label: "All" },
        { id: "checklist", label: "Checklist" },
        { id: "tests", label: "Tests" },
        { id: "unmapped", label: "Unmapped" }
      ], "Search this module")}
      <main class="wrap layout">
        <aside class="panel rail" aria-label="Checklist IDs">
          <strong>Checklist IDs</strong>
          ${(moduleData.checklist || []).map((item) => `<a href="#${anchorFor("checklist", item.id)}">${text(item.id)}</a>`).join("")}
        </aside>
        <div class="content-stack">
          ${renderOverview(moduleData)}
          ${renderCoverageMatrix(moduleData)}
          ${renderChecklist(moduleData)}
          ${renderTests(moduleData)}
          ${validationPanel(moduleData)}
          <footer>
            <p>This page is generated from <code>readme.json</code>. Checklist and test-case content should stay in the JSON source, not in this HTML.</p>
          </footer>
        </div>
      </main>`;
    setupFilters(".filterable");
    setupChecklistState(moduleData.name);
    setupCopyButtons();
  }

  function renderOverview(moduleData) {
    const overview = moduleData.overview || {};
    return `
      <section class="panel filterable" id="overview" data-kind="overview" data-search="${text([overview.summary, overview.moduleConfig, (overview.ops || []).join(" "), (overview.tags || []).join(" ")].join(" "))}">
        <h2>Overview</h2>
        <p>${inlineMarkdown(overview.summary || "")}</p>
        <div class="grid">
          <div>
            <h3>Module Config</h3>
            <p>${overview.moduleConfig ? `<code>${text(overview.moduleConfig)}</code>` : `<span class="muted">No module config detected.</span>`}</p>
          </div>
          <div>
            <h3>Tags</h3>
            <div class="chip-row">${chipList(overview.tags || [])}</div>
          </div>
        </div>
        <h3>Daemon Ops</h3>
        <div class="chip-row">${chipList(overview.ops || [])}</div>
      </section>`;
  }

  function renderCoverageMatrix(moduleData) {
    const coverage = coverageMap(moduleData);
    const rows = (moduleData.checklist || []).map((item) => {
      const tests = coverage.get(item.id) || [];
      return `
        <tr class="filterable" data-kind="checklist" data-unmapped="${tests.length ? "false" : "true"}" data-search="${text([item.id, item.description, tests.map((testCase) => testCase.name).join(" ")].join(" "))}">
          <td><a class="id-badge" href="#${anchorFor("checklist", item.id)}">${text(item.id)}</a></td>
          <td>${linkedTestChips(tests)}</td>
          <td>${tests.map((testCase) => `<code>${text(testCase.command)}</code>`).join("<br>") || `<span class="muted">No mapped command</span>`}</td>
        </tr>`;
    }).join("");
    return `
      <section class="panel" id="coverage">
        <h2>Coverage Matrix</h2>
        <table class="matrix">
          <thead><tr><th>Checklist ID</th><th>Covered by test case</th><th>Command</th></tr></thead>
          <tbody>${rows || `<tr><td colspan="3" class="empty">No checklist data.</td></tr>`}</tbody>
        </table>
      </section>`;
  }

  function renderChecklist(moduleData) {
    const coverage = coverageMap(moduleData);
    return `
      <section id="checklist">
        <h2>Checklist Details</h2>
        <div class="checklist">
          ${(moduleData.checklist || []).map((item) => {
            const tests = coverage.get(item.id) || [];
            return `
              <label class="check-item filterable" id="${anchorFor("checklist", item.id)}" data-kind="checklist" data-unmapped="${tests.length ? "false" : "true"}" data-id="${text(item.id)}" data-search="${text([item.id, item.description, tests.map((testCase) => testCase.name).join(" ")].join(" "))}">
                <input type="checkbox" aria-label="Mark ${text(item.id)} complete">
                <span>
                  <span class="check-head">
                    <span class="id-badge">${text(item.id)}</span>
                    <span class="tag green">CHECKLIST</span>
                    ${tests.length ? "" : `<span class="tag amber">UNMAPPED</span>`}
                  </span>
                  <span class="check-body">${inlineMarkdown(item.description || "")}</span>
                  <span class="check-meta chip-row">${linkedTestChips(tests)}</span>
                </span>
              </label>`;
          }).join("") || `<div class="panel empty">No checklist items.</div>`}
        </div>
      </section>`;
  }

  function renderTests(moduleData) {
    return `
      <section id="tests">
        <h2>Test Cases</h2>
        <div class="content-stack">
          ${(moduleData.test || []).map((testCase) => `
            <details class="test-case filterable" id="${anchorFor("test", testCase.name)}" data-kind="tests" data-search="${text([testCase.name, testCase.description, testCase.command, (testCase.checklist || []).join(" ")].join(" "))}" open>
              <summary>
                <strong>${text(testCase.name)}</strong>
                <div class="small muted">${linkedChecklistChips(testCase.checklist || [])}</div>
              </summary>
              <div class="test-body">
                <p>${inlineMarkdown(testCase.description || "")}</p>
                <div class="command-row">
                  <code>${text(testCase.command || "")}</code>
                  <button class="copy-btn" type="button" data-copy="${text(testCase.command || "")}">Copy</button>
                </div>
              </div>
            </details>
          `).join("") || `<div class="panel empty">No test cases.</div>`}
        </div>
      </section>`;
  }

  async function loadHome() {
    const embedded = readEmbeddedJson("readme-home-data");
    if (embedded && !embedded.error) {
      renderHome(embedded);
      return;
    }
    const manifestPath = document.body.dataset.manifestJson || "readme-manifest.json";
    const manifest = await fetchJson(manifestPath);
    const modules = await Promise.all((manifest.modules || []).map((moduleRef) => fetchJson(moduleRef.path)));
    renderHome({ manifest, modules });
  }

  async function loadModule() {
    const embedded = readEmbeddedJson("readme-module-data");
    if (embedded && !embedded.error) {
      renderModulePage(embedded);
      return;
    }
    const jsonPath = document.body.dataset.moduleJson || "readme.json";
    renderModulePage(await fetchJson(jsonPath));
  }

  function renderError(error) {
    $("#app").innerHTML = `
      <main class="wrap">
        <section class="panel danger">
          <h1>Unable to render E2E readme page</h1>
          <p>${text(error.message || error)}</p>
          <p class="muted">Open this page through a local HTTP server if the browser blocks local JSON loading from <code>file://</code>.</p>
        </section>
      </main>`;
  }

  document.addEventListener("DOMContentLoaded", () => {
    const load = pageKind === "module" ? loadModule : loadHome;
    load().catch(renderError);
  });
})();
