import { readFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";

import { HookConfigEntrySchema, type HookConfigEntry } from "@eos/tool";
import { z } from "zod";

const HookConfigSchema = z.array(HookConfigEntrySchema);

const DEFAULT_HOOK_CONFIG_PATH = ".eos-agents/hooks.json";

/**
 * Load the operator hook config: a JSON array of `HookConfigEntry`. A
 * missing file means no hooks; anything else malformed is a startup error
 * naming the Zod issues - config errors fail loudly at `createAgentRuntime`,
 * never silently mid-run.
 */
export function loadHookConfig(path = DEFAULT_HOOK_CONFIG_PATH): HookConfigEntry[] {
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch (error) {
    // Node fs boundary: ENOENT is the documented "no hooks" case.
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw new Error(`hook config ${path} is not readable`, { cause: error });
  }
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch (error) {
    throw new Error(`hook config ${path} is not valid JSON`, { cause: error });
  }
  const parsed = HookConfigSchema.safeParse(json);
  if (!parsed.success) {
    throw new Error(
      `hook config ${path} is invalid: ${parsed.error.issues
        .map((issue) => `${issue.path.map(String).join(".")}: ${issue.message}`)
        .join("; ")}`,
    );
  }
  const cwd = commandCwdFor(path);
  return parsed.data.map((entry) => ({
    ...entry,
    hooks: entry.hooks.map((hook) =>
      hook.type === "command" && hook.cwd === undefined ? { ...hook, cwd } : hook,
    ),
  }));
}

function commandCwdFor(path: string): string {
  const configDir = dirname(resolve(path));
  return basename(configDir) === ".eos-agents" ? dirname(configDir) : configDir;
}
