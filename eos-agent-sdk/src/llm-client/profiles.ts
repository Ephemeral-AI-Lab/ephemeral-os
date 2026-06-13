import { z } from "zod";

import type { Access } from "./access/access.js";
import { apiKeyAccess } from "./access/api-key.js";
import { claudeCodingPlanAccess } from "./access/claude-coding-plan.js";
import { codexCodingPlanAccess } from "./access/codex-coding-plan.js";
import { SecretString } from "./secret.js";
import { anthropicMessagesWire } from "./wires/anthropic-messages.js";
import { openAiResponsesWire } from "./wires/openai-responses.js";
import type { WireFactory, WireOptions } from "./wires/wire.js";

const secretString = z.union([
  z.instanceof(SecretString),
  z.string().transform((raw) => new SecretString(raw)),
]);

/** The Claude Code identity prepended as the first system block (§4.1). */
const CLAUDE_CODE_SYSTEM_PREFIX =
  "You are Claude Code, Anthropic's official CLI for Claude.";

/**
 * A connection carries only *where* (`base_url`) and *as-whom* (credentials);
 * the model key stays on `LlmRequest.model`, so one connection serves every
 * model its endpoint hosts. A custom `base_url` on the api profiles is the
 * compatible-endpoint path (gateways, proxies, self-hosted).
 */
export const ProviderConnectionSchema = z.discriminatedUnion("provider", [
  z.object({
    provider: z.literal("anthropic_api"),
    base_url: z.string().default("https://api.anthropic.com"),
    api_key: secretString,
  }),
  z.object({
    provider: z.literal("openai_api"),
    base_url: z.string().default("https://api.openai.com/v1"),
    api_key: secretString,
  }),
  z.object({
    provider: z.literal("claude_coding_plan"),
    base_url: z.string().default("https://api.anthropic.com"),
    access_token: secretString,
  }),
  z.object({
    provider: z.literal("codex_coding_plan"),
    base_url: z.string().default("https://chatgpt.com/backend-api/codex"),
    access_token: secretString,
  }),
]);

export type ProviderConnection = z.input<typeof ProviderConnectionSchema>;

/** A profile resolved to its composition parts; consumed by the factory. */
interface ResolvedProfile {
  wire: WireFactory;
  wireOptions: WireOptions;
  access: Access;
}

/**
 * The only vendor-aware mapping in the package: provider id -> { connection
 * schema (above), wire, wire options, access, default `base_url` }. Adding a
 * provider is one access module and/or one wire module plus one entry here.
 */
export function resolveProfile(connection: ProviderConnection): ResolvedProfile {
  const parsed = ProviderConnectionSchema.parse(connection);
  switch (parsed.provider) {
    case "anthropic_api":
      return {
        wire: anthropicMessagesWire,
        wireOptions: {},
        access: apiKeyAccess(parsed.base_url, parsed.api_key),
      };
    case "openai_api":
      return {
        wire: openAiResponsesWire,
        wireOptions: { dialect: "public" },
        access: apiKeyAccess(parsed.base_url, parsed.api_key),
      };
    case "claude_coding_plan":
      return {
        wire: anthropicMessagesWire,
        wireOptions: { systemPrefix: CLAUDE_CODE_SYSTEM_PREFIX },
        access: claudeCodingPlanAccess(parsed.base_url, parsed.access_token),
      };
    case "codex_coding_plan":
      return {
        wire: openAiResponsesWire,
        wireOptions: { dialect: "codex" },
        access: codexCodingPlanAccess(parsed.base_url, parsed.access_token),
      };
  }
}
