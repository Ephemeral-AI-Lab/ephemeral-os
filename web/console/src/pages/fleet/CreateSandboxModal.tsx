import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Button, Input, Modal, Select, Text } from "@mantine/core";
import {
  buildArgs,
  findOperation,
  type OperationSpecDoc,
  useCatalogs,
} from "@/api/catalog";
import { listDockerImages } from "@/api/hostResources";
import { rpcStream, systemScope } from "@/api/rpc";
import { useErrorToast } from "@/components/ErrorToast";
import { WorkspacePicker } from "@/pages/fleet/WorkspacePicker";

function defaultValues(spec: OperationSpecDoc | undefined): Record<string, string> {
  const values: Record<string, string> = {};
  for (const arg of spec?.args ?? []) {
    if (arg.default !== null) values[arg.name] = arg.default;
  }
  return values;
}

/**
 * Builds `create_sandbox` arguments from the catalog while using controls
 * suited to its Docker image and host-folder inputs.
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
  const images = useQuery({
    queryKey: ["docker-images"],
    queryFn: listDockerImages,
    enabled: open,
  });

  useEffect(() => {
    if (!open || !spec) return;
    setValues((current) => ({ ...defaultValues(spec), ...current }));
  }, [open, spec]);

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
      setValues(defaultValues(spec));
    }
  };

  const openModal = () => {
    setOpen(true);
    setErrors({});
    setValues(defaultValues(spec));
  };

  return (
    <>
      <Button variant="filled" disabled={busy} onClick={openModal}>
        <Plus size={13} />
        {busy ? "Creating…" : "New Sandbox"}
      </Button>
      <Modal
        opened={open}
        onClose={() => setOpen(false)}
        title="Create sandbox"
        centered
      >
        <Text size="sm" c="dimmed" mb="md">
          {spec?.summary ?? "Create a host-side sandbox record and runtime sandbox."}
        </Text>
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
              {arg.name === "image" ? (
                <Select
                  id={`create-${arg.name}`}
                  value={values[arg.name] ?? ""}
                  onChange={(value) =>
                    setValues((current) => ({ ...current, [arg.name]: value ?? "" }))
                  }
                  data={images.data ?? []}
                  placeholder={images.isPending ? "Loading Docker images…" : "Select a Docker image"}
                  disabled={
                    images.isPending || images.isError || (images.data?.length ?? 0) === 0
                  }
                  classNames={{ input: "font-mono" }}
                />
              ) : arg.name === "workspace_root" ? (
                <WorkspacePicker
                  id={`create-${arg.name}`}
                  value={values[arg.name] ?? ""}
                  onChange={(path) =>
                    setValues((current) => ({ ...current, [arg.name]: path }))
                  }
                />
              ) : (
                <Input
                  id={`create-${arg.name}`}
                  type={arg.name === "count" ? "number" : undefined}
                  min={arg.name === "count" ? 1 : undefined}
                  step={arg.name === "count" ? 1 : undefined}
                  inputMode={arg.name === "count" ? "numeric" : undefined}
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
              )}
              {errors[arg.name] ? (
                <p className="mt-1 text-[11px] text-danger">{errors[arg.name]}</p>
              ) : arg.name === "image" && images.isError ? (
                <p className="mt-1 text-[11px] text-danger">
                  {(images.error as Error).message}
                </p>
              ) : arg.name === "image" && images.data?.length === 0 ? (
                <p className="mt-1 text-[11px] text-danger">No Docker images are available.</p>
              ) : (
                <p className="mt-1 text-[11px] text-ink-faint">{arg.help}</p>
              )}
            </div>
          ))}
          <div className="mt-1 flex justify-end gap-2">
            <Button variant="subtle" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button variant="filled" type="submit" disabled={!spec}>
              Create
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
