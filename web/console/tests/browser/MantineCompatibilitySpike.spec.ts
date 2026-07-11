import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openFixture(page: Page) {
  await page.goto("/");
  await expect(page.getByText("Disposable P00 fixture")).toBeVisible();
}

test("P00 compatibility fixture supports keyboard focus and portals", async ({ page }) => {
  await openFixture(page);

  await page.getByRole("button", { name: "Tooltip trigger" }).focus();
  await expect(page.getByRole("tooltip", { name: "Keyboard tooltip" })).toBeVisible();

  await page.getByRole("button", { name: "Open modal" }).click();
  await expect(page.getByRole("dialog", { name: "P00 portal modal" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Modal input" })).toBeFocused();

  await page.getByRole("button", { name: "Close modal" }).click();
  await page.getByRole("button", { name: "Show notification" }).click();
  await expect(page.getByRole("alert")).toContainText("Mantine notifications mount");

  await page.getByRole("textbox", { name: "Virtual option" }).fill("option 1999");
  await expect(page.getByRole("option", { name: "option 1999" })).toBeVisible();
});

test("P00 compatibility fixture has no axe violations @a11y", async ({ page }) => {
  await openFixture(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

test("P00 compatibility fixture desktop screenshot @visual", async ({ page }) => {
  await openFixture(page);
  await expect(page).toHaveScreenshot("p00-compatibility-1440.png", {
    animations: "disabled",
    fullPage: true,
  });

  await page.getByRole("button", { name: "Validate form" }).click();
  await page.getByRole("button", { name: "Show notification" }).click();
  await page.getByRole("button", { name: "Tooltip trigger" }).focus();
  await expect(page).toHaveScreenshot("p00-compatibility-1440-interaction.png", {
    animations: "disabled",
    fullPage: true,
  });

  await page.getByRole("button", { name: "Open modal" }).click();
  await expect(page).toHaveScreenshot("p00-compatibility-1440-modal.png", {
    animations: "disabled",
    fullPage: true,
  });
});
