import { expect, test } from "@playwright/test";
import { measureFromTimestampToPaintP95 } from "./performance";

type PollingPerformanceWindow = Window & {
  p12LastFastPollAcceptedAt?: number;
  p12LongTaskDurations?: number[];
  p12CumulativeLayoutShift?: number;
};

async function installPollingPerformanceObservers(page: Parameters<typeof test>[0]["page"]) {
  await page.addInitScript(() => {
    const target = window as PollingPerformanceWindow;
    const fetch = window.fetch.bind(window);
    target.p12LongTaskDurations = [];
    target.p12CumulativeLayoutShift = 0;

    window.fetch = async (input, init) => {
      const response = await fetch(input, init);
      const url = new URL(typeof input === "string" ? input : input.url, window.location.href);
      if (url.searchParams.get("mode") === "fast") {
        target.p12LastFastPollAcceptedAt = performance.now();
      }
      return response;
    };

    if (PerformanceObserver.supportedEntryTypes.includes("longtask")) {
      new PerformanceObserver((entries) => {
        target.p12LongTaskDurations?.push(...entries.getEntries().map((entry) => entry.duration));
      }).observe({ type: "longtask", buffered: true });
    }
    if (PerformanceObserver.supportedEntryTypes.includes("layout-shift")) {
      new PerformanceObserver((entries) => {
        for (const entry of entries.getEntries() as PerformanceEntryList & Array<PerformanceEntry & { hadRecentInput: boolean; value: number }>) {
          if (!entry.hadRecentInput) {
            target.p12CumulativeLayoutShift = (target.p12CumulativeLayoutShift ?? 0) + entry.value;
          }
        }
      }).observe({ type: "layout-shift", buffered: true });
    }
  });
}

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

test("P12 sustains the fast poller for a virtual 60 seconds without unbounded work", async ({ page }) => {
  let fastCalls = 0;
  await page.route("**/p00-polling-data*", async (route) => {
    const mode = new URL(route.request().url()).searchParams.get("mode");
    const revision = mode === "fast" ? ++fastCalls : 1;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ revision }) });
  });

  await installPollingPerformanceObservers(page);
  await page.clock.install({ time: new Date("2024-01-01T00:00:00Z") });
  await page.goto("/polling.html");
  await expect(page.getByTestId("poll-fast")).not.toHaveText("loading");
  const initialCalls = fastCalls;
  await page.evaluate(() => {
    const target = window as PollingPerformanceWindow;
    target.p12LongTaskDurations = [];
    target.p12CumulativeLayoutShift = 0;
  });
  await page.clock.runFor(60_000);
  await expect.poll(() => fastCalls).toBeGreaterThanOrEqual(initialCalls + 100);
  expect(fastCalls).toBeLessThanOrEqual(initialCalls + 151);
  const metrics = await page.evaluate(() => {
    const target = window as PollingPerformanceWindow;
    return {
      maxTask: Math.max(0, ...(target.p12LongTaskDurations ?? [])),
      cls: target.p12CumulativeLayoutShift ?? 0,
    };
  });
  expect(metrics.maxTask).toBeLessThanOrEqual(200);
  expect(metrics.cls).toBe(0);
});

test("P12 keeps accepted fast-poll results below the input-to-paint budget", async ({ page }) => {
  let fastCalls = 0;
  await page.route("**/p00-polling-data*", async (route) => {
    const mode = new URL(route.request().url()).searchParams.get("mode");
    const revision = mode === "fast" ? ++fastCalls : 1;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ revision }) });
  });
  await installPollingPerformanceObservers(page);
  await page.goto("/polling.html");
  const fast = page.getByTestId("poll-fast");
  await expect(fast).not.toHaveText("loading");

  await measureFromTimestampToPaintP95(page, "Fast poll accepted result", async () => {
    const previous = Number(await fast.textContent());
    return page.evaluate(async (currentRevision) => {
      const target = window as PollingPerformanceWindow;
      const output = document.querySelector('[data-testid="poll-fast"]');
      const trigger = document.querySelector('[data-testid="poll-fast-refetch"]');
      if (!(output instanceof HTMLOutputElement)) throw new Error("Missing fast-poll output");
      if (!(trigger instanceof HTMLButtonElement)) throw new Error("Missing fast-poll refetch trigger");

      target.p12LastFastPollAcceptedAt = undefined;
      await new Promise<void>((resolve, reject) => {
        let timeoutId: number | undefined;
        const observer = new MutationObserver(() => {
          if (Number(output.textContent) > currentRevision) {
            observer.disconnect();
            if (timeoutId !== undefined) window.clearTimeout(timeoutId);
            resolve();
          }
        });
        observer.observe(output, { childList: true, characterData: true, subtree: true });
        timeoutId = window.setTimeout(() => {
          observer.disconnect();
          reject(new Error("Timed out waiting for a fast-poll result"));
        }, 5_000);
        trigger.click();
      });
      if (target.p12LastFastPollAcceptedAt === undefined) {
        throw new Error("Fast-poll response acceptance was not recorded");
      }
      return target.p12LastFastPollAcceptedAt;
    }, previous);
  });
});
