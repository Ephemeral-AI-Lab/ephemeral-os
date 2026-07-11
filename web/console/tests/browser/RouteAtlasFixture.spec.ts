import { expect, test } from "@playwright/test";
import { atlasRoutes, atlasViewports, installAtlasApi, waitForAtlasRoute } from "./atlas";

for (const route of atlasRoutes) {
  for (const viewport of atlasViewports) {
    test(`P00 atlas ${route.name} at ${viewport.name} @visual`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await installAtlasApi(page);
      await page.goto(`/atlas.html?route=${encodeURIComponent(route.path)}`);
      await waitForAtlasRoute(page, route.ready);
      await expect(page).toHaveScreenshot(`p00-atlas-${route.name}-${viewport.name}.png`, {
        animations: "disabled",
      });
    });
  }
}
