# Action Reference: submit_replan (cancel and redraft)

Use `submit_replan(new_tasks=[...], cancel_ids=[...])` when one or more of your direct siblings are stale and must be replaced. Cancelling a sibling cancels the entire node and its subtree automatically. Replacement tasks go into `new_tasks` and are inserted as direct children of this replanner, so downstream work remains blocked until recovery finishes.

## Task/Goal

- A direct sibling is working on invalidated assumptions or the wrong files.
- A shared dependency changed and a sibling's subtree is no longer valid.
- Adding corrective tasks alone would leave stale work running or conflicting.

## Avoid

- Never cancel DONE, FAILED, or CANCELLED tasks; terminal records are immutable.
- Never cancel the original failed `request_replan` task; it is immutable failure evidence even when it appears next to this replanner in the graph.
- Never try to cancel a non-sibling (e.g. a nested task inside a sibling's subtree). Cancel the sibling root instead and let the cascade handle the subtree.
- Do not cancel tasks without confirming they are actually stale.
- Do not replace a failed task with a new-file task for a missing module, shim, re-export module, or import bridge whose only evidence is a test import or collection error.

## Workflow

- Must confirm which direct siblings are actually stale before adding to `cancel_ids`.
- `cancel_ids` accepts only direct siblings of this replanner (same `parent_id`). The replanner cannot cancel itself or the original `request_replan` task. If the only failed neighbor is the original request-replan task, use `cancel_ids=[]`.
- If a failure names a missing import path, keep it as evidence unless non-test production evidence proves the absent file is the intended repository surface. Prefer an existing live owner or live boundary for the replacement.
- Replacement work that logically replaces a cancelled sibling belongs in `new_tasks`. If the replacement itself needs a hierarchy, make it a planner-role task under this replanner and let that planner author its own subtree on the next turn.
- Each replacement task must include `description`, a short planner-authored label under about 10 words.
- If a replacement planner-role task is needed, its spec must say that the planner submits with `submit_plan`, not `submit_replan`.
- If replacement work removes a repo file, its spec must name `daytona_delete_file`; if it relocates or renames a path, its spec must name `daytona_move_file`. Never tell a child to use CodeAct `rm`, `mv`, `unlink`, `shutil.rmtree`, or `shutil.move`.
- Replacement tasks must not depend on downstream tasks already blocked on this replanner; that creates a recovery-gate dependency cycle.
- Prefer `deps` ids that are local to this same `new_tasks` payload. Validator deps must be local to this payload. Use an existing task id only when fresh graph context proves the exact id is accepted by the current graph, schedulable, and not downstream of this replanner or the original failed task; otherwise omit that existing dep.
- If `new_tasks` contains 3 or more concrete non-planner replacement tasks, add one terminal `validator` task in this same `submit_replan(...)` payload. Its `deps` must cover those concrete tasks, and its spec must run the relevant broad verification after diagnostics.
- Each replacement `spec` must use numbered colon labels in this exact order: `1. Goal:`, `2. Environment:`, `3. Scope:`, `4. Context:`, `5. Acceptance Criteria:`. Do not use Markdown headings such as `## Goal`.
- Do not include `task_note`, `output`, `background`, `parent_id`, or any top-level field besides `new_tasks` and `cancel_ids`.
- Self-check the final payload before the single terminal call; do not use a failed `submit_replan(...)` attempt as a validation pass.
- Self-check that `cancel_ids` excludes the original failed task and that no replacement creates a test-derived missing path without non-test production evidence. A target count, collection blocker, standard re-export pattern, or similar in-scope filename is not an exception.
- Must call `context_changed_since()` before submitting if freshness moved.

## Expected Outcome

- Stale sibling work is replaced cleanly at this layer without duplicate or dangling work, and deeper subtrees are cleaned up by cascade.
