import type { LlmClient } from "./client.js";
import type { ProviderClientOptions } from "./config.js";
import { resolveProfile, type ProviderConnection } from "./profiles.js";
import { LlmStreamClient } from "./stream-client.js";
import type { WireTransport } from "./wires/wire.js";

/**
 * Construct an `LlmClient` for a named provider connection: resolve the
 * profile, bind the access scheme to the wire as its transport, and wrap the
 * composition in the generic stream client. Invalid connections and codex
 * JWT-claim failures throw here (`ZodError` / `ProviderError` kind
 * `request`); the returned client honors the Phase 02 `LlmClient` leg
 * contract unchanged.
 */
export function createLlmClient(
  connection: ProviderConnection,
  options: ProviderClientOptions = {},
): LlmClient {
  const { wire, wireOptions, access } = resolveProfile(connection);
  const transport: WireTransport = {
    baseUrl: access.baseUrl,
    credential: access.credential,
    headers: () => access.headers(),
    ...(options.fetch !== undefined ? { fetch: options.fetch } : {}),
  };
  return new LlmStreamClient(wire(transport), wireOptions, options);
}
