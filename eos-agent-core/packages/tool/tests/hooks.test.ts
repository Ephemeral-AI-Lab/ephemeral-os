import { describe, expect, it } from "vitest";

import { scriptedRunState, scriptedTool, writeTranscriptFixture } from "@eos/testkit";
import { z } from "zod";

import { defineTool } from "../src/define.js";
import type { HookCommand, HookConfigEntry, HookOutput } from "../src/hooks/protocol.js";
import { hookWarnings, runPipeline } from "./support.js";

/** A command hook running an inline node script (double quotes only). */
function nodeHook(js: string, timeoutMs?: number): HookCommand {
  return {
    type: "command",
    command: `"${process.execPath}" -e '${js}'`,
    ...(timeoutMs !== undefined && { timeout_ms: timeoutMs }),
  };
}

function pre(matcher: string | undefined, ...hooks: HookCommand[]): HookConfigEntry {
  return { event: "PreToolUse", matcher, hooks };
}

const probeTool = (onExecute?: () => void) =>
  scriptedTool({
    name: "probe",
    execute: () => {
      onExecute?.();
      return Promise.resolve({ content: "ran" });
    },
  });

describe("hook command adapter", () => {
  it("denies via exit 2 with stderr as the model-visible reason; the call never executes (§15.11)", async () => {
    let executed = false;
    const result = await runPipeline(probeTool(() => (executed = true)), {
      entries: [
        pre("probe", nodeHook('process.stderr.write("blocked by script"); process.exit(2);')),
      ],
    });
    expect(result).toMatchObject({ content: "blocked by script", is_error: true });
    expect(executed).toBe(false);
  });

  it("receives the full payload as JSON on stdin", async () => {
    const echoPayload =
      'let d="";process.stdin.on("data",(c)=>d+=c);process.stdin.on("end",()=>{const p=JSON.parse(d);process.stderr.write([p.event,p.tool_name,p.tool_use_id,p.run.kind,String(p.run.workspace.is_isolated)].join("|"));process.exit(2);});';
    const result = await runPipeline(probeTool(), {
      entries: [pre(undefined, nodeHook(echoPayload))],
    });
    expect(result.content).toBe("PreToolUse|probe|tu_1|main|false");
  });

  it("applies a script's updatedInput re-validated through the schema (§15.12)", async () => {
    const received: number[] = [];
    const calc = defineTool({
      name: "calc",
      description: "calc",
      input: z.object({ n: z.number() }),
      execute: (input) => {
        received.push(input.n);
        return Promise.resolve({ content: "ok" });
      },
    });
    const result = await runPipeline(calc, {
      input: { n: 1 },
      entries: [
        pre("calc", nodeHook('console.log(JSON.stringify({updatedInput:{n:42}}));')),
      ],
    });
    expect(result.is_error).toBe(false);
    expect(received).toEqual([42]);
  });

  it("treats garbage stdout as passthrough with a warning (§15.13)", async () => {
    let executed = false;
    const result = await runPipeline(probeTool(() => (executed = true)), {
      entries: [pre("probe", nodeHook('process.stdout.write("not json at all");'))],
    });
    expect(executed, "non-blocking: the call still ran").toBe(true);
    expect(result.is_error).toBe(false);
    expect(hookWarnings(result)).toContain("not JSON");
  });

  it("treats schema-mismatched stdout as passthrough with a warning (§15.13)", async () => {
    const result = await runPipeline(probeTool(), {
      entries: [
        pre("probe", nodeHook('console.log(JSON.stringify({decision:"maybe"}));')),
      ],
    });
    expect(result.is_error).toBe(false);
    expect(hookWarnings(result)).toContain(
      "did not match HookOutput",
    );
  });

  it("treats schema-mismatched callback output as passthrough with a warning", async () => {
    let executed = false;
    const result = await runPipeline(probeTool(() => (executed = true)), {
      entries: [
        pre("probe", {
          type: "callback",
          run: () =>
            Promise.resolve({ additionalContext: 123 } as unknown as HookOutput),
        }),
      ],
    });
    expect(executed).toBe(true);
    expect(result.is_error).toBe(false);
    expect(hookWarnings(result)).toContain("callback hook output did not match HookOutput");
  });

  it("treats other nonzero exits as passthrough with a warning, never a deny", async () => {
    let executed = false;
    const result = await runPipeline(probeTool(() => (executed = true)), {
      entries: [
        pre("probe", nodeHook('process.stderr.write("flaky"); process.exit(3);')),
      ],
    });
    expect(executed).toBe(true);
    expect(result.is_error).toBe(false);
    expect(hookWarnings(result)).toContain("exited 3");
    expect(hookWarnings(result)).toContain("flaky");
  });

  it("kills a hook on its timeout and passes through with a warning", async () => {
    const result = await runPipeline(probeTool(), {
      entries: [pre("probe", nodeHook("setInterval(() => {}, 1000);", 250))],
    });
    expect(result.is_error).toBe(false);
    expect(hookWarnings(result)).toContain("aborted");
  }, 10_000);

  it("skips hooks whose matcher names a different tool", async () => {
    const result = await runPipeline(probeTool(), {
      entries: [
        pre("other_tool", nodeHook('process.stderr.write("wrong"); process.exit(2);')),
      ],
    });
    expect(result.is_error).toBe(false);
    expect(result.content).toBe("ran");
  });

  it("infers state through run.transcript_path, never live objects", async () => {
    const transcriptPath = writeTranscriptFixture([
      { role: "user", note: "contains FORBIDDEN marker" },
    ]);
    const readTranscript =
      'let d="";process.stdin.on("data",(c)=>d+=c);process.stdin.on("end",()=>{const p=JSON.parse(d);const t=require("fs").readFileSync(p.run.transcript_path,"utf8");if(t.includes("FORBIDDEN")){process.stderr.write("transcript said no");process.exit(2);}process.exit(0);});';
    const result = await runPipeline(probeTool(), {
      runState: scriptedRunState("main", { transcriptPath }),
      entries: [pre("probe", nodeHook(readTranscript))],
    });
    expect(result).toMatchObject({ content: "transcript said no", is_error: true });
  });

  it("folds parallel script outputs through the precedence kernel: deny wins", async () => {
    let executed = false;
    const result = await runPipeline(probeTool(() => (executed = true)), {
      entries: [
        pre(
          "probe",
          nodeHook('console.log(JSON.stringify({decision:"allow"}));'),
          nodeHook('process.stderr.write("hard no"); process.exit(2);'),
        ),
      ],
    });
    expect(result).toMatchObject({ content: "hard no", is_error: true });
    expect(executed).toBe(false);
  });
});
