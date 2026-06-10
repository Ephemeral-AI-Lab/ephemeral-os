import * as fs from "node:fs/promises";
import * as path from "node:path";

import type {
  FieldInventory,
  MethodInventory,
  ModuleInventory,
  PackageInventory,
  Relation,
  SymbolInventory,
  VariantInventory,
  WorkspaceInventory,
} from "./types.js";

interface FileTreeNode {
  name: string;
  folders: Map<string, FileTreeNode>;
  modules: ModuleInventory[];
}

export async function writeInventory(
  workspaceRoot: string,
  inventory: WorkspaceInventory,
): Promise<void> {
  const outRoot = path.join(workspaceRoot, "docs/code-inventory");
  const htmlRoot = path.join(outRoot, "html");
  await fs.mkdir(outRoot, { recursive: true });
  await fs.rm(htmlRoot, { recursive: true, force: true });
  await fs.mkdir(path.join(htmlRoot, "assets"), { recursive: true });
  await fs.mkdir(path.join(htmlRoot, "packages"), { recursive: true });
  await fs.mkdir(path.join(htmlRoot, "modules"), { recursive: true });
  const writeText = (target: string, contents: string): Promise<void> =>
    fs.writeFile(target, stripTrailingWhitespace(contents));

  const inventoryJson = `${JSON.stringify(inventory, null, 2)}\n`;
  const graph = graphData(inventory);
  await writeText(path.join(outRoot, "inventory.json"), inventoryJson);
  await writeText(path.join(outRoot, "graph.json"), `${JSON.stringify(graph, null, 2)}\n`);
  await writeText(path.join(htmlRoot, "assets/inventory.json"), inventoryJson);
  await writeText(
    path.join(htmlRoot, "assets/graph.json"),
    `${JSON.stringify(graph, null, 2)}\n`,
  );
  await writeText(
    path.join(htmlRoot, "assets/inventory-data.js"),
    `/* global window */\nwindow.EOS_CODE_INVENTORY = ${JSON.stringify(inventory)};\n`,
  );
  await writeText(path.join(htmlRoot, "assets/inventory.css"), css());
  await writeText(path.join(htmlRoot, "assets/inventory.js"), js());
  await writeText(path.join(htmlRoot, "index.html"), indexPage(inventory));

  for (const pkg of inventory.packages) {
    await writeText(
      path.join(htmlRoot, "packages", `${packageSlug(pkg.name)}.html`),
      packagePage(inventory, pkg),
    );
    for (const module of pkg.modules) {
      await writeText(
        path.join(htmlRoot, "modules", `${moduleSlug(module.id)}.html`),
        modulePage(pkg, module, inventory.relations),
      );
    }
  }
}

function stripTrailingWhitespace(contents: string): string {
  return contents.replace(/[ \t]+$/gm, "");
}

function graphData(inventory: WorkspaceInventory): {
  nodes: { id: string; kind: "package" | "module" | "symbol"; tags: string[] }[];
  relations: Relation[];
} {
  const nodes = inventory.packages.flatMap((pkg) => [
    { id: pkg.name, kind: "package" as const, tags: pkg.tags },
    ...pkg.modules.flatMap((module) => [
      { id: module.id, kind: "module" as const, tags: module.tags },
      ...module.symbols.map((symbol) => ({
        id: symbol.id,
        kind: "symbol" as const,
        tags: symbol.tags,
      })),
    ]),
  ]);
  return { nodes, relations: inventory.relations };
}

