import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { buildArgs, findOperation, useCatalogs } from "@/api/catalog";
import { rpcStream, systemScope } from "@/api/rpc";
import { useErrorToast } from "@/components/ErrorToast";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

/**
 * Mirrors `create_sandbox`'s catalog args exactly (image, workspace bind
 * root, count) — fields render from the spec so the form cannot drift.
 * Submits with `_stream_logs`; progress renders on the fleet's Creating
 * card, the web equivalent of the CLI's `--progress`.
 */
export function CreateSandboxModal({
  onStream,
}: {
  onStream: (logs: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const catalogs = useCatalogs();
  const { showError } = useErrorToast();
  const queryClient = useQueryClient();

  const spec = findOperation(catalogs.data?.management, "create_sandbox");

  const submit = async () => {
    if (!spec) return;
    const built = buildArgs(spec.args, values);
    setErrors(built.errors);
    if (Object.keys(built.errors).length > 0) return;
    setBusy(true);
    const logs: string[] = [];
    onStream(logs.slice());
    setOpen(false);
    try {
      await rpcStream("create_sandbox", systemScope, built.args, (line) => {
        logs.push(line);
        onStream(logs.slice());
        void queryClient.invalidateQueries({ queryKey: ["fleet"] });
      });
      void queryClient.invalidateQueries({ queryKey: ["fleet"] });
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
      onStream(null);
      setValues({});
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setErrors({});
      }}
    >
      <DialogTrigger asChild>
        <Button variant="primary" disabled={busy}>
          <Plus size={13} />
          {busy ? "Creating…" : "New Sandbox"}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create sandbox</DialogTitle>
          <DialogDescription>
            {spec?.summary ?? "Create a host-side sandbox record and runtime sandbox."}
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {(spec?.args ?? []).map((arg) => (
            <div key={arg.name}>
              <label
                htmlFor={`create-${arg.name}`}
                className="mb-1 block text-xs font-medium text-ink-mid"
              >
                <span className="font-mono">{arg.name}</span>
                {arg.required ? <span className="text-danger"> *</span> : null}
              </label>
              <Input
                id={`create-${arg.name}`}
                value={values[arg.name] ?? ""}
                onChange={(event) =>
                  setValues((current) => ({
                    ...current,
                    [arg.name]: event.target.value,
                  }))
                }
                placeholder={arg.help}
                className="w-full font-mono"
              />
              {errors[arg.name] ? (
                <p className="mt-1 text-[11px] text-danger">{errors[arg.name]}</p>
              ) : (
                <p className="mt-1 text-[11px] text-ink-faint">{arg.help}</p>
              )}
            </div>
          ))}
          <div className="mt-1 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={!spec}>
              Create
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
