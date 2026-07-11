import { expect, type Page } from "@playwright/test";

const P95_LIMIT_MS = 100;

function assertP95(label: string, durations: number[]) {
  const ordered = durations.toSorted((left, right) => left - right);
  const p95 = ordered[Math.ceil(ordered.length * 0.95) - 1]!;
  console.info(`[P12 performance] ${label}: p95=${p95.toFixed(1)}ms over ${durations.length} samples`);
  expect(p95, `${label} p95`).toBeLessThan(P95_LIMIT_MS);
  return p95;
}

export async function measureInputToPaintP95(
  page: Page,
  label: string,
  action: (iteration: number) => Promise<void>,
  samples = 200,
) {
  const durations: number[] = [];

  for (let iteration = 0; iteration < samples; iteration += 1) {
    const startedAt = await page.evaluate(() => performance.now());
    await action(iteration);
    durations.push(await page.evaluate(async (start) => {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      return performance.now() - start;
    }, startedAt));
  }

  return assertP95(label, durations);
}

export async function measureFromTimestampToPaintP95(
  page: Page,
  label: string,
  action: (iteration: number) => Promise<number>,
  samples = 200,
) {
  const durations: number[] = [];

  for (let iteration = 0; iteration < samples; iteration += 1) {
    const startedAt = await action(iteration);
    durations.push(await page.evaluate(async (start) => {
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      return performance.now() - start;
    }, startedAt));
  }

  return assertP95(label, durations);
}
