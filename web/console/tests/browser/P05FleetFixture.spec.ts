import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { measureInputToPaintP95 } from "./performance";

type SandboxState = "creating" | "ready" | "stopping" | "stopped" | "failed";

function record(id: string, state: SandboxState = "ready") {
  return {
    id,
    workspace_root: `/workspaces/${id}`,
    state,
    daemon: state === "ready" ? { host: "127.0.0.1", port: 7801 } : null,
    daemon_http: state === "ready" ? { host: "127.0.0.1", port: 7802 } : null,
    shared_base: null,
  };
}

function records(count: number, states: SandboxState[] = ["ready"]) {
  return Array.from({ length: count }, (_, index) => record(`sandbox-${String(index + 1).padStart(2, "0")}`, states[index % states.length]));
}

const sample = {
  ts: 1_700_000_000_000,
  sample_delta_ms: 5_000,
  metrics: { mem_cur: 24_000_000, disk_bytes: 4_000_000, files: 17 },
  deltas: { cpu_usec: 12_000 },
};

function snapshotFor(list: ReturnType<typeof record>[]) {
  return {
    sandboxes: list.map((entry, index) => ({
      sandbox_id: entry.id,
      lifecycle_state: entry.state,
      availability: entry.state === "ready" ? "available" : "pending",
      sampled_at_unix_ms: 1_700_000_000_000,
      errors: [],
      daemon: entry.daemon ? { daemon_pid: index + 1, runtime_dir: `/runtime/${entry.id}` } : null,
      resources: { latest: sample, history: [sample] },
      workspaces: entry.state === "ready" ? [
        {
          workspace_id: `workspace-${index + 1}`,
          lifecycle_state: "running",
          network_profile: "shared",
          layers: { base_root_hash: `root-${index + 1}`, layer_count: 2 },
          namespace_fd_count: 3,
          resources: { latest: sample, history: [sample] },
          active_namespace_executions: index === 0 ? [
            { namespace_execution_id: "active-fixture-command", operation: "exec", lifecycle_state: "running" },
          ] : [],
        },
      ] : [],
      stack: { layer_count: 2, layers_bytes: 6_000_000, active_leases: 1 },
    })),
  };
}

const catalog = {
  management: {
    operation_execution_space: "manager",
    families: [],
    routes: [],
    operations: [
      {
        name: "create_sandbox",
        family: "sandbox",
        summary: "Create a deterministic fixture sandbox.",
        description: "",
        related: [],
        args: [
          { name: "name", kind: "string", required: true, help: "Unique sandbox name.", default: "fixture-new" },
          { name: "image", kind: "string", required: true, help: "Docker image.", default: "node:22-alpine" },
          { name: "workspace_root", kind: "path", required: true, help: "Host workspace folder.", default: "/synthetic-large" },
          { name: "count", kind: "integer", required: false, help: "Worker count.", default: "1" },
        ],
      },
    ],
  },
  runtime: { operation_execution_space: "runtime", families: [], routes: [], operations: [] },
  observability: { operation_execution_space: "observability", families: [], routes: [], operations: [] },
};

type FixtureOptions = {
  list?: ReturnType<typeof record>[];
  failList?: boolean;
  failAfterFirstList?: boolean;
  listDelayMs?: number;
};

async function installFleetApi(page: Page, options: FixtureOptions = {}) {
  const list = options.list ?? records(7, ["ready", "ready", "failed", "creating", "stopped"]);
  let listCalls = 0;
  const rootDirectories = Array.from({ length: 500 }, (_, index) => ({
    name: `root-folder-${String(index + 1).padStart(3, "0")}`,
    path: `/root-folder-${index + 1}`,
  }));
  const largeDirectories = Array.from({ length: 10_000 }, (_, index) => ({
    name: `folder-${index + 1}`,
    path: `/synthetic-large/folder-${index + 1}`,
  }));

  await page.route("**/api/catalog", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(catalog) }),
  );
  await page.route("**/api/sandboxes/*/health", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "ok" }) }),
  );
  await page.route("**/api/rpc", async (route) => {
    const { op, args } = route.request().postDataJSON() as { op: string; args: Record<string, unknown> };
    if (op === "list_sandboxes") {
      listCalls += 1;
      if (options.listDelayMs) await new Promise((resolve) => setTimeout(resolve, options.listDelayMs));
      if (options.failList || (options.failAfterFirstList && listCalls > 1)) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: { kind: "gateway_unavailable", message: "fixture gateway unavailable" } }),
        });
        return;
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ sandboxes: list }) });
      return;
    }
    if (op === "snapshot") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshotFor(list)) });
      return;
    }
    if (op === "list_docker_images") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ images: ["node:22-alpine", "python:3.13-slim"] }) });
      return;
    }
    if (op === "list_workspace_directories") {
      const path = typeof args.path === "string" ? args.path : null;
      const body = path === "/synthetic-large"
        ? { path, parent: null, truncated: false, directories: largeDirectories }
        : path === null
          ? { path: null, parent: null, truncated: true, directories: rootDirectories }
          : { path, parent: "/synthetic-large", truncated: false, directories: [] };
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
      return;
    }
    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ error: { kind: "fixture_error", message: `unexpected operation ${op}` } }),
    });
  });
}

