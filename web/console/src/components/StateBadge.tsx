import { Badge } from "@mantine/core";
import type { CommandStatus, SandboxState } from "@/api/types";
import { cn } from "@/lib/cn";

type Tone = "ok" | "run" | "warn" | "idle" | "danger";

const toneColors: Record<Tone, string> = {
  ok: "success",
  run: "eyeBlue",
  warn: "warning",
  idle: "warm",
  danger: "danger",
};

const sandboxTones: Record<SandboxState, Tone> = {
  ready: "ok",
  creating: "run",
  stopping: "warn",
  stopped: "idle",
  failed: "danger",
};

const commandTones: Record<CommandStatus, Tone> = {
  running: "run",
  ok: "ok",
  error: "danger",
  timed_out: "warn",
  cancelled: "idle",
};

export function StateBadge({
  state,
  label,
  className,
}: {
  state: SandboxState | CommandStatus | Tone;
  label?: string;
  className?: string;
}) {
  const tone: Tone =
    state in sandboxTones
      ? sandboxTones[state as SandboxState]
      : state in commandTones
        ? commandTones[state as CommandStatus]
        : (state as Tone);
  return (
    <Badge
      className={cn("inline-flex", className)}
      color={toneColors[tone]}
      variant="light"
      leftSection={
        <span
          aria-hidden
          style={{ width: 6, height: 6, borderRadius: "50%", backgroundColor: "currentColor" }}
        />
      }
    >
      {label ?? String(state)}
    </Badge>
  );
}