function indexPage(inventory: WorkspaceInventory): string {
  const packageCards = inventory.packages.map(packageCard).join("\n");
  const codeTree = workspaceFileTree(inventory);
  const relationRows = inventory.relations.slice(0, 240).map(relationRow).join("\n");

  return shell({
    title: "eos-agent-core Code Inventory",
    assetPrefix: "assets/",
    body: `
      <section class="page-head">
        <div>
          <p class="eyebrow">eos-agent-core</p>
          <h1>TypeScript Code Inventory</h1>
          <p class="lede">Package boundaries, module surfaces, symbols, Zod contracts, and graph relations generated from the live TypeScript workspace.</p>
        </div>
        <dl class="stat-grid">
          ${stat("Packages", inventory.stats.packages)}
          ${stat("Modules", inventory.stats.modules)}
          ${stat("Symbols", inventory.stats.symbols)}
          ${stat("Relations", inventory.stats.relations)}
        </dl>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Packages</h2>
          <span>${escapeHtml(inventory.packageManager ?? "package manager unknown")}</span>
        </div>
        <div class="package-grid">${packageCards}</div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Code Tree</h2>
          <input class="search-input" type="search" placeholder="Filter files, symbols, tags" aria-label="Filter files, symbols, tags">
        </div>
        ${codeTree}
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Graph Relations</h2>
          <span>showing ${count(Math.min(inventory.relations.length, 240))} of ${count(inventory.relations.length)}</span>
        </div>
        <div class="table-wrap relation-table">
          <table>
            <thead><tr><th>Kind</th><th>From</th><th>To</th><th>Line</th></tr></thead>
            <tbody>${relationRows}</tbody>
          </table>
        </div>
      </section>
    `,
  });
}

function packagePage(inventory: WorkspaceInventory, pkg: PackageInventory): string {
  const codeTree = packageFileTree(pkg, "../");
  const relations = inventory.relations
    .filter((relation) => relation.from === pkg.name || relation.from.startsWith(`${pkg.name}/`))
    .slice(0, 240)
    .map(relationRow)
    .join("\n");

  return shell({
    title: `${pkg.name} Code Inventory`,
    assetPrefix: "../assets/",
    body: `
      <section class="page-head">
        <div>
          <p class="eyebrow"><a href="../index.html">Code Inventory</a></p>
          <h1>${escapeHtml(pkg.name)}</h1>
          <p class="lede">${escapeHtml(pkg.path)}</p>
          ${tags(pkg.tags)}
        </div>
        <dl class="stat-grid">
          ${stat("Modules", pkg.stats.modules)}
          ${stat("Source", pkg.stats.sourceModules)}
          ${stat("Tests", pkg.stats.testModules)}
          ${stat("Symbols", pkg.stats.symbols)}
        </dl>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Package Manifest</h2>
          <span>${escapeHtml(pkg.packageJson.type ?? "type unspecified")}</span>
        </div>
        <div class="manifest-grid">
          <div><strong>exports</strong>${list(pkg.packageJson.exports)}</div>
          <div><strong>dependencies</strong>${list(pkg.packageJson.dependencies)}</div>
          <div><strong>devDependencies</strong>${list(pkg.packageJson.devDependencies)}</div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>File Tree</h2>
          <input class="search-input" type="search" placeholder="Filter files, symbols, tags" aria-label="Filter files, symbols, tags">
        </div>
        ${codeTree}
      </section>

      <section class="panel">
        <div class="panel-head"><h2>Relations</h2><span>${count(pkg.stats.relations)}</span></div>
        <div class="table-wrap relation-table">
          <table>
            <thead><tr><th>Kind</th><th>From</th><th>To</th><th>Line</th></tr></thead>
            <tbody>${relations}</tbody>
          </table>
        </div>
      </section>
    `,
  });
}

