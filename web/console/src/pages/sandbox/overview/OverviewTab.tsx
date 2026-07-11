import { Link } from "react-router";
import type { ReactNode } from "react";
import { Box, Group, Paper, SimpleGrid, Skeleton as MantineSkeleton, Stack, Text, Title } from "@mantine/core";
import type { SandboxSnapshot, WorkspaceSnapshot } from "@/api/observability";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { StateBadge } from "@/components/StateBadge";
import { formatBytes, shortHash } from "@/lib/format";

export function OverviewTab() {
  const { sandboxId, record, snapshot } = useSandbox();
  const sandboxSnapshot = snapshot?.sandboxes[0];

  return (
    <SimpleGrid cols={{ base: 1, lg: 2 }} data-overview-grid p="md" spacing="md">
      <Panel title="Record">
        {record ? (
          <Box component="dl" data-overview-record-fields>
            <Field name="id" value={<Mono>{record.id}</Mono>} />
            <Field name="state" value={<StateBadge state={record.state} />} />
            <Field name="workspace_root" value={<Mono>{record.workspace_root}</Mono>} />
            <Field
              name="daemon"
              value={
                record.daemon ? (
                  <Mono>{record.daemon.host}:{record.daemon.port}</Mono>
                ) : (
                  <Faint>none</Faint>
                )
              }
            />
            <Field
              name="daemon_http"
              value={
                record.daemon_http ? (
                  <Mono>{record.daemon_http.host}:{record.daemon_http.port}</Mono>
                ) : (
                  <Faint>none</Faint>
                )
              }
            />
            <Field
              name="shared_base"
              value={
                record.shared_base ? (
                  <Text component="span" ff="monospace" size="xs" style={{ overflowWrap: "anywhere" }}>
                    {shortHash(record.shared_base.root_hash, 16)} → {record.shared_base.target}
                    {record.shared_base.readonly ? " (ro)" : ""}
                  </Text>
                ) : (
                  <Faint>none</Faint>
                )
              }
            />
          </Box>
        ) : (
          <Skeleton />
        )}
      </Panel>

      <Panel title="Resources">
        {sandboxSnapshot ? <ResourceSnapshotPanel snapshot={sandboxSnapshot} /> : <Empty>no snapshot — sandbox not ready</Empty>}
      </Panel>

      <Panel title="Workspace sessions">
        {sandboxSnapshot ? (
          sandboxSnapshot.workspaces.length > 0 ? (
            <Stack component="ul" gap="xs" m={0} p={0} style={{ listStyle: "none" }}>
              {sandboxSnapshot.workspaces.map((workspace) => (
                <WorkspaceRow
                  key={workspace.workspace_id}
                  sandboxId={sandboxId}
                  workspace={workspace}
                />
              ))}
            </Stack>
          ) : (
            <Empty>
              no live sessions — {" "}
              <Text component={Link} size="xs" to="../terminal">create one in the Terminal tab</Text>
            </Empty>
          )
        ) : (
          <Skeleton />
        )}
      </Panel>

      <Panel title="In-flight executions">
        {sandboxSnapshot ? <InFlightExecutions sandboxId={sandboxId} snapshot={sandboxSnapshot} /> : <Skeleton />}
      </Panel>
    </SimpleGrid>
  );
}

function WorkspaceRow({
  sandboxId,
  workspace,
}: {
  sandboxId: string;
  workspace: WorkspaceSnapshot;
}) {
  return (
    <Paper component="li" data-overview-workspace p="xs" withBorder>
      <Group gap="sm" wrap="wrap">
        <Text
          component={Link}
          ff="monospace"
          size="xs"
          to={`/sandboxes/${encodeURIComponent(sandboxId)}/terminal?session=${encodeURIComponent(workspace.workspace_id)}`}
        >
          {workspace.workspace_id}
        </Text>
        <Text c="dimmed" size="xs">{workspace.network_profile}</Text>
        <Text c="dimmed" size="xs">{workspace.lifecycle_state}</Text>
        <Group gap="sm" ml="auto">
          <Text c="dimmed" size="xs">{workspace.layers.layer_count} layers</Text>
          <Text c="dimmed" ff="monospace" size="xs" title="base root hash">
            {shortHash(workspace.layers.base_root_hash)}
          </Text>
        </Group>
      </Group>
    </Paper>
  );
}

