import { Code, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useCallback } from "react";
import { AlertCircle } from "lucide-react";
import { RpcError } from "@/api/rpc";

interface ErrorNotification {
  kind: string;
  message: string;
  details: string | null;
}

export interface ErrorNotificationApi {
  showError: (error: unknown) => void;
}
let nextNotificationId = 1;

export function toErrorNotification(error: unknown): ErrorNotification {
  if (error instanceof RpcError) {
    const details = JSON.stringify(error.details ?? {});
    return {
      kind: error.transport ? `${error.kind} (transport)` : error.kind,
      message: error.message,
      details: details === "{}" ? null : details,
    };
  }
  if (error instanceof Error) {
    return { kind: "client_error", message: error.message, details: null };
  }
  return { kind: "client_error", message: String(error), details: null };
}

function showNormalizedError(error: unknown) {
  const entry = toErrorNotification(error);
  notifications.show({
    id: `error-${nextNotificationId++}`,
    color: "danger",
    icon: <AlertCircle size={15} />,
    title: entry.kind,
    autoClose: 8000,
    message: (
      <Stack gap={2}>
        <Text size="xs">{entry.message}</Text>
        {entry.details ? <Code block>{entry.details}</Code> : null}
      </Stack>
    ),
  });
}

/**
 * Product-semantic adapter: normalize RPC failures while Mantine owns visual
 * rendering, the four-item host limit, dismissal, and 8-second auto-close.
 */
export function useErrorToast(): ErrorNotificationApi {
  const showError = useCallback((error: unknown) => showNormalizedError(error), []);
  return { showError };
}
