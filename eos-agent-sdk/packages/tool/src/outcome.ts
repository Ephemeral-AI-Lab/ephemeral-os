import type { AgentRunId } from "@eos/contracts";
import type { z } from "zod";

const INTERNAL = Symbol("eos-agent-outcome-fn");

/** What the terminal handler receives alongside the payload. */
export interface SubmitCtx {
  runId: AgentRunId;
  /**
   * Stable per submission attempt (the toolUseId). Hosts MUST key
   * transactional transitions on it so handler retries are idempotent.
   */
  submissionId: string;
}

/** The handler's verdict: commit-and-finish, or send the model back in-run. */
export type SubmitVerdict<T> = { accept: T } | { reject: string };

/** The unwrapped factory product; workspace-internal. */
export interface AgentOutcomeBinding<T> {
  /** The terminal tool the model calls and hook matchers see. */
  name: string;
  description: string;
  schema: z.ZodType<T>;
  onSubmit: (payload: T, ctx: SubmitCtx) => Promise<SubmitVerdict<T>>;
}

/**
 * How a terminal-tool run completes — opaque; minted only by
 * `createAgentOutcomeFn`. Its presence on an `AgentSpec` selects
 * terminal-tool termination; its absence selects text mode.
 */
export interface AgentOutcomeFn<T = string> {
  readonly [INTERNAL]: AgentOutcomeBinding<T>;
}

/**
 * Mint the run's terminal contract: the factory owns the terminal tool's
 * identity (`name` is what the model calls and what hook matchers see;
 * `description` its docstring, derived from the schema when absent), the
 * payload schema, and the caller-owned submission handler. The default
 * `onSubmit` is the trivial validator — accept the payload unchanged.
 */
export function createAgentOutcomeFn<T>(spec: {
  name: string;
  description?: string;
  schema: z.ZodType<T>;
  onSubmit?: (payload: T, ctx: SubmitCtx) => Promise<SubmitVerdict<T>>;
}): AgentOutcomeFn<T> {
  const name = spec.name.trim();
  if (name.length === 0) {
    throw new Error("createAgentOutcomeFn requires a non-empty name");
  }
  return {
    [INTERNAL]: {
      name,
      description: spec.description ?? deriveDescription(spec.schema),
      schema: spec.schema,
      onSubmit: spec.onSubmit ?? ((payload) => Promise.resolve({ accept: payload })),
    },
  };
}

/** Workspace-internal accessor; the root package never re-exports it. */
export function unwrapAgentOutcomeFn<T>(
  fn: AgentOutcomeFn<T>,
): AgentOutcomeBinding<T> {
  return fn[INTERNAL];
}

function deriveDescription(schema: z.ZodType<unknown>): string {
  const base =
    "Finish the run by submitting its terminal outcome as this tool's input. A successful call ends the run.";
  const hint = (schema as { description?: string }).description;
  return hint === undefined ? base : `${base} ${hint}`;
}
