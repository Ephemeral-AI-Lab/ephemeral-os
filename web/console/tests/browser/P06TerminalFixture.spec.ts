import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { measureFromTimestampToPaintP95 } from "./performance";

type TerminalApi = {
  createdProfiles: string[];
  destroyedSessions: string[];
  execArgs: Record<string, unknown>[];
  stdinWrites: string[];
  readCalls: number;
  failedReadCalls: number;
  failTranscript: () => void;
};

const FIXTURE_NOW = 1_700_003_600_000;

function transcriptLines(commandSessionId: string) {
  if (commandSessionId === "large-command") {
    return Array.from({ length: 10_000 }, (_, index) =>
      index === 5_000
        ? `line ${index} ${"x".repeat(10_000)}`
        : `line ${String(index).padStart(5, "0")} · terminal fixture output`,
    );
  }
  return [
    "fixture process started",
    "listening on port 5173",
    "awaiting input",
  ];
}

async function installTerminalApi(page: Page, failInitially = false): Promise<TerminalApi> {
  let transcriptFailure = failInitially;
  let readCalls = 0;
  let failedReadCalls = 0;
  const stdinWrites: string[] = [];
  const createdProfiles: string[] = [];
  const destroyedSessions: string[] = [];
  const execArgs: Record<string, unknown>[] = [];

  await page.route("**/api/rpc", async (route) => {
    const { op, args } = route.request().postDataJSON() as {
      op: string;
      args: Record<string, unknown>;
    };

    if (op === "read_command_lines") {
      readCalls += 1;
      if (transcriptFailure) {
        failedReadCalls += 1;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: { kind: "gateway_unavailable", message: "fixture transcript unavailable" } }),
        });
        return;
      }
      const commandSessionId = String(args.command_session_id ?? "running-command");
      const lines = transcriptLines(commandSessionId);
      const start = Number(args.start_offset ?? 0);
      const limit = Number(args.limit ?? 1_000);
      const end = Math.min(start + limit, lines.length);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          status: "running",
          exit_code: null,
          wall_time_seconds: 1,
          command_total_time_seconds: 1,
          start_offset: start,
          end_offset: end,
          total_lines: lines.length,
          original_token_count: 0,
          output: lines.slice(start, end).join("\n"),
          command_session_id: commandSessionId,
          workspace_session_id: "workspace-alpha",
        }),
      });
      return;
    }

    if (op === "write_command_stdin") {
      stdinWrites.push(String(args.stdin ?? ""));
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
      return;
    }

    if (op === "exec_command") {
      execArgs.push(args);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          status: "running",
          exit_code: null,
          wall_time_seconds: 0,
          command_total_time_seconds: 0,
          start_offset: 0,
          end_offset: 0,
          total_lines: 0,
          original_token_count: 0,
          output: "",
          command_session_id: "launched-command",
          workspace_session_id: "workspace-alpha",
        }),
      });
      return;
    }

    if (op === "create_workspace_session") {
      createdProfiles.push(String(args.network_profile ?? "shared"));
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          workspace_session_id: "workspace-created",
          network_profile: args.network_profile ?? "shared",
          finalize_policy: "no_op",
        }),
      });
      return;
    }

    if (op === "destroy_workspace_session") {
      destroyedSessions.push(String(args.workspace_session_id ?? ""));
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          workspace_session_id: args.workspace_session_id,
          destroyed: true,
          evicted_upperdir_bytes: 4096,
        }),
      });
      return;
    }

    await route.fulfill({
      status: 400,
      contentType: "application/json",
      body: JSON.stringify({ error: { kind: "fixture_error", message: `unexpected operation ${op}` } }),
    });
  });

  return {
    createdProfiles,
    destroyedSessions,
    execArgs,
    stdinWrites,
    get readCalls() {
      return readCalls;
    },
    get failedReadCalls() {
      return failedReadCalls;
    },
    failTranscript: () => { transcriptFailure = true; },
  };
}

async function openTerminal(page: Page, large = false, failInitially = false) {
  const api = await installTerminalApi(page, failInitially);
  await page.clock.setFixedTime(FIXTURE_NOW);
  await page.goto(`/p06-terminal.html${large ? "?large=1" : ""}`);
  await expect(page.locator("[data-terminal-workspace]")).toBeVisible();
  await expect(page.locator("[data-terminal-command]")).toHaveCount(large ? 1 : 3);
  return api;
}

