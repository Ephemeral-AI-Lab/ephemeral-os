import type { LlmClient, LlmStreamOptions } from "./client.js";
import {
  RetryConfigSchema,
  StreamGuardConfigSchema,
  type ProviderClientOptions,
  type RetryConfig,
} from "./config.js";
import { ProviderError, toProviderError } from "./errors.js";
import type { LlmStreamEvent } from "./events.js";
import { retryStream } from "./retry.js";
import type { LlmRequest } from "./types.js";
import type { Wire, WireOptions } from "./wires/wire.js";

class IdleTimeoutSignal extends Error {}

/**
 * Race each chunk against the idle watchdog: a stream that goes quiet is a
 * first-class `transport` failure, not a hang. On timeout the in-flight
 * request is aborted.
 */
async function* withIdleGuard<T>(
  source: AsyncIterable<T>,
  idleTimeoutMs: number,
  abort: AbortController,
): AsyncGenerator<T> {
  const iterator = source[Symbol.asyncIterator]();
  try {
    for (;;) {
      const next = iterator.next();
      let timer: NodeJS.Timeout | undefined;
      try {
        const result = await Promise.race([
          next,
          new Promise<never>((_, reject) => {
            timer = setTimeout(
              () => { reject(new IdleTimeoutSignal()); },
              idleTimeoutMs,
            );
          }),
        ]);
        if (result.done) return;
        yield result.value;
      } catch (error) {
        if (error instanceof IdleTimeoutSignal) {
          // The pending read settles after the abort; observe its rejection.
          void next.catch(() => undefined);
          abort.abort();
          throw ProviderError.transport(
            `provider stream idle for ${String(idleTimeoutMs / 1000)}s`,
          );
        }
        throw error;
      } finally {
        clearTimeout(timer);
      }
    }
  } finally {
    try {
      await iterator.return?.();
    } catch {
      // the stream already failed; nothing left to release
    }
  }
}

/**
 * Run one wire attempt: open the sdk stream (the wire pulls per-attempt
 * headers from its transport), guard it with the idle watchdog, feed events
 * through the decoder, and enforce the leg contract (exactly one
 * terminal event; a clean end without it is a truncated stream). Caller
 * aborts are rethrown as-is.
 */
async function* runAttempt(
  wire: Wire,
  request: LlmRequest,
  wireOptions: WireOptions,
  idleTimeoutMs: number,
  signal: AbortSignal | undefined,
): AsyncGenerator<LlmStreamEvent> {
  const attemptAbort = new AbortController();
  const attemptSignal = signal
    ? AbortSignal.any([signal, attemptAbort.signal])
    : attemptAbort.signal;
  let stream: AsyncIterable<unknown>;
  let requestId: string | undefined;
  try {
    ({ stream, requestId } = await wire.open(
      request,
      wireOptions,
      attemptSignal,
    ));
  } catch (error) {
    if (signal?.aborted) throw error;
    throw toProviderError(error, "open");
  }
  const decoder = wire.decoder(requestId);
  try {
    for await (const event of withIdleGuard(
      stream,
      idleTimeoutMs,
      attemptAbort,
    )) {
      yield* decoder.handle(event);
      if (decoder.completed) break;
    }
  } catch (error) {
    if (signal?.aborted) throw error;
    throw toProviderError(error, "stream", requestId);
  } finally {
    attemptAbort.abort();
  }
  signal?.throwIfAborted();
  if (!decoder.completed) {
    throw ProviderError.truncatedStream(requestId);
  }
}

/**
 * The one generic streaming client: a wire composed with an access scheme's
 * transport (bound in the factory), wrapped in the visible-output retry gate
 * and the idle guard. Implements the `LlmClient` leg contract
 * unchanged from Phase 02.
 */
export class LlmStreamClient implements LlmClient {
  readonly #wire: Wire;
  readonly #wireOptions: WireOptions;
  readonly #retry: RetryConfig;
  readonly #idleTimeoutMs: number;

  constructor(
    wire: Wire,
    wireOptions: WireOptions = {},
    options: ProviderClientOptions = {},
  ) {
    this.#wire = wire;
    this.#wireOptions = wireOptions;
    this.#retry = RetryConfigSchema.parse(options.retry ?? {});
    this.#idleTimeoutMs =
      StreamGuardConfigSchema.parse(options.streamGuard ?? {}).idle_timeout_s *
      1000;
  }

  streamMessage(
    request: LlmRequest,
    options?: LlmStreamOptions,
  ): AsyncIterable<LlmStreamEvent> {
    const attempt = () =>
      runAttempt(
        this.#wire,
        request,
        this.#wireOptions,
        this.#idleTimeoutMs,
        options?.signal,
      );
    return retryStream(this.#retry, attempt, options?.signal);
  }
}
