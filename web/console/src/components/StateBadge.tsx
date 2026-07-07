import { cn } from "@/lib/cn";
import type { CommandStatus, SandboxState } from "@/api/types";

type Tone = "ok" | "run" | "warn" | "idle" | "danger";

const toneClasses: Record<Tone, string> = {
  ok: "bg-ok-soft text-ok border-ok/30",
  run: "bg-run-soft text-run border-run/30",
  warn: "bg-warn-soft text-warn border-warn/40",
  idle: "bg-idle-soft text-idle border-idle/30",
  danger: "bg-danger-soft text-danger border-danger/30",
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
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-1.5 py-px text-[11px] font-medium leading-4",
        toneClasses[tone],
        className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {label ?? String(state)}
    </span>
  );
}
