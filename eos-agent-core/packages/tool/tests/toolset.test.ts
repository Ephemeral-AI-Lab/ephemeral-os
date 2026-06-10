import { describe, expect, it } from "vitest";

import { AgentKindSchema, type AgentKind } from "@eos/contracts";
import {
  BackgroundSupervisor,
  NotificationInbox,
  type ToolExecutor,
} from "@eos/engine";
import { scriptedRunState, scriptedTool } from "@eos/testkit";

import type { ToolDefinition } from "../src/contract.js";
import { AGENT_TOOLSET, buildToolExecutor } from "../src/toolset.js";
import { backgroundTools, submissionTool } from "../src/index.js";
import { live, must, toolUse } from "./support.js";

function assemble(kind: AgentKind): {
  executor: ToolExecutor;
  definitions: ToolDefinition[];
} {
  const inbox = new NotificationInbox();
  const supervisor = new BackgroundSupervisor(inbox);
  const definitions = [
    ...backgroundTools(supervisor),
    ...AgentKindSchema.options.map((k) => submissionTool(k, supervisor)),
    scriptedTool({
      name: "rogue",
      execute: () => Promise.resolve({ content: "off the books" }),
    }),
  ];
  const executor = buildToolExecutor({
    runState: scriptedRunState(kind),
    definitions,
    inbox,
  });
  return { executor, definitions };
}

describe("toolset assembly", () => {
  it.each`
    kind          | expected
    ${"main"}     | ${["cancel_background_session", "list_background_sessions", "submit_main_outcome"]}
    ${"worker"}   | ${["cancel_background_session", "list_background_sessions", "submit_worker_outcome"]}
    ${"subagent"} | ${["cancel_background_session", "list_background_sessions", "submit_subagent_outcome"]}
    ${"planner"}  | ${["submit_planner_outcome"]}
    ${"advisor"}  | ${["submit_advisor_outcome"]}
  `(
    "gives $kind exactly its constructed row in sorted order (§15.19)",
    ({ kind, expected }: { kind: AgentKind; expected: string[] }) => {
      const { executor } = assemble(kind);
      expect(executor.specs().map((spec) => spec.name)).toEqual(expected);
    },
  );

  it("skips row names with no constructed definition (§15.19)", () => {
    // Every kind's row names sandbox tools; none are constructed this
    // phase, and the planner row is nothing but skips + its submission.
    expect(AGENT_TOOLSET.planner).toContain("read");
    const { executor } = assemble("planner");
    expect(executor.specs().map((spec) => spec.name)).toEqual([
      "submit_planner_outcome",
    ]);
  });

  it("excludes definitions outside the kind's row (§15.19)", () => {
    const { executor } = assemble("planner");
    const names = executor.specs().map((spec) => spec.name);
    expect(names).not.toContain("rogue");
    expect(names).not.toContain("submit_main_outcome");
    expect(names).not.toContain("list_background_sessions");
  });

  it("dispatches through the assembled pipeline: a solo submission terminates (§15.19)", async () => {
    const { executor } = assemble("main");
    const events: unknown[] = [];
    const results = await executor.executeBatch(
      [toolUse("tu_s", "submit_main_outcome", { summary: "shipped" })],
      live(),
      (event) => events.push(event),
    );
    expect(must(results.at(0))).toMatchObject({
      is_error: false,
      is_terminal: true,
      content: { summary: "shipped" },
    });
  });

  it("names every deferred family in the rows so later phases only add factories", () => {
    expect(AGENT_TOOLSET.main).toEqual(
      expect.arrayContaining(["read", "exec_command", "run_subagent", "delegate_workflow"]),
    );
    expect(AGENT_TOOLSET.worker).toEqual(expect.arrayContaining(["edit"]));
    expect(AGENT_TOOLSET.advisor).toEqual(["read", "multi_read", "submit_advisor_outcome"]);
  });
});
