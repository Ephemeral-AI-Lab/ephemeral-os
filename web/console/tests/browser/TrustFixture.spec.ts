import { expect, test, type Page } from "@playwright/test";

const INITIAL_FILE = "operator note: keep this local change";
const SERVER_FILE = "operator note: another writer changed the server version";

async function installFixtureRpc(page: Page) {
  let fileReadCount = 0;
  await page.route("**/api/rpc", async (route) => {
    const request = route.request().postDataJSON() as {
      op: string;
      args: Record<string, unknown>;
    };
    if (request.op === "events") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          view: "events",
          events: [
            {
              ts: 1_700_000_000_000,
              trace: "trace-fixture",
              parent: null,
              name: "workspace.publication_rejected",
              attrs: { reason: "policy_denied", fixture: true },
            },
          ],
        }),
      });
      return;
    }
    if (request.op === "file_read") {
      fileReadCount += 1;
      const content = fileReadCount >= 3 ? SERVER_FILE : INITIAL_FILE;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          path: "notes/operator.txt",
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
      return;
    }
    await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: { message: `unexpected ${request.op}` } }) });
  });
}

async function createConflict(page: Page) {
  await page.getByRole("button", { name: "Edit" }).click();
  const editor = page.locator(".cm-content");
  await expect(editor).toContainText(INITIAL_FILE);
  await editor.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("local draft: preserve this operator edit");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("alert")).toContainText("Local draft preserved");
  await expect(editor).toContainText("local draft: preserve this operator edit");
  await page.locator("#root").evaluate((root) => {
    root.scrollLeft = 0;
  });
  await expect(page.locator("#root")).toHaveJSProperty("scrollLeft", 0);
}

async function openTrustFixture(page: Page) {
  await installFixtureRpc(page);
  await page.goto("/trust.html");
  await expect(page.getByText("Trust-state evidence")).toBeVisible();
  await expect(page.getByText("workspace.publication_rejected")).toBeVisible();
  await expect(page.getByRole("button", { name: "tail" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "tail" }).click();
  await expect(page.getByRole("button", { name: "tail" })).toHaveAttribute("aria-pressed", "false");
  await createConflict(page);
}

test("P00 trust fixture shows paused events, publication rejection, and preserved draft @visual", async ({ page }) => {
  await openTrustFixture(page);
  await expect(page).toHaveScreenshot("p00-trust-1440.png", {
    animations: "disabled",
    fullPage: true,
  });
});

test("P00 trust fixture exposes the same trust states on mobile @visual", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openTrustFixture(page);
  await expect(page).toHaveScreenshot("p00-trust-375.png", {
    animations: "disabled",
    fullPage: true,
  });
});