function modulePage(
  pkg: PackageInventory,
  module: ModuleInventory,
  relations: readonly Relation[],
): string {
  const importRows = module.imports.map((edge) => `
    <tr class="search-item">
      <td><code>${escapeHtml(edge.source)}</code></td>
      <td>${escapeHtml(edge.imported.join(", "))}</td>
      <td>${edge.typeOnly ? "type" : "value"}</td>
      <td>${count(edge.line)}</td>
    </tr>
  `).join("\n");
  const exportRows = module.exports.map((edge) => `
    <tr class="search-item">
      <td><code>${escapeHtml(edge.target ?? module.id)}</code></td>
      <td>${escapeHtml(edge.exported.join(", "))}</td>
      <td>${edge.typeOnly ? "type" : "value"}</td>
      <td>${count(edge.line)}</td>
    </tr>
  `).join("\n");
  const symbolCards = module.symbols.map((symbol) => symbolCard(symbol, module)).join("\n");
  const symbolOutline = moduleSymbolOutline(module);
  const relationRows = relations
    .filter((relation) => relation.from === module.id || relation.from.startsWith(`${module.id}#`))
    .map(relationRow)
    .join("\n");

  return shell({
    title: `${module.id} Code Inventory`,
    assetPrefix: "../assets/",
    body: `
      <section class="page-head">
        <div>
          <p class="eyebrow"><a href="../index.html">Code Inventory</a> / <a href="../packages/${packageSlug(pkg.name)}.html">${escapeHtml(pkg.name)}</a></p>
          <h1>${escapeHtml(module.id)}</h1>
          <p class="lede">${escapeHtml(module.path)}</p>
          ${tags(module.tags)}
        </div>
        <dl class="stat-grid">
          ${stat("Symbols", module.stats.symbols)}
          ${stat("Schemas", module.stats.schemas)}
          ${stat("Types", module.stats.types)}
          ${stat("Functions", module.stats.functions)}
        </dl>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Imports</h2>
          <span>${count(module.imports.length)}</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Source</th><th>Names</th><th>Mode</th><th>Line</th></tr></thead>
            <tbody>${importRows}</tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Exports</h2>
          <span>${count(module.exports.length)}</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Target</th><th>Names</th><th>Mode</th><th>Line</th></tr></thead>
            <tbody>${exportRows}</tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Symbol Outline</h2>
          <input class="search-input" type="search" placeholder="Filter symbols" aria-label="Filter symbols">
        </div>
        <div class="module-symbol-layout">
          ${symbolOutline}
          <div class="symbol-stack">${symbolCards}</div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head"><h2>Relations</h2></div>
        <div class="table-wrap relation-table">
          <table>
            <thead><tr><th>Kind</th><th>From</th><th>To</th><th>Line</th></tr></thead>
            <tbody>${relationRows}</tbody>
          </table>
        </div>
      </section>
    `,
  });
}

function shell(input: {
  title: string;
  assetPrefix: string;
  body: string;
}): string {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(input.title)}</title>
  <link rel="stylesheet" href="${input.assetPrefix}inventory.css">
  <script defer src="${input.assetPrefix}inventory-data.js"></script>
  <script defer src="${input.assetPrefix}inventory.js"></script>
</head>
<body>
  <header class="topbar">
    <a class="brand" href="${input.assetPrefix === "assets/" ? "index.html" : "../index.html"}">eos-agent-core</a>
    <nav>
      <a href="${input.assetPrefix}inventory.json">JSON</a>
      <a href="${input.assetPrefix}graph.json">Graph</a>
    </nav>
  </header>
  <main>
    ${input.body}
  </main>
