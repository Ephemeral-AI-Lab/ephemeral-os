import { Box, Text, Tooltip } from "@mantine/core";
import { useHealth } from "@/api/health";

/**
 * The daemon_http endpoint health dot, always paired with a tooltip label
 * (state colors never stand alone).
 */
export function HealthDot({
  sandboxId,
  enabled = true,
  showLabel = false,
}: {
  sandboxId: string;
  enabled?: boolean;
  showLabel?: boolean;
}) {
  const health = useHealth(sandboxId, enabled);
  const status = health.data?.status;
  const label =
    status === "ok"
      ? "daemon_http reachable"
      : status === "unreachable"
        ? `daemon_http unreachable: ${health.data?.detail ?? ""}`
        : "probing daemon_http…";
  const color =
    status === "ok"
      ? "var(--mantine-color-success-6)"
      : status === "unreachable"
        ? "var(--mantine-color-danger-6)"
        : "var(--mantine-color-warm-4)";

  return (
    <Tooltip label={label} openDelay={300}>
      <Box component="span" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <Box
          component="span"
          style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: color, opacity: status ? 1 : 0.4 }}
          role="img"
          aria-label={label}
        />
        {showLabel ? (
          <Text component="span" size="xs" c="dimmed">
            {status === "ok" ? "http ok" : status === "unreachable" ? "http down" : "http ?"}
          </Text>
        ) : null}
      </Box>
    </Tooltip>
  );
}
