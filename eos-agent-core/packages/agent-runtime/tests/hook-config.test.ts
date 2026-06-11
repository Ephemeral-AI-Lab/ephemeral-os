import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { loadHookConfig, loadNotificationRules } from "../src/hook-config.js";
import { tempDir } from "./support.js";

describe("hook config loading", () => {
  it("treats a missing file as no hooks (§7)", () => {
    expect(loadHookConfig(join(tempDir("eos-hooks-"), "absent.json"))).toEqual([]);
  });

  it("loads a valid HookConfigEntry array", () => {
    const path = join(tempDir("eos-hooks-"), "hooks.json");
    const entries = [
      {
        event: "PreToolUse",
        matcher: "write_file",
        hooks: [{ type: "command", command: "node check.js", timeout_ms: 1000 }],
      },
      {
        event: "PostToolUse",
        hooks: [{ type: "command", command: "node audit.js" }],
      },
    ];
    writeFileSync(path, JSON.stringify(entries));
    expect(loadHookConfig(path)).toEqual(
      entries.map((entry) => ({
        ...entry,
        hooks: entry.hooks.map((hook) => ({ ...hook, cwd: dirname(path) })),
      })),
    );
  });

  it("runs .eos-agents hook commands from the repo root", () => {
    const root = tempDir("eos-hooks-root-");
    const agentsDir = join(root, ".eos-agents");
    mkdirSync(agentsDir);
    const path = join(agentsDir, "hooks.json");
    writeFileSync(
      path,
      JSON.stringify([
        {
          event: "PreToolUse",
          hooks: [{ type: "command", command: "node .eos-agents/hooks/check.cjs" }],
        },
      ]),
    );
    expect(loadHookConfig(path)[0]?.hooks[0]).toMatchObject({ cwd: root });
  });

  it("fails loudly on a file that is not JSON (§7)", () => {
    const path = join(tempDir("eos-hooks-"), "hooks.json");
    writeFileSync(path, "{not json");
    expect(() => loadHookConfig(path)).toThrow(/is not valid JSON/);
  });

  it("fails loudly naming the Zod issues on a malformed entry (§7)", () => {
    const path = join(tempDir("eos-hooks-"), "hooks.json");
    writeFileSync(
      path,
      JSON.stringify([{ event: "OnBoot", hooks: [{ type: "command", command: "x" }] }]),
    );
    expect(() => loadHookConfig(path)).toThrow(/is invalid: .*event/);
  });

  it("rejects a top-level object: the config is an entry array (§7)", () => {
    const path = join(tempDir("eos-hooks-"), "hooks.json");
    writeFileSync(path, JSON.stringify({ hooks: [] }));
    expect(() => loadHookConfig(path)).toThrow(/is invalid/);
  });

  it("rejects a trigger event: notification rules live in their own file (04.9 §5)", () => {
    const path = join(tempDir("eos-hooks-"), "hooks.json");
    writeFileSync(
      path,
      JSON.stringify([
        { event: "TurnCompleted", hooks: [{ type: "command", command: "node x.cjs" }] },
      ]),
    );
    expect(() => loadHookConfig(path)).toThrow(/is invalid: .*event/);
  });
});

describe("notification rules loading (04.9 §5)", () => {
  it("treats a missing file as no rules", () => {
    expect(loadNotificationRules(join(tempDir("eos-rules-"), "absent.json"))).toEqual([]);
  });

  it("loads both rule kinds with matchers and fills the command cwd like hooks.json", () => {
    const root = tempDir("eos-rules-root-");
    const agentsDir = join(root, ".eos-agents");
    mkdirSync(agentsDir);
    const path = join(agentsDir, "notification_rules.json");
    const entries = [
      {
        event: "TurnCompleted",
        agent_kind: "main",
        rules: [{ type: "command", command: "node .eos-agents/notification-rules/remind.cjs" }],
      },
      {
        event: "IdleParked",
        agent_name: "researcher",
        timeout_ms: 60_000,
        rules: [{ type: "command", command: "node .eos-agents/notification-rules/idle.cjs" }],
      },
    ];
    writeFileSync(path, JSON.stringify(entries));
    expect(
      loadNotificationRules(path),
      ".eos-agents rule commands run from the repo root",
    ).toEqual(
      entries.map((entry) => ({
        ...entry,
        rules: entry.rules.map((rule) => ({ ...rule, cwd: root })),
      })),
    );
  });

  it("rejects an IdleParked rule without timeout_ms", () => {
    const path = join(tempDir("eos-rules-"), "notification_rules.json");
    writeFileSync(
      path,
      JSON.stringify([
        { event: "IdleParked", rules: [{ type: "command", command: "node x.cjs" }] },
      ]),
    );
    expect(() => loadNotificationRules(path)).toThrow(/is invalid: .*timeout_ms/);
  });

  it("rejects a rule with an empty rules list", () => {
    const path = join(tempDir("eos-rules-"), "notification_rules.json");
    writeFileSync(path, JSON.stringify([{ event: "TurnCompleted", rules: [] }]));
    expect(() => loadNotificationRules(path)).toThrow(/is invalid: .*rules/);
  });

  it("rejects an unknown agent_kind matcher", () => {
    const path = join(tempDir("eos-rules-"), "notification_rules.json");
    writeFileSync(
      path,
      JSON.stringify([
        {
          event: "TurnCompleted",
          agent_kind: "supervisor",
          rules: [{ type: "command", command: "node x.cjs" }],
        },
      ]),
    );
    expect(() => loadNotificationRules(path)).toThrow(/is invalid: .*agent_kind/);
  });

  it("rejects a tool hook event: those belong in hooks.json", () => {
    const path = join(tempDir("eos-rules-"), "notification_rules.json");
    writeFileSync(
      path,
      JSON.stringify([
        { event: "PreToolUse", rules: [{ type: "command", command: "node x.cjs" }] },
      ]),
    );
    expect(() => loadNotificationRules(path)).toThrow(/is invalid: .*event/);
  });

  it("fails loudly on a file that is not JSON", () => {
    const path = join(tempDir("eos-rules-"), "notification_rules.json");
    writeFileSync(path, "{not json");
    expect(() => loadNotificationRules(path)).toThrow(/is not valid JSON/);
  });
});