</body>
</html>
`;
}

function packageCard(pkg: PackageInventory): string {
  return `
    <a class="package-card search-item" href="packages/${packageSlug(pkg.name)}.html">
      <strong>${escapeHtml(pkg.name)}</strong>
      <span>${escapeHtml(pkg.path)}</span>
      <small>${count(pkg.stats.modules)} modules · ${count(pkg.stats.symbols)} symbols · ${count(pkg.stats.relations)} relations</small>
      ${tags(pkg.tags)}
    </a>
  `;
}

function workspaceFileTree(inventory: WorkspaceInventory): string {
  return `
    <div class="tree-nav">
      ${inventory.packages.map((pkg) => `
        <details class="tree-folder tree-package" open>
          <summary>
            <span class="tree-label">${escapeHtml(pkg.name)}</span>
            <a class="tree-action" href="packages/${packageSlug(pkg.name)}.html">Open</a>
            <small>${count(pkg.stats.modules)} modules · ${count(pkg.stats.symbols)} symbols</small>
          </summary>
          ${packageFileTree(pkg, "")}
        </details>
      `).join("\n")}
    </div>
  `;
}

function packageFileTree(pkg: PackageInventory, linkPrefix: string): string {
  const root = buildFileTree(pkg);
  if (pkg.modules.length === 0) {
    return `<div class="tree-empty">No TypeScript source files found.</div>`;
  }
  return `<div class="tree-nav">${renderTreeNode(root, linkPrefix)}</div>`;
}

function buildFileTree(pkg: PackageInventory): FileTreeNode {
  const root = treeNode("");
  for (const module of pkg.modules) {
    const relativeModulePath = module.path.startsWith(`${pkg.path}/`)
      ? module.path.slice(pkg.path.length + 1)
      : module.path;
    insertModule(root, relativeModulePath.split("/"), module);
  }
  return root;
}

function insertModule(
  node: FileTreeNode,
  parts: readonly string[],
  module: ModuleInventory,
): void {
  const head = parts.at(0);
  if (head === undefined) {
    return;
  }
  const tail = parts.slice(1);
  if (tail.length === 0) {
    node.modules.push(module);
    node.modules.sort((left, right) => left.path.localeCompare(right.path));
    return;
  }
  const child = node.folders.get(head) ?? treeNode(head);
  node.folders.set(head, child);
  insertModule(child, tail, module);
}

function treeNode(name: string): FileTreeNode {
  return { name, folders: new Map(), modules: [] };
}

function renderTreeNode(node: FileTreeNode, linkPrefix: string): string {
  const folders = [...node.folders.values()]
    .sort((left, right) => left.name.localeCompare(right.name))
    .map((folder) => `
      <details class="tree-folder" open>
        <summary>
          <span class="tree-label">${escapeHtml(folder.name)}</span>
        </summary>
        ${renderTreeNode(folder, linkPrefix)}
      </details>
    `)
    .join("\n");
  const files = node.modules.map((module) => moduleTreeFile(module, linkPrefix)).join("\n");
  return `<div class="tree-children">${folders}${files}</div>`;
}

function moduleTreeFile(module: ModuleInventory, linkPrefix: string): string {
  return `
    <div class="tree-file search-item">
      <div class="tree-file-row">
        <a href="${linkPrefix}modules/${moduleSlug(module.id)}.html"><code>${escapeHtml(fileName(module.path))}</code></a>
        <small>${escapeHtml(module.kind)} · ${count(module.stats.symbols)} symbols</small>
      </div>
      ${tags(module.tags)}
      ${symbolTree(module, linkPrefix)}
    </div>
  `;
}

function symbolTree(module: ModuleInventory, linkPrefix: string): string {
  if (module.symbols.length === 0) {
    return "";
  }
  return `
    <ul class="tree-symbols">
      ${module.symbols.map((symbol) => `
        <li class="tree-symbol search-item">
          <a href="${linkPrefix}modules/${moduleSlug(module.id)}.html#${anchor(symbol.name)}"><code>${escapeHtml(symbol.name)}</code></a>
          <span>${escapeHtml(symbol.kind)}</span>
          ${compactTags(symbol.tags)}
        </li>
      `).join("\n")}
    </ul>
  `;
}

function moduleSymbolOutline(module: ModuleInventory): string {
  if (module.symbols.length === 0) {
    return `<nav class="symbol-outline"><div class="tree-empty">No top-level symbols found.</div></nav>`;
  }
  return `
    <nav class="symbol-outline">
      <div class="tree-file-row">
        <code>${escapeHtml(fileName(module.path))}</code>
        <small>${count(module.symbols.length)} symbols</small>
      </div>
      <ul class="tree-symbols">
        ${module.symbols.map((symbol) => `
          <li class="tree-symbol search-item">
            <a href="#${anchor(symbol.name)}"><code>${escapeHtml(symbol.name)}</code></a>
            <span>${escapeHtml(symbol.kind)}</span>
            ${compactTags(symbol.tags)}
          </li>
        `).join("\n")}
      </ul>
    </nav>
  `;
}

function symbolCard(symbol: SymbolInventory, module: ModuleInventory): string {
  const docs = symbol.docs === undefined ? "" : `<p>${escapeHtml(symbol.docs)}</p>`;
  const fields = symbol.fields.length === 0 ? "" : `
    <h3>Fields</h3>
    ${fieldTable(symbol.fields)}
  `;
  const methods = symbol.methods.length === 0 ? "" : `
    <h3>Methods</h3>
    ${methodTable(symbol.methods)}
  `;
  const variants = symbol.variants.length === 0 ? "" : `
    <h3>Variants</h3>
    ${variantList(symbol.variants)}
  `;
  const heritage = [...symbol.extends, ...symbol.implements].length === 0 ? "" : `
    <h3>Heritage</h3>
    <p>${escapeHtml([...symbol.extends, ...symbol.implements].join(", "))}</p>
  `;
  return `
    <article id="${anchor(symbol.name)}" class="symbol-card search-item">
      <div class="symbol-head">
        <div>
          <strong>${escapeHtml(symbol.name)}</strong>
          <span>${escapeHtml(symbol.kind)} · line ${count(symbol.line)} · ${escapeHtml(symbol.visibility)}</span>
        </div>
        <a href="../modules/${moduleSlug(module.id)}.html#${anchor(symbol.name)}">Module</a>
      </div>
      <pre><code>${escapeHtml(symbol.signature)}</code></pre>
      ${docs}
      ${tags(symbol.tags)}
      ${heritage}
      ${fields}
      ${methods}
      ${variants}
    </article>
  `;
}

function relationRow(relation: Relation): string {
  return `
    <tr class="search-item">
      <td>${escapeHtml(relation.kind)}${relation.typeOnly === true ? " <small>type</small>" : ""}</td>
      <td><code>${escapeHtml(relation.from)}</code></td>
      <td><code>${escapeHtml(relation.to)}</code></td>
      <td>${relation.line === undefined ? "" : count(relation.line)}</td>
    </tr>
  `;
}

function fieldTable(fields: readonly FieldInventory[]): string {
  return `
    <div class="table-wrap compact">
      <table>
        <thead><tr><th>Name</th><th>Type</th><th>Flags</th></tr></thead>
        <tbody>
          ${fields.map((field) => `
            <tr>
              <td><code>${escapeHtml(field.name)}</code></td>
              <td><code>${escapeHtml(field.ty)}</code></td>
              <td>${[field.optional ? "optional" : "", field.readonly ? "readonly" : ""].filter(Boolean).join(" ")}</td>
            </tr>
          `).join("\n")}
        </tbody>
      </table>
    </div>
  `;
}

function methodTable(methods: readonly MethodInventory[]): string {
  return `
    <div class="table-wrap compact">
      <table>
        <thead><tr><th>Name</th><th>Signature</th><th>Flags</th></tr></thead>
        <tbody>
          ${methods.map((method) => `
            <tr>
              <td><code>${escapeHtml(method.name)}</code></td>
              <td><code>${escapeHtml(method.signature)}</code></td>
              <td>${[method.async ? "async" : "", method.static ? "static" : ""].filter(Boolean).join(" ")}</td>
            </tr>
          `).join("\n")}
        </tbody>
      </table>
    </div>
  `;
}

function variantList(variants: readonly VariantInventory[]): string {
  return `
    <ul class="variant-list">
      ${variants.map((variant) => `
        <li><code>${escapeHtml(variant.name)}</code>${variant.value === undefined ? "" : ` = <code>${escapeHtml(variant.value)}</code>`}</li>
      `).join("\n")}
    </ul>
  `;
}

function stat(label: string, value: number): string {
  return `<div><dt>${escapeHtml(label)}</dt><dd>${count(value)}</dd></div>`;
}

function count(value: number): string {
  return value.toLocaleString("en-US");
}

function tags(values: readonly string[]): string {
  if (values.length === 0) {
    return "";
  }
  return `<div class="tags">${values.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>`;
}

function compactTags(values: readonly string[]): string {
  const visible = values.filter((value) =>
    [
      "public-api",
      "internal",
      "schema:zod",
      "contract",
      "provider-client",
      "provider-wire",
      "test-only",
      "config",
      "event",
      "run-handle",
    ].includes(value),
  );
  if (visible.length === 0) {
    return "";
  }
  return `<span class="compact-tags">${visible.map(escapeHtml).join(" ")}</span>`;
}

function list(values: readonly string[]): string {
  if (values.length === 0) {
    return "<p>none</p>";
  }
  return `<ul>${values.map((value) => `<li><code>${escapeHtml(value)}</code></li>`).join("")}</ul>`;
}

function packageSlug(name: string): string {
  return name.replace(/^@/, "").replace(/[^a-zA-Z0-9]+/g, "-");
}

function fileName(filePath: string): string {
  return filePath.split("/").at(-1) ?? filePath;
}

function moduleSlug(id: string): string {
  return id.replace(/^@/, "").replace(/[^a-zA-Z0-9]+/g, "-");
}

function anchor(name: string): string {
  return name.replace(/[^a-zA-Z0-9_-]+/g, "-");
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function css(): string {
  return `:root {
  color-scheme: light;
  --bg: #f8fafc;
  --panel: #ffffff;
  --ink: #111827;
  --muted: #64748b;
  --line: #d7dee8;
  --accent: #2563eb;
  --accent-strong: #1e40af;
  --tag-bg: #edf2f7;
  --tag-ink: #334155;
  --code-bg: #f1f5f9;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 16px;
  line-height: 1.5;
}

a {
  color: var(--accent);
  text-decoration: none;
}

a:hover,
a:focus-visible {
  color: var(--accent-strong);
  text-decoration: underline;
}

a:focus-visible,
input:focus-visible {
  outline: 3px solid #93c5fd;
  outline-offset: 2px;
}

code,
pre {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
}

pre {
  margin: 12px 0;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--code-bg);
  padding: 12px;
  font-size: 13px;
}

