import { expect, test } from "@playwright/test";
import { atlasRoutes, installAtlasApi, waitForAtlasRoute } from "./atlas";

const sentinelRoutes = atlasRoutes.filter((route) =>
  ["fleet", "terminal", "events", "files"].includes(route.name),
);

const sentinelViewports = [
  { name: "375x812", width: 375, height: 812 },
  { name: "1440x900", width: 1440, height: 900 },
] as const;

for (const route of sentinelRoutes) {
  for (const viewport of sentinelViewports) {
    test(`P12 cross-browser ${route.name} sentinel at ${viewport.name} @visual`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await installAtlasApi(page);
      await page.goto(`/atlas.html?route=${encodeURIComponent(route.path)}`);
      await waitForAtlasRoute(page, route.ready);

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
      expect(overflow).toBe(false);
      await expect(page).toHaveScreenshot(`p12-${route.name}-${viewport.name}.png`, {
        animations: "disabled",
      });
    });
  }
}
