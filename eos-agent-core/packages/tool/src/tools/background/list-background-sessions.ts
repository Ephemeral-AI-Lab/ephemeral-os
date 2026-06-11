import type { JsonObject } from "@eos/contracts";
import type {
  BackgroundSessionRow,
  BackgroundSessionSupervisor,
} from "@eos/background";
import { z } from "zod";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";
import { DESCRIPTION } from "../description_prompts/list_background_sessions_prompt.js";

/** Rows for running plus settled-but-undelivered sessions. */
export function listBackgroundSessionsTool(
  supervisor: BackgroundSessionSupervisor,
): ToolDefinition {
  return defineTool({
    name: "list_background_sessions",
    description: DESCRIPTION,
    input: z.object({}),
    execute: () =>
      Promise.resolve({
        content: supervisor.listBackgroundSessions().map(backgroundSessionRowContent),
      }),
  });
}

function backgroundSessionRowContent(row: BackgroundSessionRow): JsonObject {
  return {
    type: row.type,
    id: row.id,
    status: row.status,
    started_at: row.started_at,
    ...(row.summary !== undefined && { summary: row.summary }),
    ...(row.description !== undefined && { description: row.description }),
  };
}
