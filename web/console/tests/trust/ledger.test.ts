import { describe, expect, it } from "vitest";
import { entryFromExec, transcriptFor } from "@/pages/sandbox/terminal/ledger";

describe("terminal ledger trust contract", () => {
  it("scopes in-memory transcripts by sandbox as well as command id", () => {
    const first = transcriptFor("command-1", "sandbox-a");
    const second = transcriptFor("command-1", "sandbox-b");

    first.lines.push("sandbox-a output");

    expect(second).not.toBe(first);
    expect(second.lines).toEqual([]);
  });

  it("retains the authoritative publication-rejection outcome", () => {
    const entry = entryFromExec("echo hello", null, {
      status: "ok",
      exit_code: 0,
      wall_time_seconds: 0,
      command_total_time_seconds: 0,
      start_offset: 0,
      end_offset: 0,
      total_lines: 0,
      original_token_count: 0,
      output: "",
      publish_rejected: true,
      publish_reject_class: "protected_path",
    });

    expect(entry).toMatchObject({
      publishRejected: true,
      publishRejectClass: "protected_path",
    });
  });
});
