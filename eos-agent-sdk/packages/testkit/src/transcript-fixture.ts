import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { JsonObject } from "@eos/contracts";

/** Write an append-only JSONL fixture and return its path. */
export function writeTranscriptFixture(
  lines: JsonObject[],
  name = "transcript.jsonl",
): string {
  const path = join(mkdtempSync(join(tmpdir(), "eos-testkit-")), name);
  const body = lines.map((line) => JSON.stringify(line)).join("\n");
  writeFileSync(path, body.length > 0 ? `${body}\n` : body);
  return path;
}