main {
  width: min(1440px, calc(100% - 32px));
  margin: 0 auto;
  padding: 24px 0 48px;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  min-height: 56px;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--line);
  background: rgba(248, 250, 252, 0.94);
  padding: 0 24px;
  backdrop-filter: blur(10px);
}

.brand {
  color: var(--ink);
  font-weight: 700;
}

.topbar nav {
  display: flex;
  gap: 16px;
}

.page-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 520px);
  gap: 24px;
  align-items: end;
  margin: 20px 0 24px;
}

.eyebrow {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1,
h2,
h3,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 8px;
  font-size: clamp(32px, 5vw, 52px);
  line-height: 1.05;
}

h2 {
  margin-bottom: 0;
  font-size: 20px;
}

h3 {
  margin: 18px 0 8px;
  font-size: 14px;
}

.lede {
  max-width: 760px;
  margin-bottom: 0;
  color: var(--muted);
  font-size: 17px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--line);
}

.stat-grid div {
  background: var(--panel);
  padding: 14px;
}

.stat-grid dt {
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

.stat-grid dd {
  margin: 4px 0 0;
  font-size: 24px;
  font-weight: 700;
}

.panel {
  margin: 18px 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}

.panel-head {
  display: flex;
  min-height: 58px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--line);
  padding: 14px 16px;
}

