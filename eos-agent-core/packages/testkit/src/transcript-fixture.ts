import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { JsonObject } from "@eos/contracts";

/**
 * Write an append-only JSONL transcript fixture and return its path. Hooks
 * infer state through `run.transcript_path` and never receive live
 * objects; until Phase 04.5 ships the real writer, suites point hooks at
 * one of these.
 */
export function writeTranscriptFixture(
  lines: JsonObject[],
  name = "transcript.jsonl",
): string {
  const path = join(mkdtempSync(join(tmpdir(), "eos-testkit-")), name);
  const body = lines.map((line) => JSON.stringify(line)).join("\n");
  writeFileSync(path, body.length > 0 ? `${body}\n` : body);
  return path;
}
