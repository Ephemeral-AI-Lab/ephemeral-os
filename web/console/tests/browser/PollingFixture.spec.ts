import { expect, test } from "@playwright/test";

test("P00 polling sends requests at the fast cadence and catches up on focus", async ({ page }) => {
  let fastCalls = 0;
  let slowCalls = 0;
  await page.route("**/p00-polling-data*", async (route) => {
    const mode = new URL(route.request().url()).searchParams.get("mode");
    const revision = mode === "fast" ? ++fastCalls : ++slowCalls;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ revision }),
    });
  });

  await page.goto("/polling.html");
  await expect(page.getByTestId("poll-fast")).not.toHaveText("loading");
  await expect(page.getByTestId("poll-slow")).not.toHaveText("loading");
  const fastAtStart = fastCalls;
  const slowAtStart = slowCalls;

  await page.waitForTimeout(850);
  expect(fastCalls - fastAtStart).toBeGreaterThanOrEqual(2);
  expect(slowCalls).toBe(slowAtStart);

  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByTestId("poll-slow")).toHaveText(String(slowAtStart + 1));
});