.panel-head span {
  color: var(--muted);
  font-size: 13px;
}

.search-input {
  width: min(420px, 100%);
  min-height: 40px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 12px;
  font: inherit;
}

.package-grid,
.symbol-grid,
.manifest-grid {
  display: grid;
  gap: 12px;
  padding: 16px;
}

.package-grid {
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
}

.symbol-grid {
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
}

.manifest-grid {
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
}

.tree-nav {
  padding: 12px 16px 16px;
}

.tree-nav .tree-nav {
  padding: 6px 0 4px;
}

.tree-folder {
  border-left: 1px solid var(--line);
  margin-left: 8px;
  padding-left: 12px;
}

.tree-folder > summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 10px;
  align-items: center;
  min-height: 34px;
  cursor: pointer;
  list-style: none;
}

.tree-folder > summary::-webkit-details-marker {
  display: none;
}

.tree-folder > summary::before {
  content: "";
  width: 0;
  height: 0;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 6px solid var(--muted);
  transform: rotate(90deg);
}

.tree-folder:not([open]) > summary::before {
  transform: rotate(0deg);
}

.tree-folder > summary {
  grid-template-columns: 12px minmax(0, 1fr) auto auto;
}

.tree-label {
  min-width: 0;
  overflow-wrap: anywhere;
  font-weight: 700;
}

