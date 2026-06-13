import type { AgentOutcome, AgentRunId } from "eos-agent-sdk";

import {
  isPursuitEntityTerminal,
  mintPursuitId,
  type CreatePursuitInput,
  CreatePursuitInputSchema,
  type InitialUserMessage,
  type PlannerOutcomePayload,
  type PlannerSubmissionTarget,
  type PursuitHandle,
  type PursuitId,
  type PursuitSettlement,
  type SubmissionResult,
  type WorkerOutcomePayload,
  type WorkerSubmissionTarget,
} from "./contracts/pursuit.js";
import { createPursuitDatabase, type PursuitDb, type PursuitTransaction } from "./db/index.js";

import {
  claimLaunchable,
  plannerOutcome,
  stampAgentRunId,
  verifyClaimLaunchable,
  workerOutcome,
  type ClaimedLaunch,
  type PursuitAgents,
} from "./agent-launcher.js";
import { formatAttemptFailureReason } from "./attempt/context.js";
import type { ComposeLaunchContext } from "./context-engine/composer.js";
import {
  buildPlannerContextInput,
  buildWorkerContextInput,
} from "./context-engine/input.js";
import { buildPursuitContext, pursuitRootPath } from "./context-engine/projection/paths.js";
import { projectPursuitContextMirror } from "./context-engine/projection/mirror.js";
import { applyPlannerSettlement } from "./plan/transition.js";
import { cancelPursuit, createPursuitRows } from "./pursuit/transition.js";
import { loadPursuitTree, type PursuitTree } from "./pursuit-tree.js";
import { applyWorkItemSettlement } from "./work-item/transition.js";

const DEFAULT_MAX_ATTEMPTS = 2;

export interface PursuitServiceDependencies {
  db: PursuitDb;
  compose: ComposeLaunchContext;
  contextRoot: string;
  plannerAgentName: string;
  isRegisteredWorkerAgent: (agentName: string) => boolean;
  defaultMaxAttempts?: number;
  logMirrorFailure?: (pursuitId: PursuitId, error: unknown) => void;
}

/** The app-owned factory deps (spec §11): the service opens its own store and
 *  derives worker membership from the configured worker name. */
export interface OpenPursuitServiceDeps {
  plannerAgentName: string;
  workerAgentName: string;
  storePath: string;
  contextRoot: string;
  defaultMaxAttempts?: number;
  compose: ComposeLaunchContext;
}

export function openPursuitService(deps: OpenPursuitServiceDeps): PursuitService {
  return new PursuitService({
    db: createPursuitDatabase(deps.storePath),
    compose: deps.compose,
    contextRoot: deps.contextRoot,
    plannerAgentName: deps.plannerAgentName,
    isRegisteredWorkerAgent: (agentName) => agentName === deps.workerAgentName,
    ...(deps.defaultMaxAttempts !== undefined && {
      defaultMaxAttempts: deps.defaultMaxAttempts,
    }),
  });
}

interface PlannerSubmission {
  target: PlannerSubmissionTarget;
  payload: PlannerOutcomePayload;
  runId: AgentRunId;
  submissionId: string;
}

interface WorkerSubmission {
  target: WorkerSubmissionTarget;
  payload: WorkerOutcomePayload;
  runId: AgentRunId;
  submissionId: string;
}

interface TerminalResolver {
  promise: Promise<PursuitSettlement>;
  resolve(terminal: PursuitSettlement): void;
}

