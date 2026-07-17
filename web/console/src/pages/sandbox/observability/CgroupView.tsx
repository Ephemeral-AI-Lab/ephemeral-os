import {
  Alert,
  Badge,
  Box,
  Code,
  Grid,
  Group,
  Paper,
  Stack,
  Text,
} from "@mantine/core";
import { CornerDownRight } from "lucide-react";
import {
  fetchCgroup,
  type CgroupGroup,
  type CgroupTopology,
} from "@/api/observability";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { usePoll } from "@/poll/usePoll";

const TOPOLOGY_WINDOW_MS = 60_000;

/**
 * Live cgroup-v2 hierarchy and process placement as reported from the
 * sandbox daemon's `/proc/<pid>/cgroup` view.
 */
export function CgroupView() {
  const { sandboxId } = useSandbox();
  const result = usePoll({
    key: ["observability", sandboxId, "cgroup", "topology"],
    fn: () => fetchCgroup(sandboxId, "sandbox", TOPOLOGY_WINDOW_MS),
    mode: "slow",
  });

  return (
    <Stack gap="md" p="md" data-cgroup-view>
      {result.isError && !result.data ? (
        <Alert color="red" title="Cgroup topology unavailable">
          {result.error.message} — retrying automatically.
        </Alert>
      ) : null}

      <CgroupTopologyPanel
        topology={result.data?.topology}
        pending={result.data === undefined && !result.isError}
      />
    </Stack>
  );
}

function CgroupTopologyPanel({
  topology,
  pending,
}: {
  topology?: CgroupTopology;
  pending: boolean;
}) {
  const available = topology?.available === true;
  return (
    <Paper withBorder p="md" component="section" data-cgroup-topology>
      <Group justify="space-between" align="flex-start" gap="md">
        <Box>
          <Text component="h2" size="lg" fw={600}>Cgroup topology</Text>
          <Text size="sm" c="dimmed">Process placement from /proc/&lt;pid&gt;/cgroup</Text>
        </Box>
        <Stack gap={2} align="flex-end">
          <Badge color={available ? "success" : pending ? "neutral" : "yellow"} variant="light">
            {available ? "cgroup v2" : pending ? "loading" : "unavailable"}
          </Badge>
          <Text size="xs" c="dimmed">auto-refresh</Text>
        </Stack>
      </Group>

      {pending ? (
        <Text size="sm" c="dimmed" mt="md">Loading topology…</Text>
      ) : available && topology ? (
        <>
          <Group gap="xs" mt="md" wrap="wrap">
            <Text size="xs" c="dimmed">delegated root</Text>
            <Code>{topology.root ?? "unknown"}</Code>
            <Text size="xs" c="dimmed" ml={{ base: 0, sm: "sm" }}>controllers</Text>
            <Text size="xs" ff="monospace">{topology.controllers.join(" · ") || "none"}</Text>
          </Group>
          {topology.groups.length > 0 ? (
            <Stack gap={0} mt="md" style={{ border: "1px solid var(--mantine-color-warm-3)", borderRadius: "var(--mantine-radius-sm)", overflow: "hidden" }}>
              {topology.groups.map((group, index) => (
                <CgroupRow key={group.path} group={group} divided={index > 0} />
              ))}
            </Stack>
          ) : (
            <Text size="sm" c="dimmed" mt="md">No delegated child cgroups were reported.</Text>
          )}
        </>
      ) : (
        <Stack gap="xs" mt="md">
          <Text size="sm">{topology?.error ?? "Topology was not reported by this daemon."}</Text>
          {topology?.self_cgroup ? (
            <Group gap="xs" wrap="wrap">
              <Text size="xs" c="dimmed">/proc/self/cgroup</Text>
              <Code>{topology.self_cgroup}</Code>
            </Group>
          ) : null}
          <Text size="xs" c="dimmed">No delegated child cgroups are available to inspect.</Text>
        </Stack>
      )}
    </Paper>
  );
}

function CgroupRow({ group, divided }: { group: CgroupGroup; divided: boolean }) {
  return (
    <Box
      p="sm"
      style={divided ? { borderTop: "1px solid var(--mantine-color-warm-3)" } : undefined}
      data-cgroup-path={group.path}
    >
      <Grid gap="sm" align="flex-start">
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Group gap="xs" align="center" wrap="nowrap">
            {group.role === "root" ? null : <CornerDownRight aria-hidden size={14} />}
            <Text ff="monospace" size="sm" fw={600} style={{ overflowWrap: "anywhere" }}>
              {group.path}
            </Text>
            <Badge color={roleColor(group.role)} variant="light">{group.role}</Badge>
          </Group>
          {group.processes.length > 0 ? (
            <Stack gap={2} mt="xs" ml={group.role === "root" ? 0 : 22}>
              {group.processes.map((process) => (
                <Text key={process.pid} size="xs" ff="monospace" c="dimmed" style={{ overflowWrap: "anywhere" }}>
                  {process.pid} · {process.name} · {process.membership ?? "membership unavailable"}
                </Text>
              ))}
            </Stack>
          ) : (
            <Text size="xs" c="dimmed" mt="xs" ml={group.role === "root" ? 0 : 22}>no direct processes</Text>
          )}
          {group.error ? <Text size="xs" c="yellow.8" mt="xs">{group.error}</Text> : null}
        </Grid.Col>
        <Grid.Col span={{ base: 4, md: 2 }}>
          <TopologyMetric label="CPU total" value={formatCount(group.cpu_usage_usec, "µs")} />
        </Grid.Col>
        <Grid.Col span={{ base: 4, md: 2 }}>
          <TopologyMetric
            label="Memory"
            value={`${formatBytes(group.memory_current_bytes)} / ${group.memory_max_unlimited ? "unlimited" : formatBytes(group.memory_max_bytes)}`}
          />
        </Grid.Col>
        <Grid.Col span={{ base: 4, md: 2 }}>
          <TopologyMetric label="PIDs" value={String(group.processes.length)} />
        </Grid.Col>
      </Grid>
    </Box>
  );
}

function TopologyMetric({ label, value }: { label: string; value: string }) {
  return (
    <Box component="dl" m={0}>
      <Text component="dt" size="xs" c="dimmed">{label}</Text>
      <Text component="dd" m={0} mt={2} ff="monospace" size="xs" style={{ overflowWrap: "anywhere" }}>{value}</Text>
    </Box>
  );
}

function roleColor(role: CgroupGroup["role"]) {
  if (role === "daemon") return "eyeBlue";
  if (role === "workspace") return "success";
  return "neutral";
}

function formatCount(value: number | null, suffix: string) {
  return value === null ? "—" : `${value.toLocaleString()} ${suffix}`;
}

function formatBytes(value: number | null) {
  if (value === null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let scaled = value;
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  return `${scaled.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[unit]}`;
}