.tree-action {
  font-size: 13px;
}

.tree-folder small,
.tree-file-row small,
.tree-symbol span {
  color: var(--muted);
  font-size: 12px;
}

.tree-children {
  padding-left: 4px;
}

.tree-file {
  margin: 6px 0 8px 20px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  padding: 10px 12px;
}

.tree-file-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
}

.tree-file-row code,
.tree-symbol code {
  overflow-wrap: anywhere;
}

.tree-symbols {
  display: grid;
  gap: 4px;
  margin: 10px 0 0;
  padding: 0 0 0 18px;
}

.tree-symbol {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 220px);
  gap: 10px;
  align-items: baseline;
  min-height: 28px;
}

.compact-tags {
  color: var(--muted);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.tree-empty {
  color: var(--muted);
  padding: 14px 16px;
}

.module-symbol-layout {
  display: grid;
  grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
  gap: 16px;
  padding: 16px;
}

.symbol-outline {
  align-self: start;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  padding: 12px;
}

.symbol-stack {
  display: grid;
  gap: 12px;
}

.package-card,
.symbol-card {
  display: block;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #ffffff;
  padding: 14px;
}

.package-card:hover,
.symbol-card:hover {
  border-color: #adc4e8;
}

.package-card strong,
.symbol-card strong {
  display: block;
  color: var(--ink);
  font-size: 17px;
}

.package-card span,
.package-card small,
.symbol-card span {
  display: block;
  color: var(--muted);
  overflow-wrap: anywhere;
}

.symbol-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.tags span {
  border-radius: 999px;
  background: var(--tag-bg);
  color: var(--tag-ink);
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 600;
}

.table-wrap {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  border-bottom: 1px solid var(--line);
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
}

th {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
}

td small {
  display: block;
  color: var(--muted);
}

.compact table {
  font-size: 13px;
}

.relation-table code,
td code {
  overflow-wrap: anywhere;
}

.variant-list {
  margin: 0;
  padding-left: 20px;
}

.is-hidden {
  display: none !important;
}

@media (max-width: 900px) {
  main {
    width: min(100% - 20px, 1440px);
  }

  .topbar {
    padding: 0 12px;
  }

  .page-head {
    grid-template-columns: 1fr;
  }

  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .panel-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .module-symbol-layout {
    grid-template-columns: 1fr;
  }

  .tree-symbol {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (prefers-reduced-motion: no-preference) {
  a,
  .package-card,
  .symbol-card {
    transition: border-color 180ms ease, color 180ms ease;
  }
}
`;
}

function js(): string {
  return `/* global document */
"use strict";

function normalize(value) {
  return value.toLowerCase();
}

function setupSearch(input) {
  var panel = input.closest(".panel") || document;
  var items = Array.prototype.slice.call(panel.querySelectorAll(".search-item"));
  input.addEventListener("input", function () {
    var query = normalize(input.value.trim());
    items.forEach(function (item) {
      var text = normalize(item.textContent || "");
      item.classList.toggle("is-hidden", query.length > 0 && !text.includes(query));
    });
  });
}

document.querySelectorAll(".search-input").forEach(setupSearch);
`;
}
