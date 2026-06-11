import {
  isWorkflowEntityTerminal,
  mintWorkflowId,
  type AgentRunId,
  type DelegateWorkflowInput,
  type DelegatedWorkflow,
  type InitialUserMessage,
  type PlannerOutcomePayload,
  type SubmissionBinding,
  type SubmissionResult,
  type WorkerOutcomePayload,
  type WorkflowId,
  type WorkflowTerminal,
} from "@eos/contracts";
import type { WorkflowDb, WorkflowTransaction } from "@eos/db";

import { buildWorkflowContext } from "./archive/paths.js";
import type { ComposeLaunchContext } from "./context-engine/composer.js";
import {
  buildPlannerContextInput,
  buildWorkerContextInput,
} from "./context-engine/input.js";
import { projectWorkflowContextMirror } from "./context-projection.js";
import {
  claimLaunchable,
  stampAgentRunId,
  verifyClaimLaunchable,
  type AgentLaunchPort,
  type ClaimedLaunch,
  type LaunchSettlement,
} from "./launcher.js";
import { reconcilePlan } from "./plan/transitions.js";
import { reconcileWorkItem } from "./work-item/transitions.js";
import { cancelWorkflow, createWorkflow } from "./workflow/transitions.js";
import { loadWorkflowTree, type WorkflowTree } from "./workflow-tree.js";

/** Rust `AttemptBudget` parity; `delegate_workflow` may override per call. */
const DEFAULT_MAX_ATTEMPTS = 2;

export interface WorkflowServiceDependencies {
  db: WorkflowDb;
  port: AgentLaunchPort;
  /** The §2.11 composer seam; called after commit, before `port.launch`. */
  compose: ComposeLaunchContext;
  /** §2.17 mirror root; the universe lands under `<contextRoot>/workflow_<id>/`. */
  contextRoot: string;
  /** The profile every plan claim launches. */
  plannerAgentName: string;
  /** Materialization rule: `agent_name` must name a registered worker profile. */
  isRegisteredWorkerAgent: (agentName: string) => boolean;
  defaultMaxAttempts?: number;
  /** Mirror write failures are non-fatal: logged, healed by the next mutation. */
  logMirrorFailure?: (workflowId: WorkflowId, error: unknown) => void;
}

interface TerminalResolver {
  promise: Promise<WorkflowTerminal>;
  resolve(terminal: WorkflowTerminal): void;
}

