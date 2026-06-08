const filterInput = document.querySelector("#filter");
const kindFilter = document.querySelector("#kind-filter");
const root = document.querySelector("#filter-root");
const refreshButton = document.querySelector("#refresh-inventory");
const refreshStatus = document.querySelector("#refresh-status");
const refreshEndpoint = "/__class_inventory_refresh";
const globalResults = document.createElement("section");
globalResults.id = "global-results";
globalResults.className = "global-results";
globalResults.hidden = true;
document.querySelector(".toolbar")?.after(globalResults);

if (refreshButton && window.location.protocol === "file:") {
  refreshButton.textContent = "Copy refresh command";
  refreshButton.title = "Local files cannot run commands. Copy the command, run it in a terminal, then reload this page.";
}

function applyFilter() {
  if (!root) return;
  const query = (filterInput?.value || "").trim().toLowerCase();
  const kind = kindFilter?.value || "";
  renderGlobalResults(query);
  const richCards = root.querySelectorAll(".crate-card, .item-card, .type-card");
  richCards.forEach((card) => {
    const text = (card.dataset.filterText || card.textContent || "").toLowerCase();
    const kindList = card.dataset.kindList || card.dataset.kind || "";
    const kindMatch = !kind || kindList.split(/\s+/).includes(kind);
    card.classList.toggle("is-hidden", Boolean(query && !text.includes(query)) || !kindMatch);
  });
  root.querySelectorAll(".module-section").forEach((section) => {
    const visible = section.querySelector(".item-card:not(.is-hidden), .type-card:not(.is-hidden)");
    const sectionText = (section.dataset.filterText || section.textContent || "").toLowerCase();
    section.classList.toggle("is-hidden", Boolean(query && !sectionText.includes(query) && !visible));
  });
}

filterInput?.addEventListener("input", applyFilter);
kindFilter?.addEventListener("change", applyFilter);

function renderGlobalResults(query) {
  if (!globalResults) return;
  if (!query || query.length < 2 || !window.CLASS_INVENTORY) {
    globalResults.hidden = true;
    globalResults.innerHTML = "";
    return;
  }
  const terms = query.split(/\s+/).filter(Boolean);
  const matches = symbolIndex()
    .map((entry) => ({ entry, score: scoreEntry(entry, terms) }))
    .filter((match) => match.score > 0)
    .sort((a, b) => b.score - a.score || a.entry.name.localeCompare(b.entry.name))
    .slice(0, 40);
  if (!matches.length) {
    globalResults.hidden = false;
    globalResults.innerHTML = `<div class="global-results-head"><span>All-symbol search</span><span>No matches</span></div>`;
    return;
  }
  const rows = matches
    .map(({ entry }) => `<a href="${escapeAttr(entry.href)}"><span class="result-kind">${escapeHtml(entry.kind)}</span><span class="result-main"><strong>${escapeHtml(entry.name)}</strong><small>${escapeHtml(entry.detail)}</small></span><span class="result-path">${escapeHtml(entry.path)}</span></a>`)
    .join("");
  globalResults.hidden = false;
  globalResults.innerHTML = `<div class="global-results-head"><span>All-symbol search</span><span>${matches.length} matches</span></div>${rows}`;
}

let cachedSymbolIndex;

