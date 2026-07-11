import { useState, type ReactNode } from "react";
import { Button, Input, Modal, Text } from "@mantine/core";
import { StreamLogPane } from "@/components/StreamLogPane";

/**
 * Destroy is the one irreversible action: it requires typing the sandbox id
 * before the destroy button arms. While the destroy streams progress, the
 * dialog shows the log pane and blocks re-submission.
 */
export function ConfirmDestroyDialog({
  sandboxId,
  open,
  onOpenChange,
  onConfirm,
  busy,
  logLines,
  trigger,
}: {
  sandboxId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  busy: boolean;
  logLines: string[];
  trigger: (open: () => void) => ReactNode;
}) {
  const [typed, setTyped] = useState("");
  const armed = typed === sandboxId;

  const close = () => {
    if (busy) return;
    setTyped("");
    onOpenChange(false);
  };

  return (
    <>
      {trigger(() => onOpenChange(true))}
      <Modal
        opened={open}
        onClose={close}
        title="Destroy sandbox"
        centered
        closeOnClickOutside={!busy}
        closeOnEscape={!busy}
      >
        <Text size="sm" c="dimmed">
          This stops the daemon, destroys the runtime sandbox, and removes the
          record. It cannot be undone. Type <span className="font-mono text-ink">{sandboxId}</span>{" "}
          to confirm.
        </Text>
        <Input
          mt="md"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder={sandboxId}
          disabled={busy}
          className="w-full font-mono"
          autoFocus
        />
        {busy || logLines.length > 0 ? (
          <div className="mt-3">
            <StreamLogPane lines={logLines} />
          </div>
        ) : null}
        <div className="mt-4 flex justify-end gap-2">
          <Button
            variant="subtle"
            onClick={close}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            color="danger"
            variant="filled"
            disabled={!armed || busy}
            onClick={onConfirm}
          >
            {busy ? "Destroying…" : "Destroy sandbox"}
          </Button>
        </div>
      </Modal>
    </>
  );
}
