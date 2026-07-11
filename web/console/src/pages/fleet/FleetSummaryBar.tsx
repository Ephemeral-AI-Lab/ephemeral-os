import type { SandboxList } from "@/api/types";
import { inFlightCount, type SnapshotResult } from "@/api/observability";
import { formatBytes } from "@/lib/format";
import { Badge, Group, Text } from "@mantine/core";

/**
 * Client-side aggregation of the per-sandbox snapshot list — the no-arg
 * snapshot returns `{sandboxes: [...]}` with nothing pre-aggregated.
 */
export function FleetSummaryBar({
  list,
  snapshot,
}: {
  list: SandboxList | undefined;
  snapshot: SnapshotResult | undefined;
}) {
  const records = list?.sandboxes ?? [];
  const byState = new Map<string, number>();
  for (const record of records) {
    byState.set(record.state, (byState.get(record.state) ?? 0) + 1);
  }
  const snapshots = snapshot?.sandboxes ?? [];
  const executions = snapshots.reduce((total, entry) => total + inFlightCount(entry), 0);
  const memory = snapshots.reduce((total, entry) => {
    const mem = entry.resources.latest?.metrics["mem_cur"];
    return typeof mem === "number" ? total + mem : total;
  }, 0);

  return (
    <Group data-fleet-summary gap="xs" p="sm" wrap="wrap">
      <Text size="sm">
        Fleet: <Text component="span" fw={700} inherit>{records.length}</Text>{" "}
        {records.length === 1 ? "sandbox" : "sandboxes"}
      </Text>
      {[...byState.entries()].map(([state, count]) => (
        <Badge key={state} size="sm" variant="light">
          {count} {state}
        </Badge>
      ))}
      <Text size="sm">
        {executions} running {executions === 1 ? "command" : "commands"}
      </Text>
      <Text size="sm">Σ mem {formatBytes(memory)}</Text>
    </Group>
  );
}