function terminalResolver(): TerminalResolver {
  let resolve!: (terminal: PursuitSettlement) => void;
  const promise = new Promise<PursuitSettlement>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

interface ActivePursuit {
  agents: PursuitAgents;
  controller: AbortController;
  terminal: TerminalResolver;
  cancelReason?: string;
}

export class PursuitService {
  readonly #deps: PursuitServiceDependencies;
  readonly #active = new Map<PursuitId, ActivePursuit>();

  constructor(deps: PursuitServiceDependencies) {
    this.#deps = deps;
  }

  async createPursuit(
    input: CreatePursuitInput,
    opts: { agents: PursuitAgents },
  ): Promise<PursuitHandle> {
    const parsedInput = CreatePursuitInputSchema.parse(input);
    const pursuitId = mintPursuitId();
    const active: ActivePursuit = {
      agents: opts.agents,
      controller: new AbortController(),
      terminal: terminalResolver(),
    };
    this.#active.set(pursuitId, active);
    await this.#mutate(pursuitId, (trx) =>
      createPursuitRows(trx, {
        pursuitId,
        parentRunId: null,
        input: parsedInput,
        maxAttempts: this.#deps.defaultMaxAttempts ?? DEFAULT_MAX_ATTEMPTS,
      }),
    );
    const displayId = pursuitRootPath(pursuitId);
    return {
      pursuitId: displayId,
      title: `pursuit ${displayId}: ${firstLine(parsedInput.pursuit_goal)}`,
      cancel: (reason = "pursuit_cancelled") => this.cancel(pursuitId, reason),
      done: active.terminal.promise,
    };
  }

  async cancel(pursuitId: PursuitId, reason: string): Promise<void> {
    const active = this.#active.get(pursuitId);
    if (active) {
      active.cancelReason = reason;
      active.controller.abort("pursuit_cancelled");
    }
    await this.#mutate(pursuitId, async (trx, tree) => {
      if (tree) await cancelPursuit(trx, tree.pursuit.id);
    });
  }

  /** The single successful-submission writer for planner outcomes (spec §11). */
  async submitPlannerOutcome(submission: PlannerSubmission): Promise<SubmissionResult> {
    const { target, payload } = submission;
    let error: string | undefined;
    await this.#mutate(target.pursuitId, async (trx, tree) => {
      if (!tree) {
        error = "unknown pursuit";
        return;
      }
      error = plannerSubmissionError(tree, target, payload, {
        isRegisteredWorkerAgent: this.#deps.isRegisteredWorkerAgent,
      });
      if (error !== undefined) return;
      await applyPlannerSettlement(trx, tree, target.planId, {
        kind: "submitted",
        payload,
      });
    });
    return error === undefined ? { ok: true } : { ok: false, error };
  }

  async submitWorkerOutcome(submission: WorkerSubmission): Promise<SubmissionResult> {
    const { target, payload } = submission;
    await this.#mutate(target.pursuitId, async (trx, tree) => {
      if (!tree) return;
      await applyWorkItemSettlement(trx, tree, target, {
        isPass: payload.is_pass,
        summary: payload.summary,
        outcome: payload.outcome,
      });
    });
    return { ok: true };
  }

  /**
   * Death/cancel synthesis observed at `run.outcome()`. A completed run already
   * settled its entity through `onSubmit`, so the observer never touches state
   * after success — it only synthesizes failed/cancelled settlements.
   */
  async reconcileRun(
    pursuitId: PursuitId,
    claim: ClaimedLaunch,
    outcome: AgentOutcome<unknown>,
  ): Promise<void> {
    if (outcome.status === "completed") return;
    await this.#synthesizeFailure(
      pursuitId,
      claim,
      `run settled '${outcome.status}' without a submission`,
    );
  }

  async #mutate(
    pursuitId: PursuitId,
    mutator: (
      trx: PursuitTransaction,
      tree: PursuitTree | null,
    ) => Promise<void> | void,
  ): Promise<void> {
    const { claims, before } = await this.#deps.db.transaction().execute(async (trx) => {
      const tree = await loadPursuitTree(trx, pursuitId);
      await mutator(trx, tree);
      return {
        claims: await claimLaunchable(trx, pursuitId, this.#deps.plannerAgentName),
        before: tree,
      };
    });
    const after = await loadPursuitTree(this.#deps.db, pursuitId);
    if (!after) return;
    this.#advanceAbortGenerationOnAttemptFailure(pursuitId, before, after);
    await this.#mirror(pursuitId, after);
    for (const claim of claims) {
      await this.#launchClaim(pursuitId, claim, after);
    }
    this.#resolveTerminal(pursuitId, after);
  }

  #advanceAbortGenerationOnAttemptFailure(
    pursuitId: PursuitId,
    before: PursuitTree | null,
    after: PursuitTree,
  ): void {
    const active = this.#active.get(pursuitId);
    if (!active || isPursuitEntityTerminal(after.pursuit.status)) return;
    const failedBefore = new Set(
      (before?.legs ?? [])
        .flatMap((leg) => leg.attempts)
        .filter((attempt) => attempt.status === "Failed")
        .map((attempt) => attempt.id),
    );
    const newlyFailed = after.legs
      .flatMap((leg) => leg.attempts)
      .some((attempt) => attempt.status === "Failed" && !failedBefore.has(attempt.id));
    if (!newlyFailed) return;
    active.controller.abort("attempt_failed");
    active.controller = new AbortController();
  }

  async #mirror(pursuitId: PursuitId, tree: PursuitTree): Promise<void> {
    try {
      await projectPursuitContextMirror(
        this.#deps.contextRoot,
        buildPursuitContext(tree),
      );
    } catch (error) {
      const log =
        this.#deps.logMirrorFailure ??
        ((id: PursuitId, cause: unknown): void => {
          console.warn(`pursuit ${id} context mirror write failed`, cause);
        });
      log(pursuitId, error);
    }
  }

  async #launchClaim(
    pursuitId: PursuitId,
    claim: ClaimedLaunch,
    tree: PursuitTree,
  ): Promise<void> {
    const active = this.#active.get(pursuitId);
    let messages: InitialUserMessage[];
    try {
      const input =
        claim.kind === "plan"
          ? buildPlannerContextInput(tree, claim)
          : buildWorkerContextInput(tree, claim);
      messages = await this.#deps.compose(
        claim.agentName,
        input,
        active?.controller.signal,
      );
      if (messages.length === 0) throw new Error("composer returned no initial messages");
    } catch (error) {
      await this.#synthesizeFailure(
        pursuitId,
        claim,
        `context_script_error: ${describeError(error)}`,
      );
      return;
    }

    const permitted = await verifyClaimLaunchable(this.#deps.db, claim);
    if (!permitted || !active) return;

    const run =
      claim.kind === "plan"
        ? active.agents
            .create(claim.agentName, plannerOutcome(this, plannerTarget(claim)))
            .start({ messages })
        : active.agents
            .create(claim.agentName, workerOutcome(this, workerTarget(claim)))
            .start({ messages });
    active.controller.signal.addEventListener("abort", () => {
      run.interrupt();
    });
    await stampAgentRunId(this.#deps.db, claim, run.runId);
    const stampedTree = await loadPursuitTree(this.#deps.db, pursuitId);
    if (stampedTree) await this.#mirror(pursuitId, stampedTree);
    void run
      .outcome()
      .then((outcome) => this.reconcileRun(pursuitId, claim, outcome))
      .catch(() => undefined);
  }

  async #synthesizeFailure(
    pursuitId: PursuitId,
    claim: ClaimedLaunch,
    reason: string,
  ): Promise<void> {
    await this.#mutate(pursuitId, async (trx, tree) => {
      if (!tree) return;
      if (claim.kind === "plan") {
        await applyPlannerSettlement(trx, tree, claim.planId, {
          kind: "failed",
          reason,
        });
        return;
      }
      await applyWorkItemSettlement(trx, tree, workerTarget(claim), {
        isPass: false,
        summary: reason,
        outcome: reason,
      });
    });
  }

  #resolveTerminal(pursuitId: PursuitId, tree: PursuitTree): void {
    const status = tree.pursuit.status;
    if (!isPursuitEntityTerminal(status)) return;
    const active = this.#active.get(pursuitId);
    if (!active) return;
    this.#active.delete(pursuitId);
    active.terminal.resolve({
      status,
      summary: terminalSummary(tree, active.cancelReason),
    });
  }
}