for (const [width, height] of [
  [375, 812],
  [768, 1024],
  [1024, 768],
  [1440, 900],
] as const) {
  test(`P06 Terminal session navigation at ${width}x${height} @visual`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openTerminal(page);

    if (width < 768) {
      await expect(page.locator("[data-terminal-session-rail]")).toHaveCount(0);
      await page.getByRole("button", { name: "Open sessions" }).click();
      await expect(page.getByRole("dialog", { name: "Workspace sessions" })).toBeVisible();
    } else {
      await expect(page.locator("[data-terminal-session-rail]")).toBeVisible();
    }

    await expect(page).toHaveScreenshot(`p06-terminal-navigation-${width}x${height}.png`, {
      animations: "disabled",
    });
  });
}

test("P06 separates history filtering from execution targeting and blocks invalid timeouts @visual", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openTerminal(page);

  await page.getByRole("button", { name: "Open sessions" }).click();
  const drawer = page.getByRole("dialog", { name: "Workspace sessions" });
  await drawer.getByText("workspace-beta", { exact: true }).click();
  await expect(drawer).toBeHidden();
  await expect(page.locator("[data-terminal-command]")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Workspace session" })).toContainText("Automatic");

  await page.getByRole("button", { name: "Command options" }).click();
  const timeout = page.getByLabel("Timeout in seconds");
  await timeout.fill("0");
  await expect(page.getByText("Enter a positive timeout.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run" })).toBeDisabled();
  await page.getByRole("textbox", { name: "Command" }).focus();
  await expect(page).toHaveScreenshot("p06-terminal-invalid-timeout-375x812.png", { animations: "disabled" });
});

test("P06 creates and safely destroys explicit workspace sessions", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const api = await openTerminal(page);

  await page.getByRole("button", { name: "Workspace session" }).click();
  await page.getByText("Create isolated session", { exact: true }).click();
  await expect.poll(() => api.createdProfiles).toEqual(["isolated"]);
  await expect(page.getByText("Workspace session created")).toBeVisible();
  await expect(page.getByRole("button", { name: "Workspace session" })).toContainText("workspace-created");
  await expect(page.getByText("history: all commands")).toBeVisible();

  await page.getByRole("textbox", { name: "Command" }).fill("pwd");
  await page.getByRole("button", { name: "Run" }).click();
  await expect.poll(() => api.execArgs.at(-1)?.workspace_session_id).toBe("workspace-created");

    const sessionRail = page.locator("[data-terminal-session-rail]");
    await sessionRail.getByText("workspace-alpha", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Destroy session" })).toBeDisabled();
  await expect(page.getByText("Stop the active command first.")).toBeVisible();

    await sessionRail.getByText("workspace-beta", { exact: true }).click();
  await page.getByRole("button", { name: "Destroy session" }).click();
  const destroyDialog = page.getByRole("dialog", { name: "Destroy workspace session" });
  const confirm = destroyDialog.getByLabel("Type the workspace session ID to confirm");
  await expect(destroyDialog.getByRole("button", { name: "Destroy session" })).toBeDisabled();
  await confirm.fill("workspace-beta");
  await destroyDialog.getByRole("button", { name: "Destroy session" }).click();
  await expect.poll(() => api.destroyedSessions).toEqual(["workspace-beta"]);
  await expect(page.getByText("Workspace session destroyed")).toBeVisible();
  await expect(page.getByText("history: all commands")).toBeVisible();
});

test("P06 automatic execution omits the workspace session id", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  const api = await openTerminal(page);

  await expect(page.getByRole("button", { name: "Workspace session" })).toContainText(
    "Automatic · shared · auto-publish",
  );
  await page.getByRole("textbox", { name: "Command" }).fill("pwd");
  await page.getByRole("button", { name: "Run" }).click();
  await expect.poll(() => api.execArgs).toHaveLength(1);
  expect(api.execArgs[0]).toMatchObject({ cmd: "pwd", yield_time_ms: 0 });
  expect(api.execArgs[0]).not.toHaveProperty("workspace_session_id");
});

