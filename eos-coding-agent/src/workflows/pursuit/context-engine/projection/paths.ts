import type {
  LegId,
  PursuitContextEntityStatus,
  PursuitId,
} from "../../contracts/pursuit.js";

import { supersededDeclarationFiles, attemptFieldFiles } from "../../attempt/context.js";
import type { AttemptState } from "../../attempt/state.js";
import { legFieldFiles } from "../../leg/context.js";
import type { LegState } from "../../leg/state.js";
import { workItemFieldFiles } from "../../work-item/context.js";
import { pursuitFieldFiles } from "../../pursuit/context.js";
import type { PursuitTree } from "../../pursuit-tree.js";

/** Plans are execution state, not rendered context entities (§2.1). */
export type ContextEntityKind =
  | "pursuit"
  | "leg"
  | "attempt"
  | "work_item";

/** The owning entity behind a path: status and summary ride the DTO layer. */
export interface ContextEntityRef {
  readonly kind: ContextEntityKind;
  readonly id: string;
  readonly status: PursuitContextEntityStatus;
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
  /** True for paths inside an `superseded/` subtree. */
  readonly superseded: boolean;
}

/**
 * The §9 context path universe over one frozen tree. Paths are relative to
 * the pursuit root (`""` is the root directory); `rootPath` carries the
 * `pursuit_<id>` segment used by `context_path` DTO fields and the disk
 * mirror.
 */
export interface PursuitContext {
  readonly pursuitId: PursuitId;
  readonly rootPath: string;
  readonly latestLegId: LegId | null;
  readonly files: ReadonlyMap<string, ContextFileEntry>;
  readonly directories: ReadonlyMap<string, ContextDirEntry>;
}

export function pursuitRootPath(pursuitId: PursuitId): string {
  return `pursuit_${pursuitId}`;
}

export function legDirName(legId: LegId): string {
  return `leg_${legId}`;
}

/** Live vs superseded attempt address (§2.8): drift relocates the folder. */
export function attemptDirPath(
  leg: LegState,
  attempt: AttemptState,
): string {
  const base = legDirName(leg.id);
  return attempt.isConsistentWithLegGoal
    ? `${base}/attempt_${attempt.id}`
    : `${base}/superseded/attempt_${attempt.id}`;
}

/** Build the whole §9 universe: files, directories, archive sections. */
export function buildPursuitContext(tree: PursuitTree): PursuitContext {
  const files = new Map<string, ContextFileEntry>();
  const directories = new Map<string, ContextDirEntry>();

  const pursuitRef: ContextEntityRef = {
    kind: "pursuit",
    id: tree.pursuit.id,
    status: tree.pursuit.status,
    summaryFirstLine: null,
  };
  directories.set("", { owner: pursuitRef, superseded: false });
  for (const file of pursuitFieldFiles(tree.pursuit, tree.legs)) {
    files.set(file.name, { owner: pursuitRef, content: file.content });
  }

  tree.legs.forEach((leg) => {
    const legRef: ContextEntityRef = {
      kind: "leg",
      id: leg.id,
      status: leg.status,
      summaryFirstLine: null,
    };

    const legDir = legDirName(leg.id);
    directories.set(legDir, { owner: legRef, superseded: false });
    for (const file of legFieldFiles(leg)) {
      files.set(`${legDir}/${file.name}`, {
        owner: legRef,
        content: file.content,
      });
    }

    for (const attempt of leg.attempts) {
      const superseded = !attempt.isConsistentWithLegGoal;
      const attemptDir = attemptDirPath(leg, attempt);
      const attemptRef: ContextEntityRef = {
        kind: "attempt",
        id: attempt.id,
        status: attempt.status,
        summaryFirstLine: firstLine(attempt.plan.summary),
      };
      if (superseded) {
        directories.set(`${legDir}/superseded`, {
          owner: legRef,
          superseded: true,
        });
      }
      directories.set(attemptDir, { owner: attemptRef, superseded });

      for (const file of attemptFieldFiles(attempt)) {
        files.set(`${attemptDir}/${file.name}`, {
          owner: attemptRef,
          content: file.content,
        });
      }
      // The drifted declarer carries its superseded declaration (§2.8).
      if (superseded) {
        for (const file of supersededDeclarationFiles(attempt)) {
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
        directories.set(itemDir, { owner: itemRef, superseded });
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
    pursuitId: tree.pursuit.id,
    rootPath: pursuitRootPath(tree.pursuit.id),
    latestLegId: tree.legs.at(-1)?.id ?? null,
    files,
    directories,
  };
}

function firstLine(text: string | null): string | null {
  if (text === null) return null;
  return text.split("\n", 1)[0] ?? text;
}
