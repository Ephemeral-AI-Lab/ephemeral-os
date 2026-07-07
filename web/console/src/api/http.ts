import { RpcError } from "@/api/rpc";

interface ErrorEnvelope {
  error?: { kind?: string; message?: string; details?: unknown };
}

export async function postJson<T = unknown>(
  url: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(args),
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
    throw new RpcError({
      kind: body.error.kind ?? "unknown_error",
      message: body.error.message ?? "operation failed",
      details: body.error.details,
      transport: false,
    });
  }
  return body as T;
}
