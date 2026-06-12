import { DEFAULT_MAX_TOKENS } from "@eos/contracts";
import {
  createLlmClient,
  type LlmClient,
  type ProviderConnection,
  type ReasoningEffort,
} from "@eos/llm-client";

/** A named model profile; `AgentSpec.llm` resolves against the config keys. */
export type LlmRef = string;

/** One model profile, as parsed objects — the SDK never reads a config file. */
export interface LlmClientProfile {
  /** Provider endpoint + credentials (api key / access token as values). */
  connection: ProviderConnection;
  /** The model key sent on every request. */
  model: string;
  reasoningEffort?: ReasoningEffort;
  /** Per-turn completion cap; default `DEFAULT_MAX_TOKENS`. */
  maxTokens?: number;
}

/** Provider credentials/model profiles, keyed by `LlmRef`. */
export type LlmClientConfig = Record<LlmRef, LlmClientProfile>;

export interface ResolvedLlmProfile {
  client: LlmClient;
  model: string;
  maxTokens: number;
  reasoningEffort?: ReasoningEffort;
}

export interface LlmClientRegistry {
  /** Throws on an unknown ref — at `createAgent`, never mid-run. */
  require(ref: LlmRef): ResolvedLlmProfile;
}

/**
 * Build every configured client eagerly: an invalid connection fails
 * `createAgentSdk` loudly, never a run.
 */
export function buildLlmClientRegistry(config: LlmClientConfig): LlmClientRegistry {
  const resolved = new Map<LlmRef, ResolvedLlmProfile>();
  for (const [ref, profile] of Object.entries(config)) {
    resolved.set(ref, {
      client: createLlmClient(profile.connection),
      model: profile.model,
      maxTokens: profile.maxTokens ?? DEFAULT_MAX_TOKENS,
      ...(profile.reasoningEffort !== undefined && {
        reasoningEffort: profile.reasoningEffort,
      }),
    });
  }
  return {
    require(ref) {
      const profile = resolved.get(ref);
      if (!profile) {
        const known = [...resolved.keys()].join(", ") || "none";
        throw new Error(`unknown llm ref "${ref}" (configured: ${known})`);
      }
      return profile;
    },
  };
}
