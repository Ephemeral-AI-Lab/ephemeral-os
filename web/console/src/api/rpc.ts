export type Scope = { kind: "system" } | { kind: "sandbox"; sandbox_id: string };

export const systemScope: Scope = { kind: "system" };

export function sandboxScope(sandboxId: string): Scope {
  return { kind: "sandbox", sandbox_id: sandboxId };
}

interface RpcErrorInit {
  kind: string;
  message: string;
  details?: unknown;
  transport: boolean;
  status?: number;
}

/**
 * The single error type for both RPC failure paths: protocol errors carried
 * in a 200 body (`transport: false`) and transport failures carried in the
 * HTTP status (`transport: true`).
 */
export class RpcError extends Error {
  readonly kind: string;
  readonly details: unknown;
  readonly transport: boolean;
  readonly status?: number;

  constructor(init: RpcErrorInit) {
    super(init.message);
    this.name = "RpcError";
    this.kind = init.kind;
    this.details = init.details ?? {};
    this.transport = init.transport;
    this.status = init.status;
  }
}

interface ErrorEnvelope {
  error?: { kind?: string; message?: string; details?: unknown };
}

function protocolError(body: ErrorEnvelope): RpcError {
  return new RpcError({
    kind: body.error?.kind ?? "unknown_error",
    message: body.error?.message ?? "operation failed",
    details: body.error?.details,
    transport: false,
  });
}

export async function rpc<T = unknown>(
  op: string,
  scope: Scope,
  args: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api/rpc", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ op, scope, args }),
      signal,
    });
  } catch (error) {
    throw new RpcError({
      kind: "network_error",
      message: `console unreachable: ${String(error)}`,
      transport: true,
    });
  }
  const body = (await response.json().catch(() => null)) as ErrorEnvelope | null;
  if (!response.ok) {
    throw new RpcError({
      kind: body?.error?.kind ?? "transport_error",
      message: body?.error?.message ?? `console answered HTTP ${response.status}`,
      details: body?.error?.details,
      transport: true,
      status: response.status,
    });
  }
  if (body && typeof body === "object" && body.error) {
    throw protocolError(body);
  }
  return body as T;
}

interface SseEvent {
  event: string;
  data: string;
}

function parseSseChunk(chunk: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

/**
 * The SSE variant: same request with `Accept: text/event-stream`. Progress
 * lines stream through `onLog`; the returned promise settles with the final
 * `result` event (or rejects on a protocol error inside it, a terminal
 * `error` event, or a transport failure).
 */
export async function rpcStream<T = unknown>(
  op: string,
  scope: Scope,
  args: Record<string, unknown>,
  onLog: (line: string) => void,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/api/rpc", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "text/event-stream",
      },
      body: JSON.stringify({ op, scope, args }),
    });
  } catch (error) {
    throw new RpcError({
      kind: "network_error",
      message: `console unreachable: ${String(error)}`,
      transport: true,
    });
  }
  if (!response.ok || !response.body) {
    const body = (await response.json().catch(() => null)) as ErrorEnvelope | null;
    throw new RpcError({
      kind: body?.error?.kind ?? "transport_error",
      message: body?.error?.message ?? `console answered HTTP ${response.status}`,
      details: body?.error?.details,
      transport: true,
      status: response.status,
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
      const parsed = parseSseChunk(chunk);
      if (!parsed) continue;
      if (parsed.event === "log") {
        const payload = JSON.parse(parsed.data) as { line?: string };
        onLog(payload.line ?? "");
        continue;
      }
      if (parsed.event === "error") {
        const payload = JSON.parse(parsed.data) as {
          kind?: string;
          message?: string;
        };
        throw new RpcError({
          kind: payload.kind ?? "transport_error",
          message: payload.message ?? "gateway stream failed",
          transport: true,
        });
      }
      if (parsed.event === "result") {
        const payload = JSON.parse(parsed.data) as ErrorEnvelope;
        if (payload && typeof payload === "object" && payload.error) {
          throw protocolError(payload);
        }
        return payload as T;
      }
    }
  }
  throw new RpcError({
    kind: "transport_error",
    message: "stream ended without a result event",
    transport: true,
  });
}
