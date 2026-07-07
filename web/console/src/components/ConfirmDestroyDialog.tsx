import { useState, type ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
  trigger?: ReactNode;
}) {
  const [typed, setTyped] = useState("");
  const armed = typed === sandboxId;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        setTyped("");
        onOpenChange(next);
      }}
    >
      {trigger}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Destroy sandbox</DialogTitle>
          <DialogDescription>
            This stops the daemon, destroys the runtime sandbox, and removes
            the record. It cannot be undone. Type{" "}
            <span className="font-mono text-ink">{sandboxId}</span> to
            confirm.
          </DialogDescription>
        </DialogHeader>
        <Input
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder={sandboxId}
          disabled={busy}
          className="font-mono"
          autoFocus
        />
        {busy || logLines.length > 0 ? (
          <div className="mt-3">
            <StreamLogPane lines={logLines} />
          </div>
        ) : null}
        <div className="mt-4 flex justify-end gap-2">
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={!armed || busy}
            onClick={onConfirm}
          >
            {busy ? "Destroying…" : "Destroy sandbox"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
