import { readFileSync } from "node:fs";

import {
  SecretString,
  createLlmClient,
  type LlmClient,
  type ReasoningEffort,
} from "@eos/llm-client";
import { z } from "zod";

/** One configured client, resolved at load; lookup key is `id`. */
export interface LlmClientBinding {
  id: string;
  model_id: string;
  reasoning_effort: ReasoningEffort;
  client: LlmClient;
}

export interface LlmClientRegistry {
  /**
   * The single lookup site (the §10 seam for refresh-on-read; today the
   * JWT is validated only at load). Throws on an unknown id.
   */
  require(llmClientId: string): LlmClientBinding;
}

const ReasoningEffortSchema = z.enum(["minimal", "low", "medium", "high", "max"]);

const LlmClientEntrySchema = z.object({
  id: z.string().min(1),
  provider: z.literal("codex_coding_plan"),
  model_id: z.string().min(1),
  reasoning_effort: ReasoningEffortSchema.default("medium"),
  base_url: z.url().optional(),
  auth: z.object({
    kind: z.literal("codex_cli_auth_file"),
    path: z.string().min(1),
  }),
});

const LlmClientsConfigSchema = z.object({
  clients: z.array(LlmClientEntrySchema),
});

type LlmClientEntry = z.infer<typeof LlmClientEntrySchema>;

/**
 * Load `.eos-agents/llm_clients.json` and build every configured client.
 * Config errors fail loudly here, at startup, never silently mid-run.
 */
export function loadLlmClientRegistry(path: string): LlmClientRegistry {
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch (error) {
    throw new Error(`llm clients config ${path} is not readable`, { cause: error });
  }
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch (error) {
    throw new Error(`llm clients config ${path} is not valid JSON`, { cause: error });
  }
  const parsed = LlmClientsConfigSchema.safeParse(json);
  if (!parsed.success) {
    throw new Error(
      `llm clients config ${path} is invalid: ${parsed.error.issues
        .map((issue) => `${issue.path.map(String).join(".")}: ${issue.message}`)
        .join("; ")}`,
    );
  }
  const bindings = new Map<string, LlmClientBinding>();
  for (const entry of parsed.data.clients) {
    if (bindings.has(entry.id)) {
      throw new Error(`llm clients config ${path} has duplicate client id "${entry.id}"`);
    }
    bindings.set(entry.id, codexBinding(entry));
  }
  return {
    require(llmClientId) {
      const binding = bindings.get(llmClientId);
      if (!binding) {
        throw new Error(`unknown llm client id "${llmClientId}" (config: ${path})`);
      }
      return binding;
    },
  };
}

function codexBinding(entry: LlmClientEntry): LlmClientBinding {
  const accessToken = readCodexAccessToken(entry.auth.path);
  return {
    id: entry.id,
    model_id: entry.model_id,
    reasoning_effort: entry.reasoning_effort,
    client: createLlmClient({
      provider: entry.provider,
      ...(entry.base_url !== undefined && { base_url: entry.base_url }),
      access_token: accessToken,
    }),
  };
}

// --- Codex CLI auth file ----------------------------------------------------
// Mirrors packages/llm-client/e2e/support/codex-auth.ts, except a missing or
// stale credential is a startup error here, not a test skip. The token goes
// straight into `SecretString` and is never written back or logged.

const AuthFileSchema = z.object({
  tokens: z.object({ access_token: z.string() }),
});

const JwtPayloadSchema = z.object({
  exp: z.number().optional(),
  "https://api.openai.com/auth": z
    .object({ chatgpt_account_id: z.string().optional() })
    .optional(),
});

function readCodexAccessToken(path: string): SecretString {
  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    throw new Error(`codex auth file ${path} not found (run "codex login")`);
  }
  let accessToken: string;
  try {
    accessToken = AuthFileSchema.parse(JSON.parse(raw)).tokens.access_token;
  } catch {
    throw new Error(`codex auth file ${path} has no tokens.access_token (run "codex login")`);
  }
  const payload = decodeJwtPayload(accessToken);
  if (payload?.["https://api.openai.com/auth"]?.chatgpt_account_id === undefined) {
    throw new Error(
      `codex access token in ${path} has no chatgpt account claim (run codex to refresh)`,
    );
  }
  if (payload.exp === undefined || payload.exp * 1000 <= Date.now() + 60_000) {
    throw new Error(`codex access token in ${path} is expired (run codex to refresh)`);
  }
  return new SecretString(accessToken);
}

function decodeJwtPayload(token: string): z.infer<typeof JwtPayloadSchema> | undefined {
  const segment = token.split(".").at(1);
  if (segment === undefined || segment === "") return undefined;
  try {
    return JwtPayloadSchema.parse(
      JSON.parse(Buffer.from(segment, "base64url").toString("utf8")),
    );
  } catch {
    return undefined;
  }
}
