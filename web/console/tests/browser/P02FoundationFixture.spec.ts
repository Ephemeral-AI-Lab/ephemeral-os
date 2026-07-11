import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const STANDARD_VIEWPORTS = [
  [375, 812],
  [768, 1024],
  [1024, 768],
  [1440, 900],
] as const;

async function openFixture(page: Page) {
  await page.goto("/p02-foundation.html");
  await expect(page.getByRole("heading", { name: "Shared provider, stable legacy surface" })).toBeVisible();
  await expect(page.getByRole("banner")).toContainText("EphemeralOS");
}

for (const [width, height] of STANDARD_VIEWPORTS) {
  test(`P02 shared provider shell at ${width}x${height} @visual`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openFixture(page);

    const rootMetrics = await page.getByTestId("foundation-sentinel").evaluate((sentinel) => ({
      bodyFontSize: getComputedStyle(document.body).fontSize,
      bodyMinHeight: getComputedStyle(document.body).minHeight,
      documentMinHeight: getComputedStyle(document.documentElement).minHeight,
      rootMinHeight: getComputedStyle(document.getElementById("root")!).minHeight,
      sentinelHeight: sentinel.getBoundingClientRect().height,
    }));

    expect(rootMetrics).toEqual({
      bodyFontSize: "13.5px",
      bodyMinHeight: "100%",
      documentMinHeight: "100%",
      rootMinHeight: "100%",
      sentinelHeight: expect.any(Number),
    });
    expect(rootMetrics.sentinelHeight).toBeGreaterThan(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect(page).toHaveScreenshot(`p02-foundation-${width}x${height}.png`, {
      animations: "disabled",
      fullPage: true,
    });
  });
}

for (const [width, height] of [
  [375, 812],
  [1440, 900],
] as const) {
  test(`P02 notification and tooltip at ${width}x${height} @visual`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openFixture(page);
    await page.getByRole("button", { name: "Show notification" }).click();
    await expect(page.getByText("Notification host active")).toBeVisible();
    await page.getByTestId("tooltip-trigger").hover();
    await expect(page.getByRole("tooltip")).toHaveText("Rendered above the shared shell");
    await expect(page).toHaveScreenshot(`p02-tooltip-notification-${width}x${height}.png`, {
      animations: "disabled",
      fullPage: true,
    });
  });

  test(`P02 modal portal, focus trap, backdrop, and restoration at ${width}x${height} @visual`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openFixture(page);
    const trigger = page.getByRole("button", { name: "Open modal", exact: true });
    await trigger.focus();
    await trigger.click();

    const dialog = page.getByRole("dialog", { name: "Modal verification" });
    const input = page.getByRole("textbox", { name: "Modal command" });
    await expect(dialog).toBeVisible();
    await expect(input).toBeFocused();
    expect(await dialog.evaluate((element) => element.closest("#root") === null)).toBe(true);
    await expect(page).toHaveScreenshot(`p02-modal-${width}x${height}.png`, {
      animations: "disabled",
      fullPage: true,
    });

    await page.locator(".mantine-Modal-overlay").click({ position: { x: 4, y: 4 } });
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();

    await trigger.click();
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test(`P02 drawer portal, Escape, and restoration at ${width}x${height} @visual`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await openFixture(page);
    const trigger = page.getByRole("button", { name: "Open drawer", exact: true });
    await trigger.focus();
    await trigger.click();

    const dialog = page.getByRole("dialog", { name: "Drawer verification" });
    await expect(dialog).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Drawer filter" })).toBeFocused();
    expect(await dialog.evaluate((element) => element.closest("#root") === null)).toBe(true);
    await expect(page).toHaveScreenshot(`p02-drawer-${width}x${height}.png`, {
      animations: "disabled",
      fullPage: true,
    });

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });
}

test("P02 shared provider fixture has no Axe violations @a11y", async ({ page }) => {
  await openFixture(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
