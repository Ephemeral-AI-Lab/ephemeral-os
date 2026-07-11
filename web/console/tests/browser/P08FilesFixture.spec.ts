import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { measureInputToPaintP95 } from "./performance";

const INITIAL_FILE = "operator note: preserve this local change";
const SERVER_FILE = "operator note: another writer changed the server version";
const PAGED_FIRST = Array.from({ length: 2_000 }, (_, index) => `line ${String(index + 1).padStart(4, "0")}`).join("\n");
const PAGED_SECOND = Array.from({ length: 2_000 }, (_, index) => `line ${String(index + 2_001).padStart(4, "0")}`).join("\n");

function makeRootEntries(count: number, operatorContent = INITIAL_FILE) {
  return [
    { name: "docs", kind: "directory", size: null },
    { name: "operator.txt", kind: "file", size: operatorContent.length },
    { name: "paged.txt", kind: "file", size: PAGED_FIRST.length + PAGED_SECOND.length + 1 },
    ...Array.from({ length: count - 3 }, (_, index) => ({
      name: `fixture-${String(index).padStart(4, "0")}.txt`,
      kind: "file",
      size: index + 1,
    })),
  ];
}

async function installFilesApi(page: Page, options: { rootEntryCount?: number; operatorContent?: string } = {}) {
  const operatorContent = options.operatorContent ?? INITIAL_FILE;
  const rootEntries = makeRootEntries(options.rootEntryCount ?? 2_000, operatorContent);
  let operatorReads = 0;
  await page.route("**/api/sandboxes/files-fixture/files/list", async (route) => {
    const args = route.request().postDataJSON() as { path?: string };
    const path = args.path ?? "";
    const body = path === "docs"
      ? { path, entries: [{ name: "operator.txt", kind: "file", size: operatorContent.length }], truncated: false }
      : { path: "", entries: rootEntries, truncated: true };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.route("**/api/rpc", async (route) => {
    const { op, args } = route.request().postDataJSON() as { op: string; args: Record<string, unknown> };
    if (op === "file_blame") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ path: args.path, ranges: [{ start_line: 1, line_count: 2, owner: "workspace_session:workspace-fixture" }] }),
      });
      return;
    }
    if (op === "file_write") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
      return;
    }
    if (op !== "file_read") {
      await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: { message: `unexpected ${op}` } }) });
      return;
    }

    const path = String(args.path);
    if (path === "paged.txt") {
      const nextPage = Number(args.offset ?? 1) > 1;
      const content = nextPage ? PAGED_SECOND : PAGED_FIRST;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          path,
          content,
          start_line: nextPage ? 2_001 : 1,
          num_lines: 2_000,
          total_lines: 4_000,
          bytes_read: content.length,
          total_bytes: PAGED_FIRST.length + PAGED_SECOND.length + 1,
          next_offset: nextPage ? null : 2_001,
          truncated: !nextPage,
        }),
      });
      return;
    }

    operatorReads += 1;
    const content = operatorReads >= 3 ? SERVER_FILE : operatorContent;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        path,
        content,
        start_line: 1,
        num_lines: 1,
        total_lines: 1,
        bytes_read: content.length,
        total_bytes: content.length,
        next_offset: null,
        truncated: false,
      }),
    });
  });
}

async function openFiles(page: Page, path = "operator.txt", options: { rootEntryCount?: number; operatorContent?: string } = {}) {
  await installFilesApi(page, options);
  await page.goto(`/p08-files.html?path=${encodeURIComponent(path)}`);
  await expect(page.locator("[data-files-workspace]")).toBeVisible();
  await expect(page.locator(".cm-content")).toBeVisible();
}

for (const [width, height] of [
  [375, 812],
  [768, 1024],
  [1024, 768],
  [1440, 900],
] as const) {
  test(`P08 Files Mantine surface at ${width}x${height} @visual`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openFiles(page);
    await expect(page).toHaveScreenshot(`p08-files-${width}x${height}.png`, { animations: "disabled" });
  });
}

test("P08 Files captures expanded tree and blame at desktop @visual", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFiles(page);
  const tree = page.getByRole("tree", { name: "File tree" });
  const docs = tree.locator('[role="treeitem"][title="docs"]');
  await docs.focus();
  await page.keyboard.press("ArrowRight");
  await expect(tree.locator('[role="treeitem"][title="docs/operator.txt"]')).toBeVisible();
  await page.getByRole("button", { name: "Blame" }).click();
  await expect(page.getByText("workspace_session:workspace-fixture")).toBeVisible();
  await expect(page).toHaveScreenshot("p08-files-tree-blame-1440x900.png", { animations: "disabled" });
});

test("P08 Files captures narrow Drawers @visual", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openFiles(page);
  await page.getByRole("button", { name: "Open file navigator" }).click();
  await expect(page.getByRole("dialog", { name: "File navigator" })).toBeVisible();
  await expect(page).toHaveScreenshot("p08-files-navigator-drawer-375x812.png", { animations: "disabled" });
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Blame" }).click();
  await page.getByRole("button", { name: "Blame legend" }).click();
  await expect(page.getByRole("dialog", { name: "Blame legend" })).toBeVisible();
  await expect(page).toHaveScreenshot("p08-files-blame-drawer-375x812.png", { animations: "disabled" });
});

