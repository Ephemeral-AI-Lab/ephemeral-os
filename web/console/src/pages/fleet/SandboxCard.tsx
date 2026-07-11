import { Link, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { Alert, Box, Button, Card, Group, Stack, Text } from "@mantine/core";
import { rpcStream, systemScope } from "@/api/rpc";
import type { SandboxRecord } from "@/api/types";
import { inFlightCount, type SandboxSnapshot } from "@/api/observability";
import { recordSample } from "@/lib/sparkHistory";
import { ConfirmDestroyDialog } from "@/components/ConfirmDestroyDialog";
import { HealthDot } from "@/components/HealthDot";
import { ResourceSparkline } from "@/components/ResourceSparkline";
import { StateBadge } from "@/components/StateBadge";
import { StreamLogPane } from "@/components/StreamLogPane";
import { useErrorToast } from "@/components/ErrorToast";

export function SandboxCard({
  record,
  snapshot,
  createLogs,
}: {
  record: SandboxRecord;
  snapshot: SandboxSnapshot | undefined;
  createLogs: string[] | undefined;
}) {
  const navigate = useNavigate();
  const spark = recordSample(record.id, snapshot?.resources.latest ?? null);
  const sessions = snapshot?.workspaces.length ?? 0;
  const commands = snapshot ? inFlightCount(snapshot) : 0;

  return (
    <Card component="article" data-fleet-card padding="md" radius="md" shadow="sm" withBorder>
      <Card.Section inheritPadding py="sm" withBorder>
        <Group gap="xs" wrap="nowrap">
          <Text
            component={Link}
            ff="monospace"
            fw={700}
            size="sm"
            to={`/sandboxes/${encodeURIComponent(record.id)}`}
            truncate
            style={{ flex: 1, minWidth: 0 }}
          >
            {record.id}
          </Text>
          <StateBadge state={record.state} />
          {record.state === "ready" ? <HealthDot sandboxId={record.id} /> : null}
        </Group>
      </Card.Section>

      <Stack gap="sm" mt="sm" style={{ flex: 1, minHeight: 0 }}>
        <Box data-fleet-workspace>
          <Text c="dimmed" fw={700} size="xs" tt="uppercase">Workspace</Text>
          <Text ff="monospace" size="xs" title={record.workspace_root} truncate>
            {record.workspace_root}
          </Text>
        </Box>

        {record.state === "creating" ? (
          <StreamLogPane lines={createLogs ?? []} maxHeightClass="max-h-32" />
        ) : null}

        {record.state === "failed" ? (
          <Alert color="danger" variant="light">
            Sandbox failed to reach ready. Inspect the record for endpoint and state details.
          </Alert>
        ) : null}

        {record.state === "ready" ? (
          <Group data-fleet-activity align="center" gap="sm" justify="space-between" wrap="nowrap">
            <Box style={{ minWidth: 0 }}>
              <Text c="dimmed" fw={700} size="xs" tt="uppercase">Activity</Text>
              <Text size="sm" truncate>
                {sessions} {sessions === 1 ? "session" : "sessions"} · {commands}{" "}
                {commands === 1 ? "cmd" : "cmds"}
              </Text>
            </Box>
            <Stack gap={2} style={{ flex: "0 0 auto" }}>
              <Group gap={4} justify="flex-end" wrap="nowrap">
                <Text c="dimmed" size="xs">cpu</Text>
                <ResourceSparkline values={spark.cpu} label="cpu" />
              </Group>
              <Group gap={4} justify="flex-end" wrap="nowrap">
                <Text c="dimmed" size="xs">mem</Text>
                <ResourceSparkline values={spark.mem} label="memory" />
              </Group>
            </Stack>
          </Group>
        ) : null}
      </Stack>

      <Card.Section inheritPadding mt="sm" py="sm" withBorder>
        <Group gap="xs" justify="space-between">
          <Group gap="xs">
            {record.state === "ready" ? (
              <Button
                size="sm"
                variant="filled"
                onClick={() => void navigate(`/sandboxes/${encodeURIComponent(record.id)}`)}
              >
                Open
              </Button>
            ) : null}
            {record.state === "failed" ? (
              <Button
                size="sm"
                onClick={() => void navigate(`/sandboxes/${encodeURIComponent(record.id)}`)}
              >
                Inspect
              </Button>
            ) : null}
          </Group>
          <DestroyAction sandboxId={record.id} />
        </Group>
      </Card.Section>
    </Card>
  );
}

function DestroyAction({ sandboxId }: { sandboxId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const { showError } = useErrorToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const destroy = async () => {
    setBusy(true);
    setLogs([]);
    try {
      await rpcStream(
        "destroy_sandbox",
        systemScope,
        { sandbox_id: sandboxId },
        (line) => setLogs((current) => [...current, line]),
      );
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["fleet"] });
      void navigate("/");
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmDestroyDialog
      sandboxId={sandboxId}
      open={open}
      onOpenChange={setOpen}
      onConfirm={() => void destroy()}
      busy={busy}
      logLines={logs}
      trigger={(open) => (
        <Button
          size="compact-xs"
          color="danger"
          variant="filled"
          aria-label={`Destroy ${sandboxId}`}
          onClick={open}
        >
          <Trash2 size={12} />
        </Button>
      )}
    />
  );
}