async function openFleet(page: Page, options: FixtureOptions = {}) {
  await installFleetApi(page, options);
  await page.goto("/p05-fleet.html");
}

for (const [width, height] of [
  [375, 812],
  [768, 1024],
  [1024, 768],
  [1440, 900],
  [1920, 1080],
] as const) {
  test(`P05 Fleet mixed generation at ${width}x${height} @visual`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openFleet(page);
    await expect(page.locator("[data-fleet-card]")).toHaveCount(7);
    await expect(page).toHaveScreenshot(`p05-fleet-mixed-${width}x${height}.png`, {
      animations: "disabled",
    });
  });
}

test("P05 Fleet loading state remains explicit at 375x812 @visual", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openFleet(page, { listDelayMs: 1_000 });
  await expect(page.locator("[data-fleet-loading]")).toBeVisible();
  await expect(page).toHaveScreenshot("p05-fleet-loading-375x812.png", { animations: "disabled" });
  await expect(page.locator("[data-fleet-card]")).toHaveCount(7);
});

for (const [width, height] of [
  [375, 812],
  [768, 1024],
  [1440, 900],
  [1920, 1080],
] as const) {
test(`P05 Fleet cards stay compact and start-aligned at ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openFleet(page, { list: records(7) });
    await expect(page.locator("[data-fleet-card]")).toHaveCount(7);
    const geometry = await page.locator("[data-fleet-card]").evaluateAll((cards) =>
      cards.map((card) => {
        const box = card.getBoundingClientRect();
        return {
          flexGrow: getComputedStyle(card).flexGrow,
          height: box.height,
          width: box.width,
          x: box.x,
          y: box.y,
        };
      }),
    );
    const collectionWidth = (await page.locator("[data-fleet-card-collection]").boundingBox())!.width;
    const hasHorizontalOverflow = await page.evaluate(() => document.body.scrollWidth > document.body.clientWidth);
    expect(geometry.every((card) => card.height <= 18 * 14 + 1)).toBe(true);
    expect(geometry.every((card) => card.flexGrow === "0")).toBe(true);
    expect(hasHorizontalOverflow).toBe(false);
    if (width < 768) {
      expect(geometry.every((card) => Math.abs(card.width - collectionWidth) < 2)).toBe(true);
    } else {
      expect(geometry.every((card) => card.width <= 28 * 14 + 1)).toBe(true);
      const last = geometry.at(-1)!;
      const firstOnLastRow = geometry.find((card) => Math.abs(card.y - last.y) < 2)!;
      expect(Math.abs(firstOnLastRow.x - geometry[0].x)).toBeLessThan(2);
    }
  });
}

test("P05 ready sandbox cards expose current CPU and memory", async ({ page }) => {
  await openFleet(page, { list: [record("resources-sandbox")] });
  const card = page.locator("[data-fleet-card]");

  await expect(card.getByText("0.24%", { exact: true })).toBeVisible();
  await expect(card.getByText("24.0 MB", { exact: true })).toBeVisible();
});

test("P05 preserves the last fleet data on refresh failure and removes Fleet layers/Squash @visual", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFleet(page, { failAfterFirstList: true });
  await expect(page.locator("[data-fleet-card]")).toHaveCount(7);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("alert", { name: "Fleet refresh failed" })).toContainText("Showing the last confirmed fleet data");
  await expect(page.locator("[data-fleet-card]")).toHaveCount(7);
  await expect(page).toHaveScreenshot("p05-fleet-stale-1440x900.png", { animations: "disabled" });
  await expect(page.getByText("Squash", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Σ .*layers/)).toHaveCount(0);
});

test("P05 empty and initial-error Fleet states remain explicit @visual", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openFleet(page, { list: [] });
  await expect(page.locator("[data-fleet-empty]")).toContainText("No sandboxes yet");
  await expect(page).toHaveScreenshot("p05-fleet-empty-375x812.png", { animations: "disabled" });

  const errorPage = await page.context().newPage();
  await errorPage.setViewportSize({ width: 375, height: 812 });
  await installFleetApi(errorPage, { failList: true });
  await errorPage.goto("/p05-fleet.html");
  await expect(errorPage.locator("[data-fleet-error]")).toContainText("Gateway unreachable");
  await expect(errorPage).toHaveScreenshot("p05-fleet-error-375x812.png", { animations: "disabled" });
  await errorPage.close();
});

test("P05 WorkspacePicker searches virtually, preserves the create draft, and restores focus", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openFleet(page, { list: [record("picker-sandbox")] });
  await expect(page.locator("[data-fleet-card]")).toHaveCount(1);

  await page.getByRole("button", { name: "New Sandbox" }).click();
  const createDialog = page.getByRole("dialog", { name: "Create sandbox" });
  await expect(createDialog).toBeVisible();
  const nameInput = createDialog.locator("#create-name");
  await nameInput.fill("preserve-this-draft");
  const workspaceTrigger = createDialog.locator("#create-workspace_root");
  await workspaceTrigger.focus();
  await workspaceTrigger.click();

  const pickerDialog = page.getByRole("dialog", { name: "Select workspace folder" });
  await expect(pickerDialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(pickerDialog).toBeHidden();
  await expect(workspaceTrigger).toBeFocused();
  await expect(nameInput).toHaveValue("preserve-this-draft");

  await workspaceTrigger.click();
  await expect(pickerDialog).toBeVisible();
  await pickerDialog.getByRole("button", { name: "Search child folders" }).click();
  const searchInput = pickerDialog.getByRole("textbox", { name: "Search child folders" });
  await expect(searchInput).toBeFocused();
  const renderedOptionCount = await pickerDialog.locator("[data-workspace-folder-option]").count();
  expect(renderedOptionCount).toBeGreaterThan(0);
  expect(renderedOptionCount).toBeLessThan(64);
  const searchStartedAt = Date.now();
  await searchInput.fill("folder-9999");
  await expect(pickerDialog.getByRole("option", { name: "folder-9999" })).toBeVisible();
  expect(Date.now() - searchStartedAt).toBeLessThan(1_000);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(pickerDialog.getByText("/synthetic-large/folder-9999", { exact: true })).toBeVisible();
  await pickerDialog.getByRole("button", { name: "Use this folder" }).click();
  await expect(pickerDialog).toBeHidden();
  await expect(workspaceTrigger).toContainText("/synthetic-large/folder-9999");

  await workspaceTrigger.click();
  await pickerDialog.getByRole("button", { name: "Roots" }).click();
  await expect(pickerDialog.locator("[data-workspace-picker-truncated]")).toContainText("first 500 child folders");
  await expect(page).toHaveScreenshot("p05-workspace-picker-truncated-375x812.png", { animations: "disabled" });
});

test("P12 keeps 10k WorkspacePicker filtering below the input-to-paint budget", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFleet(page, { list: [record("performance-picker")] });
  await page.getByRole("button", { name: "New Sandbox" }).click();
  const createDialog = page.getByRole("dialog", { name: "Create sandbox" });
  await createDialog.locator("#create-workspace_root").click();
  const picker = page.getByRole("dialog", { name: "Select workspace folder" });
  await picker.getByRole("button", { name: "Search child folders" }).click();
  const input = picker.getByRole("textbox", { name: "Search child folders" });

  await measureInputToPaintP95(page, "WorkspacePicker 10k filter", async (iteration) => {
    const folder = `folder-${9_999 - iteration}`;
    await input.fill(folder);
    await expect(picker.getByRole("option", { name: folder })).toBeVisible();
  });
});

test("P05 creation and WorkspacePicker retain visible keyboard focus at 375x812 @visual", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openFleet(page, { list: [record("creation-sandbox")] });
  await expect(page.locator("[data-fleet-card]")).toHaveCount(1);

  await page.getByRole("button", { name: "New Sandbox" }).click();
  const createDialog = page.getByRole("dialog", { name: "Create sandbox" });
  await expect(createDialog).toBeVisible();
  await expect(page).toHaveScreenshot("p05-create-375x812.png", { animations: "disabled" });

  const workspaceTrigger = createDialog.locator("#create-workspace_root");
  await workspaceTrigger.focus();
  await workspaceTrigger.click();
  const pickerDialog = page.getByRole("dialog", { name: "Select workspace folder" });
  await expect(pickerDialog).toBeVisible();
  await pickerDialog.getByRole("button", { name: "Search child folders" }).click();
  await expect(pickerDialog.getByRole("textbox", { name: "Search child folders" })).toBeFocused();
  await expect(page).toHaveScreenshot("p05-workspace-picker-375x812.png", { animations: "disabled" });
});

test("P05 Fleet and creation surfaces have no Axe violations @a11y", async ({ page }) => {
  await openFleet(page, { list: records(3, ["ready", "failed", "stopped"]) });
  await expect(page.locator("[data-fleet-card]")).toHaveCount(3);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await page.getByRole("button", { name: "New Sandbox" }).click();
  await expect(page.getByRole("dialog", { name: "Create sandbox" })).toBeVisible();
  await page.waitForTimeout(250);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