test("P06 keeps stale transcript output, authoritative publish rejection, and one control write visible @visual", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const api = await openTerminal(page);
  await expect(page.locator("[data-terminal-line]")).toHaveCount(3);
  await expect(page.getByText("publish rejected · protected_path")).toBeVisible();

  const frame = page.locator("#terminal-running-command");
  await frame.focus();
  await page.keyboard.down("Control");
  await page.keyboard.press("c");
  await page.keyboard.up("Control");
  await expect.poll(() => api.stdinWrites).toEqual(["\u0003"]);

  const readsBeforeFailure = api.readCalls;
  api.failTranscript();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => api.readCalls).toBeGreaterThan(readsBeforeFailure);
  await expect.poll(() => api.failedReadCalls).toBeGreaterThan(0);
  await expect(page.locator("[data-terminal-transcript-stale]")).toBeVisible();
  await expect(page).toHaveScreenshot("p06-terminal-stale-rejected-1440x900.png", { animations: "disabled" });
});

test("P06 virtualizes 10k lines, contains a 10k-character line, and preserves tail navigation @visual", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openTerminal(page, true);
  const transcript = page.locator("[data-terminal-transcript]");
  await expect(transcript).toContainText("line 09999");
  await expect.poll(() => transcript.locator("[data-terminal-line]").count()).toBeLessThan(128);

  await transcript.evaluate(async (element) => {
    element.scrollTop = 5_000 * 18;
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
  const longLine = transcript.locator('[data-index="5000"]');
  await longLine.scrollIntoViewIfNeeded();
  await page.waitForTimeout(100);
  await longLine.evaluate((line) => {
    const pane = line.closest<HTMLElement>("[data-terminal-transcript]")!;
    const lineTop = line.getBoundingClientRect().top;
    const paneRect = pane.getBoundingClientRect();
    const paddingTop = Number.parseFloat(getComputedStyle(pane).paddingTop);
    pane.scrollTop = Math.round(pane.scrollTop + lineTop - paneRect.top - paddingTop);
  });
  await page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))),
  );
  await expect(longLine).toBeInViewport();
  const midGeometry = await longLine.evaluate((line) => ({
    scrollWidth: line.scrollWidth,
    clientWidth: line.clientWidth,
  }));
  expect(midGeometry.scrollWidth).toBeLessThanOrEqual(midGeometry.clientWidth);
  await expect(page).toHaveScreenshot("p06-terminal-10k-mid-1440x900.png", {
    animations: "disabled",
    maxDiffPixelRatio: 0.003,
  });

  await transcript.evaluate(async (element) => {
    element.scrollTop = element.scrollHeight;
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
  await page.waitForTimeout(100);
  await transcript.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  const lastLine = transcript.getByText("line 09999 · terminal fixture output");
  await lastLine.scrollIntoViewIfNeeded();
  await expect(lastLine).toBeInViewport();
  await expect(page).toHaveScreenshot("p06-terminal-10k-tail-1440x900.png", {
    animations: "disabled",
    maxDiffPixelRatio: 0.003,
  });
});

test("P12 keeps 10k Terminal scrolling below the input-to-paint budget", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openTerminal(page, true);
  const transcript = page.locator("[data-terminal-transcript]");

  await measureFromTimestampToPaintP95(page, "Terminal 10k scroll", async (iteration) => {
    const index = (iteration * 463 + 251) % 10_000;
    return transcript.evaluate((element, target) => {
      const startedAt = performance.now();
      element.scrollTop = target / 9_999 * (element.scrollHeight - element.clientHeight);
      return startedAt;
    }, index);
  });
});

test("P06 Terminal has no Axe violations in the expanded running state @a11y", async ({ page }) => {
  await openTerminal(page);
  await expect(page.locator("[data-terminal-line]")).toHaveCount(3);
  expect((await new AxeBuilder({ page }).disableRules("page-has-heading-one").analyze()).violations).toEqual([]);
});

test("P06 makes the last transcript output explicit when its initial refresh fails", async ({ page }) => {
  await openTerminal(page, false, true);
  await expect(page.locator("[data-terminal-transcript-stale]")).toBeVisible();
});