function symbolIndex() {
  if (cachedSymbolIndex) return cachedSymbolIndex;
  const inventory = window.CLASS_INVENTORY;
  const entries = [];
  const cratesPrefix = location.pathname.includes("/crates/") ? "" : "crates/";
  for (const crateInfo of inventory?.crates || []) {
    for (const moduleInfo of crateInfo.modules || []) {
      const page = `${cratesPrefix}${crateFilePageName(crateInfo, moduleInfo)}`;
      const filePath = moduleInfo.path || "";
      entries.push({
        kind: "file",
        name: displayFilePath(crateInfo.name, filePath),
        detail: `${crateInfo.name} module ${moduleInfo.module}`,
        path: filePath,
        href: page,
        search: `${crateInfo.name} ${moduleInfo.module} ${filePath}`,
      });
      for (const item of moduleInfo.items || []) {
        const itemHref = `${page}#${itemAnchor(item.kind, item.name, item.line)}`;
        entries.push({
          kind: item.kind,
          name: item.name,
          detail: item.signature || "",
          path: filePath,
          href: itemHref,
          search: `${crateInfo.name} ${moduleInfo.module} ${filePath} ${item.kind} ${item.name} ${item.signature || ""} ${item.docs || ""}`,
        });
        for (const field of item.fields || []) {
          entries.push({
            kind: "field",
            name: `${item.name}.${field.name}`,
            detail: `${field.name}: ${field.ty}`,
            path: filePath,
            href: itemHref,
            search: `${crateInfo.name} ${moduleInfo.module} ${filePath} field ${item.name} ${field.name} ${field.ty}`,
          });
        }
        for (const variant of item.variants || []) {
          entries.push({
            kind: "variant",
            name: `${item.name}::${variant.name}`,
            detail: item.name,
            path: filePath,
            href: itemHref,
            search: `${crateInfo.name} ${moduleInfo.module} ${filePath} variant ${item.name} ${variant.name}`,
          });
        }
        for (const method of item.methods || []) {
          entries.push({
            kind: method.kind || "method",
            name: `${ownerName(item)}.${method.name}`,
            detail: method.signature || "",
            path: filePath,
            href: `${page}#${methodAnchor(method.name, method.line)}`,
            search: `${crateInfo.name} ${moduleInfo.module} ${filePath} method ${ownerName(item)} ${method.name} ${method.signature || ""} ${method.docs || ""}`,
          });
        }
      }
    }
  }
  cachedSymbolIndex = entries;
  return entries;
}

function scoreEntry(entry, terms) {
  const search = entry.search.toLowerCase();
  let score = 0;
  for (const term of terms) {
    const name = entry.name.toLowerCase();
    if (name === term) score += 100;
    else if (name.startsWith(term)) score += 60;
    else if (name.includes(term)) score += 35;
    else if (search.includes(term)) score += 10;
    else return 0;
  }
  if (["struct", "enum", "trait"].includes(entry.kind)) score += 8;
  if (entry.kind === "method" || entry.kind === "constructor") score += 4;
  return score;
}

function crateFilePageName(crateInfo, moduleInfo) {
  const first = crateInfo.modules?.[0];
  if (!first || moduleInfo.path === first.path) return `${crateInfo.name}.html`;
  return `${crateInfo.name}--${slug(displayFilePath(crateInfo.name, moduleInfo.path))}.html`;
}

function displayFilePath(crateName, filePath) {
  const prefix = `crates/${crateName}/`;
  return filePath.startsWith(prefix) ? filePath.slice(prefix.length) : filePath;
}

function itemAnchor(kind, name, line) {
  return `item-${slug(kind)}-${slug(name)}-${line}`;
}

function methodAnchor(name, line) {
  return `method-${slug(name)}-${line}`;
}

function slug(value) {
  return String(value || "").split("").map((ch) => /[A-Za-z0-9]/.test(ch) ? ch : "-").join("");
}

function ownerName(item) {
  return (item.impl_target || item.name || "").split("<")[0].split("::").pop().trim() || item.name;
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value);
}

refreshButton?.addEventListener("click", async () => {
  if (window.location.protocol === "file:") {
    const command = refreshButton.dataset.refreshCommand || "";
    try {
      await navigator.clipboard.writeText(command);
      if (refreshStatus) refreshStatus.textContent = "Copied command; run it, then reload this page";
    } catch (error) {
      window.prompt("Copy and run this command, then reload this HTML file:", command);
      if (refreshStatus) refreshStatus.textContent = "Run command, then reload this page";
    }
    return;
  }
  refreshButton.disabled = true;
  if (refreshStatus) refreshStatus.textContent = "Refreshing";
  try {
    const response = await fetch(refreshEndpoint, { method: "POST" });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.ok === false) {
      throw new Error(result.error || `refresh failed (${response.status})`);
    }
    if (refreshStatus) refreshStatus.textContent = "Reloading";
    window.location.reload();
  } catch (error) {
    if (refreshStatus) refreshStatus.textContent = "Refresh unavailable";
    console.error(error);
    refreshButton.disabled = false;
  }
});
