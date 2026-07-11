import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Layers } from "lucide-react";
import { Button, Code, Group, Modal, Text } from "@mantine/core";
import { rpcStream, systemScope } from "@/api/rpc";
import { useErrorToast } from "@/components/ErrorToast";
import { StreamLogPane } from "@/components/StreamLogPane";

interface SquashResult {
  layers_before?: number;
  layers_after?: number;
  [key: string]: unknown;
}

/**
 * squash_layerstacks with `_stream_logs` into a StreamLogPane. A pre-run
 * "est. after" count is not derivable from any op, so the dialog shows the
 * before-count only and reports the after-count from the result.
 */
export function SquashDialog({
  sandboxId,
  layerCount,
  trigger,
}: {
  sandboxId: string;
  layerCount?: number;
  trigger: (open: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [result, setResult] = useState<SquashResult | null>(null);
  const { showError } = useErrorToast();
  const queryClient = useQueryClient();

  const run = async () => {
    setBusy(true);
    setLogs([]);
    setResult(null);
    try {
      const outcome = await rpcStream<SquashResult>(
        "squash_layerstacks",
        systemScope,
        { sandbox_id: sandboxId },
        (line) => setLogs((current) => [...current, line]),
      );
      setResult(outcome);
      void queryClient.invalidateQueries({ queryKey: ["fleet"] });
      void queryClient.invalidateQueries({ queryKey: ["sandbox", sandboxId] });
    } catch (error) {
      showError(error);
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };

  const openDialog = () => {
    if (busy) return;
    setLogs([]);
    setResult(null);
    setOpen(true);
  };

  return (
    <>
      {trigger(openDialog)}
      <Modal
        opened={open}
        onClose={() => !busy && setOpen(false)}
        title={
          <Group gap={6}>
            <Layers size={14} color="var(--mantine-color-eyeBlue-7)" />
            Squash layer stack
          </Group>
        }
        centered
        closeOnClickOutside={!busy}
        closeOnEscape={!busy}
      >
        <Text size="sm" c="dimmed">
          Squashes every squashable block of <Text span ff="monospace" style={{ color: "var(--mantine-color-text)" }}>{sandboxId}</Text>&apos;s
          published layers and live-remounts its sessions.
          {typeof layerCount === "number" ? <> Current stack: {layerCount} layers.</> : null}
        </Text>
        {busy || logs.length > 0 ? <StreamLogPane lines={logs} /> : null}
        {result ? (
          <Code block mt="sm" p="sm" style={{ border: "1px solid var(--mantine-color-success-3)", background: "var(--mantine-color-success-0)", color: "var(--mantine-color-text)" }}>
            {JSON.stringify(result)}
          </Code>
        ) : null}
        <Group justify="flex-end" gap="sm" mt="lg">
          <Button variant="subtle" onClick={() => setOpen(false)} disabled={busy}>
            Close
          </Button>
          <Button variant="filled" onClick={() => void run()} disabled={busy}>
            {busy ? "Squashing…" : "Squash"}
          </Button>
        </Group>
      </Modal>
    </>
  );
}
