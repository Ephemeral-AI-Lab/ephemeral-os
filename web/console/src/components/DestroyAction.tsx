import { useState } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { rpcStream, systemScope } from "@/api/rpc";
import { ConfirmDestroyDialog } from "@/components/ConfirmDestroyDialog";
import { useErrorToast } from "@/components/ErrorToast";
import { Button } from "@mantine/core";

/**
 * The destroy_sandbox action: type-the-id confirm, `_stream_logs` progress,
 * then back to the Fleet Board. Shared by SandboxCard and SandboxHeader.
 */
export function DestroyAction({
  sandboxId,
  label,
}: {
  sandboxId: string;
  label?: string;
}) {
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
            {label}
          </Button>
      )}
    />
  );
}