function terminalResolver(): TerminalResolver {
  let resolve!: (terminal: WorkflowTerminal) => void;
  const promise = new Promise<WorkflowTerminal>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

interface ActiveWorkflow {
  /** Current abort generation; all child launches share its signal (§2.21). */
  controller: AbortController;
  terminal: TerminalResolver;
  cancelReason?: string;
}

/**
 * The package's only public construction surface. No `WorkflowCell`, no
 * `liveRuns`, no in-memory queues (§2.21): ordering and deduplication are
 * DB facts; the service keeps only a terminal resolver and an abort
 * controller per active workflow.
 */
export class WorkflowService {
  readonly #deps: WorkflowServiceDependencies;
  readonly #active = new Map<WorkflowId, ActiveWorkflow>();

  constructor(deps: WorkflowServiceDependencies) {
    this.#deps = deps;
  }

  async delegate(
    input: DelegateWorkflowInput,
    parentRunId: AgentRunId,
  ): Promise<DelegatedWorkflow> {
    const workflowId = mintWorkflowId();
    const active: ActiveWorkflow = {
      controller: new AbortController(),
      terminal: terminalResolver(),
    };
    this.#active.set(workflowId, active);
    await this.#mutate(workflowId, (trx) =>
      createWorkflow(trx, {
        workflowId,
        parentRunId,
        originalGoal: input.goal,
        maxAttempts:
          input.max_attempts ?? this.#deps.defaultMaxAttempts ?? DEFAULT_MAX_ATTEMPTS,
      }),
    );
    const goalLine = input.goal.split("\n", 1)[0] ?? input.goal;
    return {
      workflowId,
      terminal: active.terminal.promise,
      cancel: (reason) => this.cancel(workflowId, reason),
      describe: () => goalLine,
    };
  }

  /**
   * The Phase 05 §8 cascade, durable teardown included: abort the workflow
   * signal, mark every non-terminal entity `Cancelled` in one transaction,
   * mirror, and resolve the terminal `Cancelled`. Resolves only after the
   * cascade committed; terminal rows no-op, so racing cancels are harmless.
   */
  async cancel(workflowId: WorkflowId, reason: string): Promise<void> {
    const active = this.#active.get(workflowId);
    if (active) {
      active.cancelReason = reason;
      active.controller.abort("workflow_cancelled");
    }
    await this.#mutate(workflowId, async (trx, tree) => {
      if (tree) await cancelWorkflow(trx, tree.workflow.id);
    });
  }

  /**
   * Every mutation rides this pipeline: one transaction (fresh tree load,
   * the §2.22 cascade, one `claimLaunchable` sweep), then commit -> abort
   * generation policy -> §2.17 mirror -> guarded launches -> terminal
   * resolution, strictly in that order.
   */
  async #mutate(
    workflowId: WorkflowId,
    mutator: (
      trx: WorkflowTransaction,
      tree: WorkflowTree | null,
    ) => Promise<void> | void,
  ): Promise<void> {
    const { claims, before } = await this.#deps.db.transaction().execute(async (trx) => {
      const tree = await loadWorkflowTree(trx, workflowId);
      await mutator(trx, tree);
      return {
        claims: await claimLaunchable(trx, workflowId, this.#deps.plannerAgentName),
        before: tree,
      };
    });
    const after = await loadWorkflowTree(this.#deps.db, workflowId);
    if (!after) return;
    this.#advanceAbortGenerationOnAttemptFailure(workflowId, before, after);
    await this.#mirror(workflowId, after);
    for (const claim of claims) {
      await this.#launchClaim(workflowId, claim, after);
    }
    this.#resolveTerminal(workflowId, after);
  }

  /**
   * §2.20: the mutation that failed an attempt cancelled its remaining
   * work in-transaction; here the workflow abort generation advances so
   * the cancelled items' live runs observe cancellation through the shared
   * signal, while later launches (the retry planner) get a fresh one.
   */
  #advanceAbortGenerationOnAttemptFailure(
    workflowId: WorkflowId,
    before: WorkflowTree | null,
    after: WorkflowTree,
  ): void {
    const active = this.#active.get(workflowId);
    if (!active || isWorkflowEntityTerminal(after.workflow.status)) return;
    const failedBefore = new Set(
      (before?.iterations ?? [])
        .flatMap((iteration) => iteration.attempts)
        .filter((attempt) => attempt.status === "Failed")
        .map((attempt) => attempt.id),
    );
    const newlyFailed = after.iterations
      .flatMap((iteration) => iteration.attempts)
      .some((attempt) => attempt.status === "Failed" && !failedBefore.has(attempt.id));
    if (!newlyFailed) return;
    active.controller.abort("attempt_failed");
    active.controller = new AbortController();
  }

  async #mirror(workflowId: WorkflowId, tree: WorkflowTree): Promise<void> {
    try {
      await projectWorkflowContextMirror(
        this.#deps.contextRoot,
        buildWorkflowContext(tree),
      );
    } catch (error) {
      const log =
        this.#deps.logMirrorFailure ??
        ((id: WorkflowId, cause: unknown): void => {
          console.warn(`workflow ${id} context mirror write failed`, cause);
        });
      log(workflowId, error);
    }
  }

  /** The §10 post-commit pipeline for one claim: compose -> guard -> launch. */
  async #launchClaim(
    workflowId: WorkflowId,
    claim: ClaimedLaunch,
    tree: WorkflowTree,
  ): Promise<void> {
    const active = this.#active.get(workflowId);
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
      if (messages.length === 0) {
        throw new Error("composer returned no initial messages");
      }
    } catch (error) {
      // §2.14: the launch never happens; the ordinary retry path runs.
      await this.#synthesizeFailure(
        workflowId,
        claim,
        `context_script_error: ${describeError(error)}`,
      );
      return;
    }

    const permitted = await verifyClaimLaunchable(this.#deps.db, claim);
    if (!permitted) return; // a cancel or settlement won the race; skip stale work

    const launched = this.#deps.port.launch(claim.agentName, messages, {
      submission: this.#buildBinding(workflowId, claim),
      ...(active && { signal: active.controller.signal }),
      parent: tree.workflow.parentRunId,
    });
    await stampAgentRunId(this.#deps.db, claim, launched.runId);
    void launched.outcome
      .catch((): LaunchSettlement => ({ status: "failed" }))
      .then((settlement) => this.#onSettlement(workflowId, claim, settlement))
      .catch(() => undefined);
  }

  /** The §2.19 entity-bound seam handed to the child's terminal tool. */
  #buildBinding(workflowId: WorkflowId, claim: ClaimedLaunch): SubmissionBinding {
    if (claim.kind === "plan") {
      return {
        kind: "planner",
        submit: (payload) => this.#submitPlanner(workflowId, claim, payload),
      };
    }
    return {
      kind: "worker",
      submit: (payload) => this.#submitWorker(workflowId, claim, payload),
    };
  }

  async #submitPlanner(
    workflowId: WorkflowId,
    claim: Extract<ClaimedLaunch, { kind: "plan" }>,
    payload: PlannerOutcomePayload,
  ): Promise<SubmissionResult> {
    let error: string | undefined;
    await this.#mutate(workflowId, async (trx, tree) => {
      if (!tree) {
        error = "unknown workflow";
        return;
      }
      // Materialization rules (§2.15): checked against fresh state, no
      // mutation on error - the agent corrects in-run, no attempt burns.
      const iteration = tree.iterations.find((it) => it.id === claim.iterationId);
      if (
        (iteration?.focus ?? null) === null &&
        payload.iteration_focus === undefined
      ) {
        error =
          "the iteration has no focus yet: the first planner submission must declare iteration_focus";
        return;
      }
      for (const item of payload.work_items) {
        if (!this.#deps.isRegisteredWorkerAgent(item.agent_name)) {
          error = `work item "${item.id}" names unknown worker agent "${item.agent_name}"`;
          return;
        }
      }
      await reconcilePlan(trx, tree, claim.planId, { kind: "submitted", payload });
    });
    return error === undefined ? { ok: true } : { ok: false, error };
  }

  async #submitWorker(
    workflowId: WorkflowId,
    claim: Extract<ClaimedLaunch, { kind: "work_item" }>,
    payload: WorkerOutcomePayload,
  ): Promise<SubmissionResult> {
    await this.#mutate(workflowId, async (trx, tree) => {
      if (!tree) return;
      await reconcileWorkItem(trx, tree, claim, {
        isPass: payload.is_pass,
        summary: payload.summary,
        outcome: payload.outcome,
      });
    });
    return { ok: true };
  }

  /**
   * Settlement consumption reduced to death synthesis (§2.19): an entity
   * still `Running` when its run settles gets a synthesized failed
   * submission through the same leaf cascade; a terminal entity (its
   * submission landed in-run, or a cancel won) no-ops on the terminal guard.
   */
  async #onSettlement(
    workflowId: WorkflowId,
    claim: ClaimedLaunch,
    settlement: LaunchSettlement,
  ): Promise<void> {
    const reason = `run settled '${settlement.status}' without a submission`;
    await this.#synthesizeFailure(workflowId, claim, reason);
  }

  async #synthesizeFailure(
    workflowId: WorkflowId,
    claim: ClaimedLaunch,
    reason: string,
  ): Promise<void> {
    await this.#mutate(workflowId, async (trx, tree) => {
      if (!tree) return;
      if (claim.kind === "plan") {
        await reconcilePlan(trx, tree, claim.planId, { kind: "failed", reason });
        return;
      }
      await reconcileWorkItem(trx, tree, claim, {
        isPass: false,
        summary: reason,
        outcome: reason,
      });
    });
  }

  #resolveTerminal(workflowId: WorkflowId, tree: WorkflowTree): void {
    const status = tree.workflow.status;
    if (!isWorkflowEntityTerminal(status)) return;
    const active = this.#active.get(workflowId);
    if (!active) return;
    this.#active.delete(workflowId);
    active.terminal.resolve({
      status,
      summary: terminalSummary(tree, active.cancelReason),
    });
  }
}

function terminalSummary(tree: WorkflowTree, cancelReason?: string): string {
  switch (tree.workflow.status) {
    case "Success": {
      const closing = tree.iterations.at(-1)?.attempts.at(-1);
      return closing?.plan.summary ?? "workflow completed";
    }
    case "Failed": {
      const failReason = [...tree.iterations]
        .reverse()
        .flatMap((iteration) => [...iteration.attempts].reverse())
        .find((attempt) => attempt.failReason !== null)?.failReason;
      return failReason ?? "workflow failed";
    }
    default:
      return cancelReason ?? "workflow cancelled";
  }
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
