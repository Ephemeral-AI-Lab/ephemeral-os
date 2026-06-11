import { readFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";

import {
  HookConfigEntrySchema,
  TriggerRuleEntrySchema,
  type HookConfigEntry,
  type TriggerRuleEntry,
} from "@eos/tool";
import { z } from "zod";

const DEFAULT_HOOK_CONFIG_PATH = ".eos-agents/hooks.json";
const DEFAULT_NOTIFICATION_RULES_PATH = ".eos-agents/notification_rules.json";

/**
 * Load the operator hook config: a JSON array of `HookConfigEntry`
 * (tool events only; notification trigger rules live in their own file).
 */
export function loadHookConfig(path = DEFAULT_HOOK_CONFIG_PATH): HookConfigEntry[] {
  return loadEntriesFile(path, "hook config", z.array(HookConfigEntrySchema), (entry, cwd) => ({
    ...entry,
    hooks: entry.hooks.map((hook) => withDefaultCwd(hook, cwd)),
  }));
}

/**
 * Load the operator notification rules (Phase 04.9): a JSON array of
 * `TriggerRuleEntry`, same file pattern as `hooks.json` but with a `rules`
 * command list. The rules apply to every agent run the runtime starts,
 * narrowed per run by the optional `agent_name`/`agent_kind` matchers.
 */
export function loadNotificationRules(
  path = DEFAULT_NOTIFICATION_RULES_PATH,
): TriggerRuleEntry[] {
  return loadEntriesFile(
    path,
    "notification rules config",
    z.array(TriggerRuleEntrySchema),
    (entry, cwd) => ({
      ...entry,
      rules: entry.rules.map((rule) => withDefaultCwd(rule, cwd)),
    }),
  );
}

function withDefaultCwd<C extends { cwd?: string }>(command: C, cwd: string): C {
  return command.cwd === undefined ? { ...command, cwd } : command;
}

/**
 * The shared mechanics: a missing file means no entries; anything else
 * malformed is a startup error naming the Zod issues - config errors fail
 * loudly at `createAgentRuntime`, never silently mid-run. `fillCwd` gives
 * commands without a `cwd` the config's owning directory (the repo root
 * for a `.eos-agents` config).
 */
function loadEntriesFile<E>(
  path: string,
  label: string,
  schema: z.ZodType<E[]>,
  fillCwd: (entry: E, cwd: string) => E,
): E[] {
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch (error) {
    // Node fs boundary: ENOENT is the documented "no entries" case.
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw new Error(`${label} ${path} is not readable`, { cause: error });
  }
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch (error) {
    throw new Error(`${label} ${path} is not valid JSON`, { cause: error });
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new Error(
      `${label} ${path} is invalid: ${parsed.error.issues
        .map((issue) => `${issue.path.map(String).join(".")}: ${issue.message}`)
        .join("; ")}`,
    );
  }
  const cwd = commandCwdFor(path);
  return parsed.data.map((entry) => fillCwd(entry, cwd));
}

function commandCwdFor(path: string): string {
  const configDir = dirname(resolve(path));
  return basename(configDir) === ".eos-agents" ? dirname(configDir) : configDir;
}