test("P08 Files captures paged and preserved-conflict editor states @visual", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFiles(page, "paged.txt");
  await page.getByRole("button", { name: "Load next 2000" }).click();
  await page.locator(".cm-scroller").evaluate(async (element) => {
    element.scrollTop = element.scrollHeight;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  await expect(page.locator(".cm-content")).toContainText("line 4000");
  await expect(page).toHaveScreenshot("p08-files-paged-1440x900.png", { animations: "disabled" });
  await page.locator(".cm-content").focus();
  await page.keyboard.press("ControlOrMeta+A");
  await expect(page).toHaveScreenshot("p08-files-paged-selection-1440x900.png", { animations: "disabled" });

  await openFiles(page);
  await page.getByRole("button", { name: "Edit" }).click();
  const content = page.locator(".cm-content");
  await content.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("local draft: preserve this operator edit");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Local draft preserved.")).toBeVisible();
  await expect(page).toHaveScreenshot("p08-files-conflict-1440x900.png", { animations: "disabled" });
});

test("P08 virtualizes the first 2,000 tree entries and supports the tree keyboard contract", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFiles(page);
  const tree = page.getByRole("tree", { name: "File tree" });
  await expect(page.getByText("Showing the first 2,000 entries returned by this directory.")).toBeVisible();
  await expect.poll(() => tree.getByRole("treeitem").count()).toBeLessThan(128);

  const docs = tree.locator('[role="treeitem"][title="docs"]');
  await docs.focus();
  await page.keyboard.press("ArrowRight");
  await expect(tree.locator('[role="treeitem"][title="docs/operator.txt"]')).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(docs).toBeFocused();
  await page.keyboard.press("p");
  await expect(tree.locator('[role="treeitem"][title="paged.txt"]')).toBeFocused();
  await page.keyboard.press("Home");
  await expect(docs).toBeFocused();
  await page.keyboard.press("End");
  await expect(tree.getByRole("treeitem").last()).toBeFocused();
});

test("P12 keeps 10k file-tree keyboard navigation below the input-to-paint budget", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFiles(page, "operator.txt", { rootEntryCount: 10_000 });
  const tree = page.getByRole("tree", { name: "File tree" });
  const docs = tree.locator('[role="treeitem"][title="docs"]');
  await docs.focus();
  await expect.poll(() => tree.getByRole("treeitem").count()).toBeLessThan(128);
  await page.keyboard.press("End");
  await expect(tree.getByRole("treeitem").last()).toBeFocused();

  await measureInputToPaintP95(page, "File tree 10k keyboard navigation", async (iteration) => {
    await page.keyboard.press(iteration % 2 ? "ArrowUp" : "ArrowDown");
    await expect(tree.locator(':focus')).toBeVisible();
  });
});

test("P12 keeps 1 MiB CodeMirror editing below the input-to-paint budget", async ({ page }) => {
  const oneMiB = `${"x".repeat(63)}\n`.repeat(16_384);
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFiles(page, "operator.txt", { operatorContent: oneMiB });
  await page.getByRole("button", { name: "Edit" }).click();
  const content = page.locator(".cm-content");
  await content.focus();

  await measureInputToPaintP95(page, "CodeMirror 1 MiB edit", async (iteration) => {
    await page.keyboard.insertText(String(iteration % 10));
    await expect(content).toBeVisible();
  });
});

test("P08 keeps the CodeMirror instance through paging, blame, and a preserved conflict", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFiles(page, "paged.txt");
  const pagedEditor = page.locator(".cm-editor");
  await pagedEditor.evaluate((element) => element.setAttribute("data-p08-editor", "paged"));
  await page.getByRole("button", { name: "Load next 2000" }).click();
  await expect(page.locator('[data-p08-editor="paged"]')).toHaveCount(1);
  await page.locator(".cm-scroller").evaluate(async (element) => {
    element.scrollTop = element.scrollHeight;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  await expect(page.locator(".cm-content")).toContainText("line 4000");

  await openFiles(page);
  const editor = page.locator(".cm-editor");
  await editor.evaluate((element) => element.setAttribute("data-p08-editor", "operator"));
  await page.getByRole("button", { name: "Blame" }).click();
  await expect(page.getByText("workspace_session:workspace-fixture")).toBeVisible();
  await expect(page.locator('[data-p08-editor="operator"]')).toHaveCount(1);

  await page.getByRole("button", { name: "Edit" }).click();
  const content = page.locator(".cm-content");
  await content.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("local draft: preserve this operator edit");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Local draft preserved.")).toBeVisible();
  await expect(page.locator('[data-p08-editor="operator"]')).toHaveCount(1);
  await expect(content).toContainText("local draft: preserve this operator edit");
});

test("P08 uses narrow navigator and blame Drawers with focus restoration", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openFiles(page);
  const navigatorTrigger = page.getByRole("button", { name: "Open file navigator" });
  await navigatorTrigger.click();
  await expect(page.getByRole("dialog", { name: "File navigator" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(navigatorTrigger).toBeFocused();

  await page.getByRole("button", { name: "Blame" }).click();
  const blameTrigger = page.getByRole("button", { name: "Blame legend" });
  await expect(blameTrigger).toBeVisible();
  await blameTrigger.click();
  await expect(page.getByRole("dialog", { name: "Blame legend" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(blameTrigger).toBeFocused();
});

test("P08 Files has no Axe violations @a11y", async ({ page }) => {
  await openFiles(page);
  expect((await new AxeBuilder({ page }).disableRules("page-has-heading-one").analyze()).violations).toEqual([]);
});
