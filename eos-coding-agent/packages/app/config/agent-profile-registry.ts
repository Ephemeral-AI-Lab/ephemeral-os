import { readdirSync } from "node:fs";
import { join } from "node:path";

import type { ToolDefinition } from "@eos/tool";

import { loadAgentProfile, type AgentProfile } from "./agent-profile-loader.js";

/**
 * The static name universe profiles validate against: each runtime-owned
 * tool family's exported name constant plus every `baseTools` definition
 * name, split by terminality. No service is needed to know a name.
 */
export interface KnownToolNames {
  ordinary: ReadonlySet<string>;
  terminal: ReadonlySet<string>;
}

export interface AgentProfileRegistry {
  /** Lookup by agent name only; throws on an unknown name. */
  require(agentName: string): AgentProfile;
  /** Startup cross-checks (llm client ids) iterate every profile. */
  list(): readonly AgentProfile[];
}

/**
 * Load `<dir>/*.md` once and perform ALL static validation at startup -
 * schema, duplicate names, and the tool-selection rules - so `startRun`
 * never re-validates a profile and never registers a run that validation
 * could still reject.
 */
export function loadAgentProfileRegistry(
  dir: string,
  known: KnownToolNames,
): AgentProfileRegistry {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch (error) {
    throw new Error(`agent profiles directory ${dir} is not readable`, {
      cause: error,
    });
  }
  const profiles = new Map<string, AgentProfile>();
  for (const entry of entries.filter((name) => name.endsWith(".md")).sort()) {
    const profile = loadAgentProfile(join(dir, entry));
    validateToolSelection(profile, known);
    const existing = profiles.get(profile.name);
    if (existing) {
      throw new Error(
        `duplicate agent profile name "${profile.name}" (${existing.source_path}, ${profile.source_path})`,
      );
    }
    profiles.set(profile.name, profile);
  }
  return {
    require(agentName) {
      const profile = profiles.get(agentName);
      if (!profile) {
        const names = [...profiles.keys()].join(", ") || "none";
        throw new Error(`unknown agent profile "${agentName}" (known: ${names})`);
      }
      return profile;
    },
    list: () => [...profiles.values()],
  };
}

/**
 * Tool selection has exactly one source (§2.8): keep `allowed_tools` plus
 * the terminal tool when the profile has one — a text-mode profile exposes
 * no submission definition at all. The registry's startup validation makes
 * this total per run.
 */
export function selectProfileDefinitions(
  profile: AgentProfile,
  available: readonly ToolDefinition[],
): ToolDefinition[] {
  const wanted = new Set<string>(profile.allowed_tools);
  if (profile.terminal_tool !== undefined) wanted.add(profile.terminal_tool);
  return available.filter((definition) => wanted.has(definition.name));
}

function validateToolSelection(profile: AgentProfile, known: KnownToolNames): void {
  const terminal = profile.terminal_tool;
  if (terminal !== undefined && profile.allowed_tools.includes(terminal)) {
    throw new Error(
      `agent profile "${profile.name}" lists its terminal_tool "${terminal}" under allowed_tools; the terminal tool is selected separately`,
    );
  }
  for (const tool of profile.allowed_tools) {
    if (!known.ordinary.has(tool)) {
      throw new Error(
        `agent profile "${profile.name}" allows "${tool}", which is not a known non-terminal tool`,
      );
    }
  }
  if (terminal !== undefined && !known.terminal.has(terminal)) {
    throw new Error(
      `agent profile "${profile.name}" selects "${terminal}", which is not a known terminal tool`,
    );
  }
}
