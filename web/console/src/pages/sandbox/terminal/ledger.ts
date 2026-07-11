import type { CommandOutput, CommandStatus } from "@/api/types";

/**
 * The command ledger is client-remembered: no listing op exists, so known
 * command ids (with command text and timestamps) persist in localStorage
 * per sandbox. A reload rebuilds the ledger from storage plus the
 * snapshot's in-flight executions.
 */
export interface LedgerEntry {
  localId: string;
  commandSessionId: string | null;
  cmd: string;
  workspaceSessionId: string | null;
  autoPublish: boolean;
  startedAt: number;
  status: CommandStatus;
  exitCode: number | null;
  endedAt: number | null;
  inlineOutput: string | null;
  publishRejected: boolean;
  publishRejectClass: string | null;
}

const LEDGER_CAP = 200;

function storageKey(sandboxId: string): string {
  return `eos-console:ledger:${sandboxId}`;
}

export function loadLedger(sandboxId: string): LedgerEntry[] {
  try {
    const raw = localStorage.getItem(storageKey(sandboxId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as LedgerEntry[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveLedger(sandboxId: string, entries: LedgerEntry[]) {
  try {
    localStorage.setItem(
      storageKey(sandboxId),
      JSON.stringify(entries.slice(-LEDGER_CAP)),
    );
  } catch {
    // localStorage full or unavailable: the ledger degrades to in-memory.
  }
}

export function entryFromExec(
  cmd: string,
  workspaceSessionId: string | null,
  output: CommandOutput,
): LedgerEntry {
  const running = output.status === "running";
  return {
    localId: crypto.randomUUID(),
    commandSessionId: output.command_session_id ?? null,
    cmd,
    workspaceSessionId: output.workspace_session_id ?? workspaceSessionId,
    autoPublish: workspaceSessionId === null,
    startedAt: Date.now(),
    status: output.status,
    exitCode: output.exit_code,
    endedAt: running ? null : Date.now(),
    inlineOutput: output.command_session_id ? null : output.output,
    publishRejected: output.publish_rejected === true,
    publishRejectClass: output.publish_reject_class ?? null,
  };
}

export function entryFromSnapshot(
  commandSessionId: string,
  workspaceSessionId: string,
): LedgerEntry {
  return {
    localId: crypto.randomUUID(),
    commandSessionId,
    cmd: `(running command ${commandSessionId} — started outside this browser)`,
    workspaceSessionId,
    autoPublish: false,
    startedAt: Date.now(),
    status: "running",
    exitCode: null,
    endedAt: null,
    inlineOutput: null,
    publishRejected: false,
    publishRejectClass: null,
  };
}

/**
 * In-memory transcript cache keyed by sandbox and command session id.
 * Survives tab switches (component unmounts) so a revisit catches up from
 * the last fetched offset instead of refetching from zero; a full reload
 * starts fresh, which re-pages from offset 0.
 */
export interface TranscriptState {
  lines: string[];
  fetchedTo: number;
  totalLines: number;
  status: CommandStatus;
  exitCode: number | null;
  publishRejected: boolean;
  publishRejectClass: string | null;
  error: string | null;
  tailPinned: boolean;
}

const transcripts = new Map<string, TranscriptState>();

export function transcriptFor(
  commandSessionId: string,
  sandboxId: string,
): TranscriptState {
  const key = JSON.stringify([sandboxId, commandSessionId]);
  let state = transcripts.get(key);
  if (!state) {
    state = {
      lines: [],
      fetchedTo: 0,
      totalLines: 0,
      status: "running",
      exitCode: null,
      publishRejected: false,
      publishRejectClass: null,
      error: null,
      tailPinned: true,
    };
    transcripts.set(key, state);
  }
  // Keep an existing hot-reload cache compatible with the current state
  // shape without ever sharing a transcript across sandbox keys.
  state.error ??= null;
  state.tailPinned ??= true;
  return state;
}

export function absorbOutput(
  state: TranscriptState,
  output: CommandOutput,
): TranscriptState {
  if (output.end_offset > output.start_offset && output.output.length > 0) {
    const incoming = output.output.split("\n");
    const start = Number(output.start_offset);
    for (let index = 0; index < incoming.length; index += 1) {
      state.lines[start + index] = incoming[index];
    }
    state.fetchedTo = Math.max(state.fetchedTo, Number(output.end_offset));
  }
  state.totalLines = Number(output.total_lines);
  state.status = output.status;
  state.exitCode = output.exit_code;
  if (output.publish_rejected === true) {
    state.publishRejected = true;
    state.publishRejectClass = output.publish_reject_class ?? null;
  }
  return state;
}