function plannerTarget(
  claim: Extract<ClaimedLaunch, { kind: "plan" }>,
): PlannerSubmissionTarget {
  return {
    pursuitId: claim.pursuitId,
    legId: claim.legId,
    attemptId: claim.attemptId,
    planId: claim.planId,
  };
}

function workerTarget(
  claim: Extract<ClaimedLaunch, { kind: "work_item" }>,
): WorkerSubmissionTarget {
  return {
    pursuitId: claim.pursuitId,
    legId: claim.legId,
    attemptId: claim.attemptId,
    workItemId: claim.workItemId,
    workItemKey: claim.workItemKey,
  };
}

function plannerSubmissionError(
  tree: PursuitTree,
  target: PlannerSubmissionTarget,
  payload: PlannerOutcomePayload,
  deps: { isRegisteredWorkerAgent(agentName: string): boolean },
): string | undefined {
  const leg = tree.legs.find((candidate) => candidate.id === target.legId);
  const attempt = leg?.attempts.find((candidate) => candidate.id === target.attemptId);
  if (!leg || !attempt) return "unknown leg attempt";

  if (
    tree.pursuit.legGoalMode === "predefined" &&
    (payload.leg_goal !== undefined || payload.next_leg_goal !== undefined)
  ) {
    return "predefined leg goals cannot be refocused or declare next_leg_goal";
  }

  const currentIds = new Set<string>();
  for (const item of payload.work_items) {
    if (currentIds.has(item.id)) return `duplicate work item id "${item.id}"`;
    currentIds.add(item.id);
    if (!deps.isRegisteredWorkerAgent(item.agent_name)) {
      return `work item "${item.id}" names unknown worker agent "${item.agent_name}"`;
    }
  }

  const allExisting = tree.legs.flatMap((candidateLeg) =>
    candidateLeg.attempts.flatMap((candidateAttempt) =>
      candidateAttempt.workItems.map((item) => ({
        leg: candidateLeg,
        attempt: candidateAttempt,
        item,
      })),
    ),
  );
  const existingInVersion = allExisting.filter(
    (entry) =>
      entry.leg.id === leg.id &&
      entry.attempt.isConsistentWithLegGoal &&
      entry.item.legGoalVersion === leg.legGoalVersion,
  );
  if (payload.leg_goal === undefined) {
    for (const item of payload.work_items) {
      if (existingInVersion.some((entry) => String(entry.item.id) === item.id)) {
        return `duplicate work item id "${item.id}" in current leg goal version`;
      }
    }
  }

  for (const item of payload.work_items) {
    for (const dependency of item.depends_on) {
      if (currentIds.has(dependency)) continue;
      if (payload.leg_goal !== undefined) {
        return "replacement leg_goal submissions cannot depend_on prior work items";
      }
      const matching = allExisting.filter(
        (entry) => String(entry.item.id) === dependency,
      );
      if (matching.length === 0) {
        return `work item "${item.id}" depends_on unknown id "${dependency}"`;
      }
      const existing = matching.find(
        (entry) =>
          entry.leg.id === leg.id &&
          entry.attempt.sequence < attempt.sequence &&
          entry.attempt.isConsistentWithLegGoal &&
          entry.item.legGoalVersion === leg.legGoalVersion,
      );
      if (existing) continue;
      const first = matching[0];
      if (first.leg.id !== leg.id) {
        return `work item "${item.id}" depends_on item from another leg`;
      }
      if (first.attempt.sequence >= attempt.sequence) {
        return `work item "${item.id}" depends_on future attempt item "${dependency}"`;
      }
      if (
        !first.attempt.isConsistentWithLegGoal ||
        first.item.legGoalVersion !== leg.legGoalVersion
      ) {
        return `work item "${item.id}" depends_on superseded leg-goal version item "${dependency}"`;
      }
    }
  }

  return currentGraphCycle(payload);
}

