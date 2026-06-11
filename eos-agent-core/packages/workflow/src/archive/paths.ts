import type {
  IterationId,
  WorkflowEntityRunStatus,
  WorkflowId,
} from "@eos/contracts";

import { archivedDeclarationFiles, attemptFieldFiles } from "../attempt/context.js";
import type { AttemptState } from "../attempt/state.js";
import { iterationFieldFiles } from "../iteration/context.js";
import type { IterationState } from "../iteration/state.js";
import { workItemFieldFiles } from "../work-item/context.js";
import { workflowFieldFiles } from "../workflow/context.js";
import type { WorkflowTree } from "../workflow-tree.js";

/** Plans are execution state, not rendered context entities (§2.1). */
export type ContextEntityKind =
  | "workflow"
  | "iteration"
  | "attempt"
  | "work_item";

/** The owning entity behind a path: status and summary ride the DTO layer. */
export interface ContextEntityRef {
  readonly kind: ContextEntityKind;
  readonly id: string;
  readonly status: WorkflowEntityRunStatus;
  /** First line of the entity's summary field, where one exists. */
  readonly summaryFirstLine: string | null;
}

export interface ContextFileEntry {
  readonly owner: ContextEntityRef;
  /** The field text, verbatim - no stamp, no status line. */
  readonly content: string;
}

export interface ContextDirEntry {
  readonly owner: ContextEntityRef;
  /** True for paths inside an `archived/` subtree. */
  readonly archived: boolean;
}

/**
 * The §9 context path universe over one frozen tree. Paths are relative to
 * the workflow root (`""` is the root directory); `rootPath` carries the
 * `workflow_<id>` segment used by `context_path` DTO fields and the disk
 * mirror.
 */
export interface WorkflowContext {
  readonly workflowId: WorkflowId;
  readonly rootPath: string;
  readonly latestIterationId: IterationId | null;
  readonly files: ReadonlyMap<string, ContextFileEntry>;
  readonly directories: ReadonlyMap<string, ContextDirEntry>;
}

export function workflowRootPath(workflowId: WorkflowId): string {
  return `workflow_${workflowId}`;
}

export function iterationDirName(iterationId: IterationId): string {
  return `iteration_${iterationId}`;
}

/** Live vs archived attempt address (§2.8): drift relocates the folder. */
export function attemptDirPath(
  iteration: IterationState,
  attempt: AttemptState,
): string {
  const base = iterationDirName(iteration.id);
  return attempt.isConsistentWithIterationFocus
    ? `${base}/attempt_${attempt.id}`
    : `${base}/archived/attempt_${attempt.id}`;
}

/** Build the whole §9 universe: files, directories, archive sections. */
export function buildWorkflowContext(tree: WorkflowTree): WorkflowContext {
  const files = new Map<string, ContextFileEntry>();
  const directories = new Map<string, ContextDirEntry>();

  const workflowRef: ContextEntityRef = {
    kind: "workflow",
    id: tree.workflow.id,
    status: tree.workflow.status,
    summaryFirstLine: null,
  };
  directories.set("", { owner: workflowRef, archived: false });
  for (const file of workflowFieldFiles(tree.workflow, tree.iterations)) {
    files.set(file.name, { owner: workflowRef, content: file.content });
  }

  tree.iterations.forEach((iteration) => {
    const iterationRef: ContextEntityRef = {
      kind: "iteration",
      id: iteration.id,
      status: iteration.status,
      summaryFirstLine: null,
    };

    const iterationDir = iterationDirName(iteration.id);
    directories.set(iterationDir, { owner: iterationRef, archived: false });
    for (const file of iterationFieldFiles(iteration)) {
      files.set(`${iterationDir}/${file.name}`, {
        owner: iterationRef,
        content: file.content,
      });
    }

    for (const attempt of iteration.attempts) {
      const archived = !attempt.isConsistentWithIterationFocus;
      const attemptDir = attemptDirPath(iteration, attempt);
      const attemptRef: ContextEntityRef = {
        kind: "attempt",
        id: attempt.id,
        status: attempt.status,
        summaryFirstLine: firstLine(attempt.plan.summary),
      };
      if (archived) {
        directories.set(`${iterationDir}/archived`, {
          owner: iterationRef,
          archived: true,
        });
      }
      directories.set(attemptDir, { owner: attemptRef, archived });

      for (const file of attemptFieldFiles(attempt)) {
        files.set(`${attemptDir}/${file.name}`, {
          owner: attemptRef,
          content: file.content,
        });
      }
      // The drifted declarer carries its superseded declaration (§2.8).
      if (archived) {
        for (const file of archivedDeclarationFiles(attempt)) {
          files.set(`${attemptDir}/${file.name}`, {
            owner: attemptRef,
            content: file.content,
          });
        }
      }

      for (const item of attempt.workItems) {
        const itemRef: ContextEntityRef = {
          kind: "work_item",
          id: item.id,
          status: item.status,
          summaryFirstLine: firstLine(item.summary),
        };
        const itemDir = `${attemptDir}/work_item_${item.id}`;
        directories.set(itemDir, { owner: itemRef, archived });
        for (const file of workItemFieldFiles(item)) {
          files.set(`${itemDir}/${file.name}`, {
            owner: itemRef,
            content: file.content,
          });
        }
      }
    }
  });

  return {
    workflowId: tree.workflow.id,
    rootPath: workflowRootPath(tree.workflow.id),
    latestIterationId: tree.iterations.at(-1)?.id ?? null,
    files,
    directories,
  };
}

function firstLine(text: string | null): string | null {
  if (text === null) return null;
  return text.split("\n", 1)[0] ?? text;
}