function InFlightExecutions({
  sandboxId,
  snapshot,
}: {
  sandboxId: string;
  snapshot: SandboxSnapshot;
}) {
  const executions = snapshot.workspaces.flatMap((workspace) =>
    workspace.active_namespace_executions.map((execution) => ({
      ...execution,
      workspace_id: workspace.workspace_id,
    })),
  );
  if (executions.length === 0) return <Empty>nothing running right now</Empty>;

  return (
    <Stack component="ul" gap="xs" m={0} p={0} style={{ listStyle: "none" }}>
      {executions.map((execution) => (
        <Paper component="li" key={execution.namespace_execution_id} p="xs" withBorder>
          <Group gap="sm" wrap="wrap">
            <Text
              component={Link}
              ff="monospace"
              size="xs"
              to={`/sandboxes/${encodeURIComponent(sandboxId)}/terminal#cmd-${encodeURIComponent(execution.namespace_execution_id)}`}
            >
              {execution.namespace_execution_id}
            </Text>
            <Text c="dimmed" size="xs">{execution.operation}</Text>
            <Box ml="auto"><StateBadge state="run" label={execution.lifecycle_state} /></Box>
            <Text c="dimmed" ff="monospace" size="xs" w="100%">
              in {execution.workspace_id}
            </Text>
          </Group>
        </Paper>
      ))}
    </Stack>
  );
}

function ResourceSnapshotPanel({ snapshot }: { snapshot: SandboxSnapshot }) {
  const latest = snapshot.resources.latest;
  const mem = latest?.metrics["mem_cur"];
  const cpuDelta = latest?.deltas["cpu_usec"];
  return (
    <Stack gap="sm">
      <Group gap="lg" wrap="wrap">
        <Metric label="memory" value={typeof mem === "number" ? formatBytes(mem) : "–"} />
        <Metric label="cpu Δ" value={typeof cpuDelta === "number" ? `${cpuDelta} µs` : "–"} />
        <Metric label="layers" value={String(snapshot.stack.layer_count)} />
        <Metric label="stack bytes" value={formatBytes(snapshot.stack.layers_bytes)} />
        <Metric label="leases" value={String(snapshot.stack.active_leases)} />
      </Group>
      {snapshot.workspaces.map((workspace) => {
        const disk = workspace.resources.latest?.metrics["disk_bytes"];
        const files = workspace.resources.latest?.metrics["files"];
        return (
          <Group data-overview-resource-workspace key={workspace.workspace_id} gap="lg" pt="xs" wrap="wrap">
            <Text ff="monospace" size="xs">{workspace.workspace_id}</Text>
            <Metric label="disk" value={typeof disk === "number" ? formatBytes(disk) : "–"} />
            <Metric label="files" value={typeof files === "number" ? String(files) : "–"} />
          </Group>
        );
      })}
    </Stack>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Paper component="section" data-overview-panel p="md" withBorder>
      <Title c="dimmed" order={2} size="xs" tt="uppercase">{title}</Title>
      <Box mt="sm">{children}</Box>
    </Paper>
  );
}

function Field({ name, value }: { name: string; value: ReactNode }) {
  return (
    <>
      <Text component="dt" c="dimmed" size="xs">{name}</Text>
      <Box component="dd" m={0} style={{ minWidth: 0, overflowWrap: "anywhere" }}>{value}</Box>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <Text c="dimmed" size="xs">
      {label} <Text component="span" c="var(--mantine-color-text)" ff="monospace" inherit>{value}</Text>
    </Text>
  );
}

function Mono({ children }: { children: ReactNode }) {
  return <Text component="span" ff="monospace" size="xs">{children}</Text>;
}

function Faint({ children }: { children: ReactNode }) {
  return <Text component="span" c="dimmed" size="xs">{children}</Text>;
}

function Empty({ children }: { children: ReactNode }) {
  return <Text c="dimmed" size="xs">{children}</Text>;
}

function Skeleton() {
  return (
    <Stack gap="xs">
      <MantineSkeleton height={10} radius="xl" />
      <MantineSkeleton height={10} radius="xl" width="66%" />
      <MantineSkeleton height={10} radius="xl" width="60%" />
    </Stack>
  );
}
