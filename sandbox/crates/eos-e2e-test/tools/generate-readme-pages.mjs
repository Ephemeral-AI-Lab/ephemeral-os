#!/usr/bin/env node

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const crateRoot = path.resolve(scriptDir, "..");
const testsRoot = path.join(crateRoot, "tests");
const cargoToml = path.join(crateRoot, "Cargo.toml");

function extractSection(markdown, heading) {
  const marker = `## ${heading}`;
  const start = markdown.indexOf(marker);
  if (start < 0) return "";
  const contentStart = start + marker.length;
  const next = markdown.indexOf("\n## ", contentStart);
  return markdown
    .slice(contentStart, next < 0 ? markdown.length : next)
    .trim();
}

function splitMarkdownRow(row) {
  let source = row.trim();
  if (source.startsWith("|")) source = source.slice(1);
  if (source.endsWith("|")) source = source.slice(0, -1);

  const cells = [];
  let cell = "";
  let inCode = false;
  for (const char of source) {
    if (char === "`") {
      inCode = !inCode;
      cell += char;
      continue;
    }
    if (char === "|" && !inCode) {
      cells.push(cell.trim());
      cell = "";
      continue;
    }
    cell += char;
  }
  cells.push(cell.trim());
  return cells;
}

function codeTokens(value) {
  return Array.from(String(value ?? "").matchAll(/`([^`]+)`/g), (match) => match[1]);
}

function stripWrappingCode(value) {
  const trimmed = String(value ?? "").trim();
  if (trimmed.startsWith("`") && trimmed.endsWith("`")) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function titleFromId(id, moduleName) {
  const normalizedPrefix = `${moduleName}-`;
  const withoutPrefix = id.startsWith(normalizedPrefix) ? id.slice(normalizedPrefix.length) : id;
  return withoutPrefix
    .split(/[-_]+/g)
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function parseModuleConfig(overview) {
  const patterns = [
    /Module config:\s*`([^`]+)`/i,
    /module config is\s*`([^`]+)`/i,
    /module config:\s*`([^`]+)`/i
  ];
  for (const pattern of patterns) {
    const match = overview.match(pattern);
    if (match) return match[1];
  }
  return null;
}

function parseChecklist(markdown, moduleName) {
  return extractSection(markdown, "Checklist")
    .split(/\r?\n/)
    .map((line) => line.match(/^- \[ \] ([^:]+):\s*(.+)$/))
    .filter(Boolean)
    .map((match) => ({
      id: match[1].trim(),
      title: titleFromId(match[1].trim(), moduleName),
      description: match[2].trim(),
      tags: [moduleName]
    }));
}

function parseTestCases(markdown) {
  return extractSection(markdown, "Test Case")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("|") && !line.includes("---"))
    .slice(1)
    .map((line) => splitMarkdownRow(line))
    .filter((cells) => cells.length >= 4)
    .map((cells) => {
      const name = stripWrappingCode(cells[0]);
      return {
        id: name,
        name,
        description: cells[1].trim(),
        command: stripWrappingCode(cells[2]),
        checklist: codeTokens(cells[3])
      };
    });
}

function inferOverview(markdown) {
  const overview = extractSection(markdown, "Overview").replace(/\s+/g, " ").trim();
  const ops = Array.from(new Set(
    codeTokens(overview).filter((token) => token.startsWith("api.") || token.startsWith("plugin."))
  ));
  return {
    summary: overview,
    moduleConfig: parseModuleConfig(overview),
    ops,
    tags: []
  };
}

function parseReadme(markdown, testEntry) {
  const heading = markdown.match(/^#\s+(.+)$/m);
  const name = heading ? heading[1].trim() : testEntry.name;
  const overview = inferOverview(markdown);
  overview.tags = Array.from(new Set([name, ...name.split(/[-_]+/g).filter(Boolean)]));
  return {
    schemaVersion: 1,
    name,
    displayName: name,
    cargoTestName: testEntry.name,
    folder: testEntry.folder,
    path: testEntry.path,
    page: `${testEntry.folder}/index.html`,
    source: {
      markdown: "readme.md"
    },
    overview,
    checklist: parseChecklist(markdown, name),
    test: parseTestCases(markdown)
  };
}

function parseCargoTests(cargo) {
  return cargo
    .split(/\n\[\[test\]\]\n/g)
    .slice(1)
    .map((block) => {
      const name = block.match(/^name\s*=\s*"([^"]+)"/m)?.[1];
      const testPath = block.match(/^path\s*=\s*"([^"]+)"/m)?.[1];
      if (!name || !testPath) return null;
      const folder = testPath.match(/^tests\/([^/]+)\/mod\.rs$/)?.[1];
      if (!folder) return null;
      return { name, path: testPath, folder };
    })
    .filter(Boolean);
}

function scriptJson(data) {
  return JSON.stringify(data, null, 2).replaceAll("<", "\\u003c");
}

function htmlShell({ title, cssPath, jsPath, bodyAttrs, embeddedId, embeddedData }) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<link rel="stylesheet" href="${cssPath}">
</head>
<body ${bodyAttrs}>
<div id="app"></div>
<script type="application/json" id="${embeddedId}">
${scriptJson(embeddedData)}
</script>
<script src="${jsPath}"></script>
</body>
</html>
`;
}

async function main() {
  const cargo = await readFile(cargoToml, "utf8");
  const testEntries = parseCargoTests(cargo);
  const modules = [];

  for (const entry of testEntries) {
    const moduleDir = path.join(testsRoot, entry.folder);
    const markdownPath = path.join(moduleDir, "readme.md");
    const markdown = await readFile(markdownPath, "utf8");
    const moduleData = parseReadme(markdown, entry);
    modules.push(moduleData);

    await writeFile(
      path.join(moduleDir, "readme.json"),
      `${JSON.stringify(moduleData, null, 2)}\n`
    );
    await writeFile(
      path.join(moduleDir, "index.html"),
      htmlShell({
        title: `${moduleData.displayName} - eos-e2e-test`,
        cssPath: "../assets/e2e-readme.css",
        jsPath: "../assets/e2e-readme.js",
        bodyAttrs: 'data-page="module" data-module-json="readme.json"',
        embeddedId: "readme-module-data",
        embeddedData: moduleData
      })
    );
  }

  const manifest = {
    schemaVersion: 1,
    source: {
      cargoToml: "../Cargo.toml",
      moduleReadmes: "*/readme.md"
    },
    modules: modules.map((moduleData) => ({
      name: moduleData.name,
      displayName: moduleData.displayName,
      cargoTestName: moduleData.cargoTestName,
      path: `${moduleData.folder}/readme.json`,
      page: `${moduleData.folder}/index.html`
    }))
  };

  await mkdir(path.join(testsRoot, "assets"), { recursive: true });
  await writeFile(path.join(testsRoot, "readme-manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  await writeFile(
    path.join(testsRoot, "index.html"),
    htmlShell({
      title: "eos-e2e-test Readme Index",
      cssPath: "assets/e2e-readme.css",
      jsPath: "assets/e2e-readme.js",
      bodyAttrs: 'data-page="home" data-manifest-json="readme-manifest.json"',
      embeddedId: "readme-home-data",
      embeddedData: { manifest, modules }
    })
  );

  console.log(`Generated ${modules.length} module readme pages under ${testsRoot}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