function currentGraphCycle(payload: PlannerOutcomePayload): string | undefined {
  const graph = new Map(
    payload.work_items.map((item) => [
      item.id,
      item.depends_on.filter((dependency) =>
        payload.work_items.some((candidate) => candidate.id === dependency),
      ),
    ]),
  );
  const done = new Set<string>();
  const visiting = new Set<string>();
  const hasCycle = (id: string): boolean => {
    if (done.has(id)) return false;
    if (visiting.has(id)) return true;
    visiting.add(id);
    for (const dependency of graph.get(id) ?? []) {
      if (hasCycle(dependency)) return true;
    }
    visiting.delete(id);
    done.add(id);
    return false;
  };
  for (const id of graph.keys()) {
    if (hasCycle(id)) return "work item depends_on contains a dependency cycle";
  }
  return undefined;
}

function terminalSummary(tree: PursuitTree, cancelReason?: string): string {
  switch (tree.pursuit.status) {
    case "Success": {
      const closing = tree.legs.at(-1)?.attempts.at(-1);
      return closing?.plan.summary ?? "pursuit completed";
    }
    case "Failed": {
      const reasons = [...tree.legs]
        .reverse()
        .flatMap((leg) => [...leg.attempts].reverse())
        .find((attempt) => attempt.failureReasons.length > 0)?.failureReasons;
      return reasons?.[0] ? formatAttemptFailureReason(reasons[0]) : "pursuit failed";
    }
    default:
      return cancelReason ?? "pursuit cancelled";
  }
}

function firstLine(text: string): string {
  return text.split("\n", 1)[0] ?? text;
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
