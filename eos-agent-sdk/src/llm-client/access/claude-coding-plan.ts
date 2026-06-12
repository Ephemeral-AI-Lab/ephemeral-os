import type { SecretString } from "../secret.js";
import type { Access } from "./access.js";

/**
 * The OAuth beta transport shape required by Claude Code subscription access
 * tokens: beta opt-in plus Claude Code identity headers.
 */
const CLAUDE_CODING_PLAN_HEADERS: Readonly<Record<string, string>> = {
  "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
  "anthropic-dangerous-direct-browser-access": "true",
  "user-agent": "claude-cli/2.1.75",
  "x-app": "cli",
};

/** Claude coding-plan access: oauth bearer + Claude Code identity headers. */
export function claudeCodingPlanAccess(
  baseUrl: string,
  accessToken: SecretString,
): Access {
  return {
    baseUrl,
    credential: { kind: "bearer", secret: accessToken },
    headers: () => Promise.resolve({ ...CLAUDE_CODING_PLAN_HEADERS }),
  };
}
