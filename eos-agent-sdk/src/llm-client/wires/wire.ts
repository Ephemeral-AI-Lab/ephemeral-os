import type { JsonObject } from "../../contracts/index.js";

import type { LlmStreamEvent } from "../events.js";
import type { SecretString } from "../secret.js";
import type { LlmRequest } from "../types.js";

/**
 * Vendor-specific encode quirks travel as data on the profile, never as
 * subclasses.
 */
export interface WireOptions {
  /** Identity text prepended as the first system block (claude coding plan). */
  systemPrefix?: string;
  /** Request-body dialect for the responses wire. */
  dialect?: "public" | "codex";
}

/**
 * What a wire needs from its connection: where, as-whom, and per-attempt
 * extra headers. The credential shape structurally mirrors `AccessCredential`
 * so `wires/` stays import-free of `access/`; the two meet only in the
 * stream client and factory.
 */
export interface WireTransport {
  baseUrl: string;
  credential: { kind: "api_key" | "bearer"; secret: SecretString };
  /** Called once per attempt; static schemes return a constant. */
  headers(): Promise<Record<string, string>>;
  /** Injectable transport for unit tests; defaults to the global fetch. */
  fetch?: typeof globalThis.fetch;
}

/**
 * Per-wire decoder state machine: sdk stream events in, normalized events
 * out. Decoders accumulate per-block strings linearly and parse tool
 * arguments once at block close.
 */
export interface StreamDecoder<TEvent> {
  /** Set once the provider terminal event has been decoded. */
  readonly completed: boolean;
  handle(event: TEvent): Iterable<LlmStreamEvent>;
}

/**
 * A protocol codec bound to one connection: encode the neutral request, open
 * one sdk streaming call per attempt, and construct the matching decoder. A
 * wire knows nothing about vendors or credential schemes.
 */
export interface Wire {
  open(
    request: LlmRequest,
    options: WireOptions,
    signal: AbortSignal,
  ): Promise<{ stream: AsyncIterable<unknown>; requestId?: string }>;
  decoder(requestId: string | undefined): StreamDecoder<unknown>;
}

/** Binds a wire to one connection; the sdk client is constructed once here. */
export type WireFactory = (transport: WireTransport) => Wire;

/** Parse accumulated tool-argument json; malformed provider json yields `{}`. */
export function parseToolArgs(raw: string): JsonObject {
  if (raw === "") return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as JsonObject;
    }
  } catch {
    // fall through to the empty object
  }
  return {};
}
