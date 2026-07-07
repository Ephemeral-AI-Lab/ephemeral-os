import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { AppWindow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function previewPath(
  sandboxId: string,
  scope: string,
  port: string,
  path = "/",
): string {
  const params = new URLSearchParams({ scope, port, path });
  return `/sandboxes/${encodeURIComponent(sandboxId)}/preview?${params}`;
}

/**
 * The PortPreview launcher: pick a scope (shared, or an isolated workspace
 * session) and a port, then open the Preview tab pre-filled. Isolated scopes
 * carry the bind hint — the in-session server must listen on 0.0.0.0 or the
 * workspace IP, never 127.0.0.1.
 */
export function PortPreview({
  sandboxId,
  scopes,
  defaultScope = "shared",
  defaultPort = "",
  trigger,
}: {
  sandboxId: string;
  scopes?: { id: string; label: string; isolated: boolean }[];
  defaultScope?: string;
  defaultPort?: string;
  trigger?: ReactNode;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState(defaultScope);
  const [port, setPort] = useState(defaultPort);
  const scopeList = scopes ?? [
    { id: "shared", label: "shared network", isolated: false },
  ];
  const selected = scopeList.find((entry) => entry.id === scope);
  const portValid = /^\d+$/.test(port) && Number(port) >= 1 && Number(port) <= 65535;

  const openPreview = () => {
    if (!portValid) return;
    setOpen(false);
    void navigate(previewPath(sandboxId, scope, port));
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {trigger ?? (
          <Button size="sm" title="Open a served port in the Preview tab">
            <AppWindow size={12} />
            Preview
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent>
        <div className="flex flex-col gap-2">
          <label className="text-xs text-ink-mid">scope</label>
          <Select value={scope} onValueChange={setScope}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {scopeList.map((entry) => (
                <SelectItem key={entry.id} value={entry.id}>
                  {entry.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <label className="mt-1 text-xs text-ink-mid">port</label>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              openPreview();
            }}
          >
            <Input
              value={port}
              onChange={(event) => setPort(event.target.value)}
              placeholder="5173"
              inputMode="numeric"
              className="w-full font-mono"
              autoFocus
            />
          </form>
          {selected?.isolated ? (
            <p className="text-[11px] leading-4 text-warn">
              Isolated session: the server must bind 0.0.0.0 or the workspace
              IP — 127.0.0.1 is not reachable.
            </p>
          ) : null}
          <Button
            variant="primary"
            size="sm"
            className="mt-1 justify-center"
            disabled={!portValid}
            onClick={openPreview}
          >
            Open preview
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
