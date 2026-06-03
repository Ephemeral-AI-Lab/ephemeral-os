export const meta = {
  name: 'rust-parity-audit',
  description: 'Side-by-side parity audit: Rust agent-core + sandbox vs Python/docs ground truth',
  phases: [
    { title: 'Investigate', detail: 'Per-area bilateral ground-truth->Rust audit with file:line evidence' },
    { title: 'Verify', detail: 'Independent re-confirmation of every named invariant (catch false matches + false alarms)' },
    { title: 'Synthesize', detail: 'Cross-domain disparity report + coverage matrix' },
  ],
}

// ---------------------------------------------------------------------------
// Ground-truth note: Python sandbox internals (layer_stack/occ/overlay/
// ephemeral_workspace/isolated_workspace/daemon) were DELETED in commit
// 37c13f3db and MATERIALIZED for this run at /tmp/oldpy/backend/src/sandbox/...
// In-tree Python (engine/workflow/tools/runtime/...) lives at backend/src/...
// Rust: sandbox/ workspace (sandbox internals) + agent-core/crates (agent core).
// Rust carries `// PORT backend/src/...:LINE` anchors mapping each item to Python.
// ---------------------------------------------------------------------------

const AREAS = [
  // ===================== SANDBOX =====================
  {
    key: 'overlay', domain: 'sandbox',
    title: 'Overlay (full-FS overlay, only target workspace mounted over layer stacks)',
    docs: ['docs/architecture/sandbox/overlay.html', 'docs/architecture/sandbox/space-model.html'],
    python: ['/tmp/oldpy/backend/src/sandbox/overlay/ (kernel_mount.py, lifecycle.py, capture.py, handle.py, path_change.py, mount_syscalls.py, writable_dirs.py, namespace_runner.py, namespace_entrypoint.py, subprocess_runner.py)'],
    git: true,
    rust: ['sandbox/crates/eos-overlay/src/ (lib.rs, kernel_mount.rs, path_change.rs, writable_dirs.rs, error.rs)'],
    invariants: [
      'The overlay presents the full sandbox filesystem, but ONLY the target workspace subtree is mounted as overlayfs over the layer stack; the rest is passthrough.',
      'Overlay mounts over the LATEST snapshot = the ordered collection of layer 0 .. layer n (lowerdirs), with an upperdir for new writes.',
      'Writes land in the upperdir; the stacked layers are read-only lowerdirs.',
      'Overlay free / teardown releases the head layer (layer n) it had mounted.',
      'Path-change capture from the overlay upperdir (added/modified/deleted/whiteout) matches the Python semantics.',
      'Writable-dir / passthrough policy is preserved.',
    ],
  },
  {
    key: 'layerstack', domain: 'sandbox',
    title: 'LayerStack (layers on workspace base, snapshot view, lease semantics)',
    docs: ['docs/architecture/sandbox/layerstack.html', '.omc/wiki layerstack-lease-semantics + layerstack-squash-workflow pages (use wiki_read or .omc/wiki/*.md)'],
    python: ['/tmp/oldpy/backend/src/sandbox/layer_stack/ (stack.py, lease.py, manifest.py, layer_index.py, view.py, workspace_base.py, workspace_binding.py, storage_lock.py, publisher.py, transaction.py, commit_staging.py, changes.py, paths.py)'],
    git: true,
    rust: ['sandbox/crates/eos-layerstack/src/ (lib.rs, stack.rs, lease.rs, workspace_base.rs, workspace_binding.rs, storage_lock.rs)'],
    invariants: [
      'Layers build on top of the workspace base; a snapshot is the ordered sequence layer 0 .. layer n.',
      'The overlay mounts the LATEST snapshot; the head layer (layer n) is released when the overlay frees it.',
      'Lease model preserved: distinguish leased_layers (any retained layer) vs lease_head_layers (a lease pinned at the head) — see the wiki pages.',
      'Publishing a write appends a NEW layer atomically (transaction + storage lock); no in-place mutation of existing layers.',
      'workspace_binding maps a workspace -> its base + active manifest; binding lookup is preserved.',
      'Manifest depth / layer-index ordering is preserved.',
    ],
  },
  {
    key: 'squash', domain: 'sandbox',
    title: 'Squash algorithm (depth limit, segment around lease heads, deferred GC)',
    docs: ['docs/architecture/sandbox/layerstack.html (squash section)', '.omc/wiki/layerstack-squash-workflow-and-deferred-gc.md'],
    python: ['/tmp/oldpy/backend/src/sandbox/layer_stack/squash.py', '/tmp/oldpy/backend/src/sandbox/layer_stack/stack.py (trigger)', '/tmp/oldpy/backend/src/sandbox/layer_stack/lease.py'],
    git: true,
    rust: ['sandbox/crates/eos-layerstack/src/squash.rs', 'sandbox/crates/eos-layerstack/src/stack.rs (trigger + apply)', 'sandbox/crates/eos-layerstack/src/lease.rs'],
    invariants: [
      'Squash is TRIGGERED when the layer count reaches a depth limit. The user believes the limit is ~100 — find the ACTUAL constant/config (e.g. max_depth) and report its real value + whether it is hardcoded or configurable; a divergence from "100" is itself a finding.',
      'Squash merges consecutive NON-leased layers (and non-head leased runs) into checkpoint segments, segmenting AROUND lease heads.',
      'Lease-HEAD layers are NOT folded during squash; they are squashed only AFTER the lease releases (deferred GC).',
      'Guards preserved: max_depth and a minimum-reduction threshold (min_reduction) gate whether a squash actually runs.',
      'Squash is NON-destructive until the retaining lease releases: it pointer-swaps a shorter manifest while lower layers stay on disk until GC.',
    ],
    owner_notes: 'Highest-signal exact-constant area. Quote the literal depth limit + comparison operators from BOTH sides.',
  },
  {
    key: 'occ', domain: 'sandbox',
    title: 'OCC gate (commit gating; gitignore/outside-workspace direct merge; git-tracked through gate)',
    docs: ['docs/architecture/sandbox/occ.html'],
    python: ['/tmp/oldpy/backend/src/sandbox/occ/ (service.py, gitignore.py, commit_queue.py, changeset.py, changeset_preparation.py, commit_transaction.py, content_hashing.py, overlay_change_conversion.py, path_staging.py, layer_stack_adapter.py, client.py, ports.py, maintenance.py)'],
    git: true,
    rust: ['sandbox/crates/eos-occ/src/ (lib.rs, service.rs, commit_queue.rs, route.rs, overlay_change_conversion.rs, error.rs)'],
    invariants: [
      'OCC is the GATE that decides what is added/committed to the workspace.',
      'gitignored items OR changes OUTSIDE the workspace are DIRECTLY merged (they bypass the OCC conflict gate).',
      'git-tracked (included) items are merged THROUGH the OCC gate (optimistic concurrency / conflict detection / serialization).',
      'A commit queue serializes commits; content hashing detects changes / conflicts.',
      'Overlay change -> changeset conversion is preserved (overlay_change_conversion).',
      'Path routing (route.rs) classifies each path: gitignored vs tracked vs outside-workspace, matching gitignore.py semantics.',
    ],
    owner_notes: 'Owns the gitignore/outside-vs-tracked routing invariant. Quote the routing predicate from both sides.',
  },
  {
    key: 'ephemeral_workspace', domain: 'sandbox',
    title: 'Ephemeral workspace lifecycle (per tool call, upperdir->OCC merge, discard on lease release)',
    docs: ['docs/architecture/sandbox/workspaces.html', 'docs/architecture/sandbox/workflow-cookbook.html', 'docs/architecture/tools/sandbox.html'],
    python: ['/tmp/oldpy/backend/src/sandbox/ephemeral_workspace/ (pipeline.py, pipeline_registry.py, workspace_publish.py, events.py, operation_overlay.py)', '/tmp/oldpy/backend/src/sandbox/daemon/workspace_tool/dispatch.py'],
    git: true,
    rust: ['sandbox/crates/eos-daemon/src/ (command.rs, dispatcher.rs)', 'sandbox/crates/eos-overlay + eos-occ + eos-layerstack integration', 'agent-core/crates/eos-sandbox-host/src/daemon_client.rs'],
    invariants: [
      'A fresh ephemeral workspace overlay is created PER tool call (mainly exec_command).',
      'Writes during the call land in the overlay upperdir.',
      'On success, the upperdir changes are sent to OCC for MERGE into the shared workspace.',
      'The ephemeral overlay / layer lease is released and the overlay DISCARDED after the call.',
      'Shared-workspace read_file/write_file/edit_file use daemon-owned LayerStack/OCC fast paths when a workspace binding exists; shell/search/plugin ops use the overlay pipeline and publish through OCC-gated paths.',
    ],
  },
  {
    key: 'isolated_workspace', domain: 'sandbox',
    title: 'Isolated workspace (isolated network, persistent upperdir, never OCC-merged, teardown on exit)',
    docs: ['docs/architecture/tools/isolated-workspace.html', 'docs/architecture/sandbox/workspaces.html'],
    python: ['backend/src/sandbox/host/isolated_workspace_lifecycle.py (IN-TREE)', '/tmp/oldpy/backend/src/sandbox/isolated_workspace/ (pipeline.py, network.py, _control_plane/{namespace_runtime,orphan_reaper,workspace_handle_lifecycle,types,pipeline_registry}.py, scripts/{ns_holder,setns_exec,setns_overlay_mount,configure_dns_in_ns}.py)'],
    git: true,
    rust: ['sandbox/crates/eos-isolated/src/ (lib.rs, session.rs, network.rs, caps.rs, audit.rs)', 'sandbox/crates/eos-ns-holder/src/lib.rs', 'agent-core/crates/eos-sandbox-host/src/isolated_workspace.rs', 'agent-core/crates/eos-tools/src/model_tools/isolated.rs'],
    invariants: [
      'enter_isolated_workspace / exit_isolated_workspace is an explicit lifecycle keyed on the active agent_id handle (NOT a separate public isolated_workspace_id routing param).',
      'The isolated session gets its OWN network namespace (isolated network).',
      'The upperdir storage is PERSISTENT throughout the isolated session lifecycle.',
      'Writes are captured + audited but NEVER OCC-published.',
      'Exit tears down the namespace, releases the snapshot lease, and removes scratch state; the changes are discarded.',
      'Enter REJECTS active sandbox-bound background work; exit CANCELS or drains it.',
      'plugin / LSP operations are BLOCKED while isolated mode is active for that agent.',
    ],
    owner_notes: 'Owns the isolated-never-OCC + enter/exit-bg-gating + plugin/LSP-blocked cross-cutting invariants.',
  },
  {
    key: 'sandbox_tools', domain: 'sandbox',
    title: 'Sandbox tools (command_exec, write_stdin, write, edit, multi-edit, grep, glob)',
    docs: ['docs/architecture/tools/sandbox.html', 'docs/architecture/tools/terminals.html'],
    python: ['backend/src/sandbox/api/tool/ (IN-TREE: all *.py)'],
    rust: ['agent-core/crates/eos-sandbox-api/src/tool_api/ (command.rs, control.rs, edit.rs, glob.rs, grep.rs, read.rs, write.rs, parse.rs, mod.rs)', 'sandbox/crates/eos-runner/src/tool_primitives.rs', 'sandbox/crates/eos-terminal-pair/src/lib.rs'],
    invariants: [
      'The tool set exists and is named: command_exec (exec), write_stdin, write, edit, multi-edit, grep, glob. Confirm EACH is present in Rust and enumerate any missing or renamed.',
      'write_stdin writes to a running command session stdin (terminal pair).',
      'edit performs a single find/replace with uniqueness/occurrence semantics; multi-edit applies multiple edits in order — semantics preserved.',
      'write (create/overwrite) and read semantics preserved.',
      'grep and glob semantics (patterns, output shape) preserved.',
      'command_exec foreground vs background routing + daemon response parsing preserved.',
    ],
  },
  {
    key: 'daemon_protocol', domain: 'sandbox',
    title: 'Daemon protocol & dispatch (wire protocol, envelopes, CAS, command session, in-flight, recovery)',
    docs: ['docs/architecture/sandbox/daemon.html', 'docs/architecture/agent_loops/provider-sandbox-bridge.html'],
    python: ['/tmp/oldpy/backend/src/sandbox/daemon/ (rpc/dispatcher.py, rpc/server.py, rpc/in_flight.py, builtin_operations.py, paths.py)', 'backend/src/sandbox/host/daemon_client.py (IN-TREE)', 'backend/src/sandbox/api/ (IN-TREE: daemon_invocations.py, daemon_audit.py, tool/_daemon_response_parsing.py)'],
    git: true,
    rust: ['sandbox/crates/eos-protocol/src/ (envelope.rs, canonical.rs, cas.rs, models.rs, version.rs, audit.rs)', 'sandbox/crates/eos-daemon/src/ (dispatcher.rs, server.rs, command.rs, invocation_registry.rs, isolated.rs)', 'agent-core/crates/eos-sandbox-host/src/daemon_client.rs', 'agent-core/crates/eos-sandbox-api/src/ (transport.rs, ops.rs)'],
    invariants: [
      'Wire protocol version is in lockstep host <-> daemon (build-time/compile-time assert).',
      'One JSON envelope per call; canonical serialization; CAS (content-addressed storage) for blobs.',
      'Dispatcher routes ops to handlers; invocation registry tracks in-flight calls (idempotency / dedupe of retries).',
      'Command session lifecycle (PTY command path) is preserved.',
      'Host-side recovery state machine (spawn / connect / empty-response retry with backoff) is preserved.',
      'Auth field + protocol field on the envelope handled on both sides.',
    ],
  },
  {
    key: 'plugins', domain: 'sandbox',
    title: 'Plugins (install, PPC, refresh, registry, OCC callbacks, projection)',
    docs: ['docs/architecture/sandbox/plugins.html', 'docs/architecture/sandbox/plugin-setup.html'],
    python: ['/tmp/oldpy/backend/src/sandbox/ephemeral_workspace/plugin/ (install.py, host_dispatch.py, op_registry.py, op_context.py, ppc_service.py, runtime_api.py, projection.py, overlay_child.py, overlay_dispatch.py)'],
    git: true,
    rust: ['sandbox/crates/eos-plugin/src/ (manifest.rs, ppc.rs, refresh.rs, registry.rs, service.rs, service_registry.rs)', 'sandbox/crates/eos-daemon/src/plugin/ (mod.rs, occ_callbacks.rs, ppc_router.rs, process.rs)', 'agent-core/crates/eos-plugin-catalog/src/'],
    invariants: [
      'Plugin install / setup flow preserved (manifest parsing, install).',
      'PPC (plugin persistent client / process channel) routing preserved.',
      'Plugin op registry + host dispatch preserved.',
      'OCC callbacks: plugin-produced changes publish through OCC-gated paths (not bypassing the gate).',
      'Projection of plugin overlay-child changes back to the parent overlay preserved.',
      'Refresh + registry lifecycle preserved.',
    ],
  },
  {
    key: 'provider_network', domain: 'sandbox',
    title: 'Provider / provisioning / network namespace (docker/daytona, ns-holder, setns)',
    docs: ['docs/architecture/sandbox/provider.html', 'docs/architecture/sandbox/space-model.html', 'docs/architecture/agent_loops/provider-sandbox-bridge.html'],
    python: ['backend/src/sandbox/provider/docker/ (IN-TREE)', 'backend/src/sandbox/provider/daytona/ (IN-TREE)', 'backend/src/config/sections/sandbox.py (IN-TREE)', 'backend/src/sandbox/provider/bootstrap.py if present (IN-TREE)'],
    rust: ['agent-core/crates/eos-sandbox-host/src/ (provider.rs, provisioning.rs, registry.rs, docker.rs, lifecycle.rs)', 'sandbox/crates/eos-ns-holder/src/lib.rs', 'sandbox/crates/eos-isolated/src/network.rs', 'sandbox/crates/eos-runner/src/ (fresh_ns.rs, setns.rs, mount.rs)'],
    invariants: [
      'Provider selection: Docker is default unless EOS_SANDBOX_PROVIDER or central config selects Daytona; provider bootstrap is process-global and first-call-wins.',
      'Provisioning / lifecycle (create, warmup, teardown) preserved.',
      'Network namespace holder keeps the netns alive across operations; setns / fresh-ns used by the runner.',
      'Daytona provider parity — confirm whether implemented or intentionally deferred in Rust, and report.',
    ],
  },
  {
    key: 'perf', domain: 'sandbox',
    title: 'Performance properties (O(1) lowerdir CoW, O(n*delta) upperdir, fast mount)',
    docs: ['docs/architecture/sandbox/overlay.html', 'docs/architecture/sandbox/layerstack.html', 'docs/architecture/sandbox/overview.html'],
    python: ['(design intent — read docs)', 'backend/scripts/bench_rust_daemon_*.py + bench_sandbox_e2e.py (IN-TREE benchmarks)'],
    rust: ['sandbox/crates/eos-overlay (kernel_mount.rs CoW)', 'sandbox/crates/eos-layerstack (manifest pointer-swap, no deep copy)'],
    invariants: [
      'Lower-dir storage is O(1) extra space: layers are shared read-only (CoW) across workspaces; NO full copy of the workspace per ephemeral overlay.',
      'Upper-dir cost is O(n*delta) for n parallel operations: each operation stores only its own delta in its own upperdir.',
      'Overlay + layerstack operations are fast: kernel overlayfs mount + manifest pointer-swap, not per-op deep file copies.',
      'Benchmarks exercise the Rust daemon path (confirm bench_rust_daemon_* scripts target eosd) and measure these properties.',
    ],
    owner_notes: 'ARCHITECTURAL-PROPERTY check. Judge from the mount/copy strategy in code + the bench scripts, NOT from guessing speed. Report whether the design preserves these complexity properties and whether a benchmark proves it.',
  },

  // ===================== AGENT-CORE =====================
  {
    key: 'query_engine', domain: 'agent-core',
    title: 'Query engine main loop (terminal-forced exit, not text-end)',
    docs: ['docs/architecture/agent_loops/main-loop.html', 'docs/architecture/agent_loops/index.html'],
    python: ['backend/src/engine/query/ (IN-TREE: loop.py + others)', 'backend/src/engine/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-engine/src/query/ (loop_.rs, mod.rs, context.rs, request.rs, provider_source.rs)', 'agent-core/crates/eos-engine/src/lib.rs'],
    invariants: [
      'The loop drives provider turns + tool execution and ONLY ends when a terminal tool ends it — NOT on text-only / non-tool output (unlike typical agent frameworks).',
      'Text-only output or a non-terminal tool call does NOT end the loop; the loop continues / re-prompts.',
      'Loop exit is conditioned on a successful terminal-tool stamp.',
      'Tool-call budget + max-iteration integration with the loop preserved.',
    ],
  },
  {
    key: 'budget_notifications', domain: 'agent-core',
    title: 'Budget notifications (75/100/125 notify, 150 fail) + premature non-terminal reminder retry',
    docs: ['docs/architecture/agent_loops/notifications-messages.html', 'docs/architecture/workflow/terminal-tools.html'],
    python: ['backend/src/notification/rules.py (make_tool_call_budget_tier_reminders) (IN-TREE)', 'backend/src/notification/runtime.py (IN-TREE)', 'backend/src/engine/ loop budget enforcement (IN-TREE)'],
    rust: ['agent-core/crates/eos-engine/src/notifications.rs', 'agent-core/crates/eos-engine/src/query/loop_.rs', 'agent-core/crates/eos-tools/src/registry.rs (budget/terminal wiring)'],
    invariants: [
      'Notifications fire at 75%, 100%, and 125% of the tool-call budget. Confirm the EXACT thresholds and that the percentage base is the configured tool_call_limit.',
      'Hard FAILURE at 150% (ceil(1.5 * limit)). Confirm the exact constant and the comparison operator (>= vs >), and that crossing it FAILS the agent rather than just warning.',
      'When an agent ends with a NON-terminal tool prematurely (no terminal submission), a system reminder fires telling it to end with its terminal tool.',
      'The system keeps RETRYING (re-prompting) until a valid terminal submission OR the 150% failure is hit.',
      'The default notification rule set + dedupe-by-name matches Python (make_tool_call_budget_tier_reminders + terminal-call reminder).',
    ],
    owner_notes: 'I (the orchestrator) edited notifications.rs + registry.rs in a prior session — verify the CURRENT state independently; do not assume prior edits were correct or complete.',
  },
  {
    key: 'terminal_tools', domain: 'agent-core',
    title: 'Terminal tools enforcement (called-alone, stamped terminating, dispatch/loop exit)',
    docs: ['docs/architecture/workflow/terminal-tools.html', 'docs/architecture/tools/submission.html', 'docs/architecture/tools/framework.html'],
    python: ['backend/src/tools/_framework/execution/tool_call.py (IN-TREE)', 'backend/src/engine/tool_call/dispatch.py (IN-TREE)', 'backend/src/engine/query/loop.py (IN-TREE)'],
    rust: ['agent-core/crates/eos-tools/src/terminal.rs', 'agent-core/crates/eos-tools/src/model_tools/submission.rs', 'agent-core/crates/eos-engine/src/tool_call/dispatch.rs', 'agent-core/crates/eos-engine/src/query/loop_.rs'],
    invariants: [
      'Terminal tools MUST be called alone: a terminal tool batched with other tool calls in the same turn is rejected / invalid.',
      'A successful terminal tool is STAMPED as terminating by the execution layer (tool_call).',
      'Dispatch + loop exit run off that terminating stamp.',
      'Terminal results are persisted task/workflow state inputs, not just user-facing messages.',
      'The set of terminal tools is enumerated (e.g. submit_root_outcome + workflow terminal submissions) and matches Python.',
    ],
  },
  {
    key: 'workflow_lifecycle', domain: 'agent-core',
    title: 'Workflow lifecycle (workflow->iteration->attempt creation rules, delegate_workflow)',
    docs: ['docs/architecture/workflow/lifecycle.html', 'docs/architecture/workflow/index.html'],
    python: ['backend/src/workflow/_core/state.py (IN-TREE)', 'backend/src/workflow/_core/outcomes.py (IN-TREE)', 'backend/src/workflow/ starter + iteration + attempt (IN-TREE)'],
    rust: ['agent-core/crates/eos-workflow/src/ (lifecycle.rs, starter.rs, iteration/mod.rs, ids.rs, ports.rs)', 'agent-core/crates/eos-tools/src/model_tools/workflow.rs'],
    invariants: [
      'delegate_workflow is a NON-terminal tool that launches a workflow from a running Task; the parent Task KEEPS running (no synthetic root workflow, no legacy waiting status).',
      'WorkflowStarter.start(prompt, parent_task_id) creates delegated workflow state and leaves the parent Task running.',
      'An iteration is created when the workflow is initialized OR when the previous iteration ends with a deferred goal handoff.',
      'An attempt is created when the iteration is initialized OR when the previous attempt ends with FAILURE.',
      'Agents inspect/cancel the background workflow via check_workflow_status / cancel_workflow, then submit their OWN terminal outcome; no close-time parent mutation, no legacy delegation-link column.',
    ],
  },
  {
    key: 'attempt_harness', domain: 'agent-core',
    title: 'Attempt harness (planner DAG, generator/reducer, PLAN->RUN->CLOSED, reducer exit gate)',
    docs: ['docs/architecture/workflow/attempt-harness.html', 'docs/architecture/workflow/agent-roles.html'],
    python: ['backend/src/workflow/attempt/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-workflow/src/attempt/ (orchestrator.rs, launch.rs, plan_dag.rs, run_stage.rs, orchestrator_registry.rs, mod.rs)'],
    invariants: [
      'Each Attempt owns ONE planner-authored DAG of generator + reducer Task rows whose edges are `needs`.',
      'Attempt stages are PLAN -> RUN -> CLOSED.',
      'The reducer is the EXIT GATE: the attempt closes through the reducer.',
      'Generators + reducers are launched based on the planned tasks (respecting `needs` dependency edges).',
      'AttemptOrchestrator is per-Attempt machinery, not a global orchestration layer.',
    ],
  },
  {
    key: 'deferred_goal_depth', domain: 'agent-core',
    title: 'Deferred goal handoff + nested depth 2 + planner@depth-2 cannot defer',
    docs: ['docs/architecture/workflow/lifecycle.html', 'docs/architecture/workflow/agent-roles.html'],
    python: ['backend/src/workflow/ (IN-TREE: iteration outcomes, deferred goal, depth guards)'],
    rust: ['agent-core/crates/eos-workflow/src/ (iteration/mod.rs, lifecycle.rs, starter.rs, attempt/plan_dag.rs, attempt/orchestrator.rs)'],
    invariants: [
      'An iteration can end with a DEFERRED GOAL that hands off to the NEXT iteration.',
      'Workflow nesting depth is capped at 2 (delegate_workflow at/beyond depth 2 is rejected).',
      'A planner at depth 2 CANNOT submit a deferred goal — this is explicitly enforced.',
      'Depth is tracked + propagated correctly through delegate_workflow nesting.',
    ],
    owner_notes: 'Owns the depth-2 + planner@depth-2-no-defer cross-cutting invariant. Quote the depth constant + the enforcement branch.',
  },
  {
    key: 'context_engine', domain: 'agent-core',
    title: 'Context engine (role packets from store state, workflow-only)',
    docs: ['docs/architecture/workflow/context-engine.html'],
    python: ['backend/src/workflow/context_engine/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-workflow/src/context/ (engine.rs, composer.rs, scope.rs, section.rs, xml.rs, mod.rs)'],
    invariants: [
      'ContextEngine builds role packets from STORE STATE for WORKFLOW agents only (not the root agent, not subagents).',
      'Lifecycle policy lives in workflow handlers/managers, NOT hidden inside context construction.',
      'Packet composition varies by role/scope (planner vs generator vs reducer see different sections).',
      'XML rendering of context sections is preserved.',
    ],
  },
  {
    key: 'advisor', domain: 'agent-core',
    title: 'Advisor (ask_advisor pass-verdict gate before terminal submission)',
    docs: ['docs/architecture/tools/ask-helper.html', 'docs/architecture/workflow/terminal-tools.html'],
    python: ['backend/src/tools/ (IN-TREE: advisor / ask_advisor helper)', 'backend/src/engine/ advisor gate integration (IN-TREE)'],
    rust: ['agent-core/crates/eos-tools/src/model_tools/advisor.rs', 'agent-core/crates/eos-engine/src/notifications.rs (AdvisorPort / AdvisorApproval)', 'agent-core/crates/eos-engine/src/tool_call/ or query/loop_.rs (the gate)'],
    invariants: [
      'The root agent AND workflow agents must call ask_advisor to choose the payload for their terminal tool.',
      'They must receive a verdict of PASS from the advisor BEFORE terminal tool submission is allowed.',
      'A non-pass advisor verdict BLOCKS terminal submission (the gate is enforced, not advisory).',
      'Which roles are subject to the advisor gate is correct (root + workflow agents; confirm subagents are excluded or not).',
    ],
    owner_notes: 'Owns the advisor-pass-before-terminal cross-cutting invariant (spans submission + loop). Find the exact gate that blocks submission without a PASS.',
  },
  {
    key: 'subagent', domain: 'agent-core',
    title: 'Subagent (launched as background task)',
    docs: ['docs/architecture/tools/subagent.html', 'docs/architecture/agent_loops/background-operations.html'],
    python: ['backend/src/tools/ (IN-TREE: subagent tool)', 'backend/src/engine/ background (IN-TREE)'],
    rust: ['agent-core/crates/eos-tools/src/model_tools/subagent.rs', 'agent-core/crates/eos-engine/src/background/ (supervisor.rs, dispatch.rs, mod.rs)'],
    invariants: [
      'Subagents are launched as BACKGROUND tasks (not inline blocking calls).',
      'The subagent result surfaces back to the launching agent.',
      'Subagent lifecycle is tracked by the background supervisor.',
    ],
  },
  {
    key: 'background_supervisor', domain: 'agent-core',
    title: 'Background supervisor (exec/subagent/workflow bg, exec status from daemon, terminal-block)',
    docs: ['docs/architecture/tools/background.html', 'docs/architecture/agent_loops/background-operations.html'],
    python: ['backend/src/engine/ background supervisor (IN-TREE)', 'backend/src/sandbox/api/ exec status (IN-TREE)'],
    rust: ['agent-core/crates/eos-engine/src/background/ (supervisor.rs, dispatch.rs, policy.rs, mod.rs)'],
    invariants: [
      'The background supervisor handles exec_command, subagent, AND workflow as background tasks.',
      'For exec_command status it PULLS from the sandbox daemon (not a provider-level persistent shell session).',
      'An agent CANNOT submit its terminal tool while any background task is still running (hard gate).',
      'Background completion surfaces back to the agent (notification / result injection).',
      'Background execution is an engine dispatch mode (policy decides what is backgroundable).',
    ],
    owner_notes: 'Owns the "no terminal submission while background task running" cross-cutting invariant. Find the exact gate + how exec status is polled from the daemon.',
  },
  {
    key: 'request_completion', domain: 'agent-core',
    title: 'User request -> completion (sandbox_id binding, root task, submit_root_outcome)',
    docs: ['docs/architecture/tools/submission.html', 'docs/architecture/workflow/index.html', 'docs/architecture/agent_loops/main-loop.html'],
    python: ['backend/src/runtime/entry.py (IN-TREE)', 'backend/src/task/ (IN-TREE)', 'submit_root_outcome path (IN-TREE)'],
    rust: ['agent-core/crates/eos-runtime/src/ (entry.rs, root_agent.rs, agent_loop.rs, agent_runner.rs, app_state.rs, tool_context.rs)', 'agent-core/crates/eos-tools/src/model_tools/submission.rs'],
    invariants: [
      'A user request is BOUND to a sandbox_id.',
      'The request mints a root Task(role=root, workflow_id=None) and runs the root agent directly through the entry path.',
      'The result the user receives comes from submit_root_outcome().',
      'The root agent MAY call delegate_workflow() for sophisticated execution, but the final user-facing result STILL comes from submit_root_outcome() (delegated workflow outcome does not directly become the user result).',
      'The request finishes through submit_root_outcome (a terminal submission).',
    ],
  },
  {
    key: 'model_provider_prompt', domain: 'agent-core',
    title: 'Model provider + SSE + prompt/context assembly',
    docs: ['docs/architecture/agent_loops/model-provider.html', 'docs/architecture/agent_loops/provider-sandbox-bridge.html', 'docs/architecture/agent_loops/prompt-context.html'],
    python: ['backend/src/providers/ (IN-TREE)', 'backend/src/prompt/ (IN-TREE)', 'backend/src/message/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-llm-client/src/', 'agent-core/crates/eos-engine/src/prompt/ (mod.rs, runtime_prompt.rs)', 'agent-core/crates/eos-engine/src/prompt_report.rs', 'agent-core/parity/ (sse fixtures, prompt_report golden, schemas)'],
    invariants: [
      'Provider abstraction covers Anthropic + OpenAI with SSE streaming parity (see agent-core/parity/sse fixtures).',
      'tool_use / thinking(Reasoning) / text block parsing parity (see parity/schemas + snapshots).',
      'Prompt assembly (system prompt + runtime prompt + context) parity.',
      'prompt_report parity (agent-core/parity/prompt_report golden jsonl).',
    ],
  },
  {
    key: 'tools_framework', domain: 'agent-core',
    title: 'Tools framework + hooks + skills + registry/spec',
    docs: ['docs/architecture/tools/framework.html', 'docs/architecture/tools/hooks.html', 'docs/architecture/tools/skills.html', 'docs/architecture/tools/index.html'],
    python: ['backend/src/tools/_framework/ (IN-TREE)', 'backend/src/skills/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-tools/src/ (dispatch.rs, execution.rs, executor.rs, hooks.rs, registry.rs, spec.rs, intent.rs, metadata.rs, meta.rs, name.rs, result.rs)', 'agent-core/crates/eos-skills/src/', 'agent-core/crates/eos-tools/src/model_tools/skills.rs'],
    invariants: [
      'Tool registry + spec generation parity (see default_tool_specs snapshot under eos-tools/src/model_tools/snapshots).',
      'Tool dispatch + execution pipeline (intent labeling, pre/post hooks) parity.',
      'Hooks framework (Pre/Post tool-use equivalents) parity.',
      'Skills loading / exposure parity.',
    ],
  },
  {
    key: 'persistence_state', domain: 'agent-core',
    title: 'Persistence / state / db / types parity',
    docs: ['docs/architecture/workflow/index.html', 'docs/architecture/sandbox/space-model.html'],
    python: ['backend/src/db/ (IN-TREE)', 'backend/src/message/ (IN-TREE)', 'backend/src/task/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-db/src/', 'agent-core/crates/eos-state/src/', 'agent-core/crates/eos-types/src/', 'agent-core/parity/sqlite/schema.sql + parity/tests/sqlite_schema.rs'],
    invariants: [
      'SQLite schema parity (agent-core/parity/sqlite/schema.sql + the sqlite_schema parity test).',
      'Task / Workflow / Iteration / Attempt row models + their state transitions are persisted (store is the coordination substrate).',
      'Message / content-block model parity (parity/schemas/message.schema.json + snapshots).',
      'No peer-to-peer agent communication path — coordination flows ONLY through persisted store state.',
    ],
  },
]

// ---------------------------------------------------------------------------
const INVESTIGATE_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['area_key', 'ground_truth_summary', 'report_path', 'invariant_rows', 'disparities'],
  properties: {
    area_key: { type: 'string' },
    ground_truth_summary: { type: 'string', description: 'What the Python/docs specify for this area, with key anchors' },
    report_path: { type: 'string' },
    invariant_rows: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['invariant', 'status', 'severity', 'python_evidence', 'rust_evidence', 'confidence'],
        properties: {
          invariant: { type: 'string' },
          status: { type: 'string', enum: ['match', 'partial', 'missing', 'bug', 'divergent', 'unverifiable'] },
          severity: { type: 'string', enum: ['high', 'medium', 'low', 'none'] },
          python_evidence: { type: 'string', description: 'file:line + short quote (ground truth)' },
          rust_evidence: { type: 'string', description: 'file:line + short quote, or "ABSENT — <where you looked>"' },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
          note: { type: 'string' },
        },
      },
    },
    disparities: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['id', 'title', 'severity', 'status', 'why_it_matters'],
        properties: {
          id: { type: 'string' },
          title: { type: 'string' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          status: { type: 'string', enum: ['missing', 'partial', 'bug', 'divergent'] },
          python_or_doc_evidence: { type: 'string' },
          rust_evidence: { type: 'string' },
          why_it_matters: { type: 'string' },
          suggested_fix: { type: 'string' },
        },
      },
    },
    extra_findings: { type: 'array', items: { type: 'string' } },
    open_questions: { type: 'array', items: { type: 'string' } },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['area_key', 'verify_path', 'invariant_verdicts', 'overall'],
  properties: {
    area_key: { type: 'string' },
    verify_path: { type: 'string' },
    invariant_verdicts: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['invariant', 'independent_status', 'evidence'],
        properties: {
          invariant: { type: 'string' },
          independent_status: { type: 'string', enum: ['confirmed_match', 'confirmed_disparity', 'investigator_overstated', 'investigator_missed', 'unproven'] },
          severity: { type: 'string', enum: ['high', 'medium', 'low', 'none'] },
          rust_evidence: { type: 'string' },
          python_evidence: { type: 'string' },
          evidence: { type: 'string', description: 'concise reasoning + the decisive anchor' },
        },
      },
    },
    disparity_verdicts: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['id', 'verdict', 'reasoning'],
        properties: {
          id: { type: 'string' },
          verdict: { type: 'string', enum: ['confirmed', 'refuted', 'adjusted'] },
          corrected_severity: { type: 'string', enum: ['high', 'medium', 'low', 'none'] },
          reasoning: { type: 'string' },
        },
      },
    },
    new_findings: { type: 'array', items: { type: 'string' } },
    overall: { type: 'string', description: 'one-paragraph verdict on Rust fidelity for this area' },
  },
}

const bullets = (arr) => arr.map((x) => `  - ${x}`).join('\n')
const numbered = (arr) => arr.map((x, i) => `  ${i + 1}. ${x}`).join('\n')

// Shared framing for the in-flight decoupling refactor. agent-core <-> sandbox
// now communicate ONLY through the eos-protocol contract crate; a Python in-
// process flow may now be split across that boundary. The // PORT comments are
// transient scaffolding being removed.
const BOUNDARY_NOTE = `## Architecture note (in-flight refactor — read before judging "missing")
The \`sandbox/\` workspace is being made an INDEPENDENT module: agent-core talks to sandbox ONLY through the \`eos-protocol\` contract crate. A dynamic that was ONE in-process flow in Python may now be SPLIT across the protocol boundary — the client/host side in agent-core (eos-sandbox-host, eos-sandbox-api) builds an eos-protocol request, and the execution/runtime side in the sandbox workspace (eos-daemon, eos-runner, eos-overlay, eos-occ, eos-layerstack, eos-isolated, eos-plugin) carries it out. When tracing an invariant, FOLLOW IT ACROSS THE BOUNDARY and name which side owns each piece; do NOT conclude a dynamic is "missing" merely because it lives on the other side of eos-protocol (but DO flag if it is missing on BOTH sides, or if the protocol drops information the dynamic needs).
The \`// PORT backend/src/...:LINE\` comments are TRANSIENT scaffolding being removed — use them only as a navigation HINT to find the Python origin, and VERIFY the actual behavior; never treat a PORT comment as proof of correctness.`

function investigatePrompt(area) {
  const reportPath = `docs/reviews/rust_parity/areas/${area.key}.md`
  const gitNote = area.git
    ? '\n  NOTE: the pre-cutover Python sandbox internals were DELETED from the working tree and MATERIALIZED for you under /tmp/oldpy/backend/src/sandbox/... — read them THERE (they are the real ground truth).'
    : ''
  return `You are auditing the Rust re-implementation of the EphemeralOS agent framework against its behavioral ground truth, for ONE area:

# AREA: ${area.title}  (domain: ${area.domain})

THE WORRY: the Rust port may SILENTLY MISS key dynamics, drop implementation details, or introduce bugs. A shallow "looks fine" verdict is worse than useless. Every claim needs bilateral file:line evidence — quoting the REAL code/constant, not paraphrase.

## Source precedence (when sources disagree, this is the order of truth)
1. Python source = behavioral GROUND TRUTH (authoritative for dynamics, constants, edge cases, ordering).
2. docs/architecture/*.html = curated corroboration of intended behavior.
3. The invariant checklist below = WHAT to confirm — but treat its specifics as possibly fuzzy (e.g. a "~100" constant may actually be a configurable value; FIND the real one). A three-way disagreement is itself a FINDING to report.

## Ground-truth sources (read these first)
Architecture docs (HTML — read as text):
${bullets(area.docs)}
Python ground truth:
${bullets(area.python)}${gitNote}

${BOUNDARY_NOTE}

## Rust under audit (the thing you are checking)
${bullets(area.rust)}
The migration plan docs (docs/plans/backend_agent_core_rust_migration/impl-*.md) and agent-core/docs/class-inventory/*.md are the Rust team's OWN claims — you may consult them, but they are NOT ground truth.

## Invariant checklist (confirm EACH — one row each, bilateral evidence mandatory)
${numbered(area.invariants)}
Also EXTRACT EXACT CONSTANTS where relevant (thresholds, limits, depths, comparison operators >= vs >) and compare literal values on both sides.
${area.owner_notes ? `\nOWNER NOTE: ${area.owner_notes}` : ''}

## Method
1. Read the ground truth (docs + Python). Understand the intended dynamic precisely — including edge cases, ordering, error paths.
2. Read the Rust. Map each invariant to its Rust anchor (use // PORT comments + grep + read).
3. For each invariant choose status: match / partial / missing / bug / divergent / unverifiable. A "match" REQUIRES quoting the Rust file:line that implements it — NEVER assert a match from mere absence of contrary evidence.
4. Hunt for bugs + dropped details BEYOND the checklist (off-by-one, missing branch, wrong operator, lost ordering, dropped error handling, race). Distinguish an INTENTIONAL migration change (e.g. Python in-process daemon replaced by the eosd binary, or Daytona deferred) from a BUG / MISSING dynamic — label which.

## Output
1. FIRST: Write a thorough markdown report to: ${reportPath}
   Sections: "## Ground truth" (with anchors) · "## Rust mapping" · "## Invariant table" (invariant | status | severity | python file:line | rust file:line | note) · "## Disparities" (detailed: evidence + why it matters + suggested fix) · "## Extra findings" · "## Open questions".
2. THEN: return the structured summary. report_path MUST equal "${reportPath}". Every invariant_row and disparity needs concrete file:line in BOTH python_evidence and rust_evidence (use "ABSENT — <where you looked>" when truly missing in Rust).

Be exhaustive, concrete, skeptical. Cite file:line everywhere. Quote actual code + constants.`
}

function verifyPrompt(area, inv) {
  const verifyPath = `docs/reviews/rust_parity/areas/${area.key}.verify.md`
  const gitNote = area.git ? ' (deleted Python materialized at /tmp/oldpy/backend/src/sandbox/...)' : ''
  const invDigest = (inv.invariant_rows || [])
    .map((r) => `  - [${r.status}/${r.severity}] ${r.invariant}\n      py: ${r.python_evidence}\n      rs: ${r.rust_evidence}`)
    .join('\n')
  const dispDigest = (inv.disparities || [])
    .map((d) => `  - (${d.id}) [${d.severity}/${d.status}] ${d.title} :: rust=${d.rust_evidence || 'n/a'}`)
    .join('\n')
  return `You are the INDEPENDENT VERIFIER for area "${area.title}" (${area.domain}). A first investigator produced a report; your job is to TRUST NOTHING and re-derive the truth yourself by opening the files.

You catch TWO failure modes, and the FIRST is the primary worry:
(a) FALSE MATCH — investigator said "match" but the Rust actually misses/breaks the dynamic. Hunt these aggressively.
(b) FALSE ALARM — investigator flagged a disparity that is actually implemented (perhaps elsewhere).

## Source precedence
Python source = ground truth; docs/architecture = corroboration; the checklist = what to confirm (specifics may be fuzzy — a constant the user "thinks is 100" may differ; find the real one).

## Ground truth
Docs:
${bullets(area.docs)}
Python:
${bullets(area.python)}${gitNote}
## Rust (the // PORT comments are transient navigation hints, not proof)
${bullets(area.rust)}

${BOUNDARY_NOTE}

## Invariant checklist — INDEPENDENTLY confirm EACH (mandatory, regardless of what the investigator said)
${numbered(area.invariants)}
Extract + compare EXACT CONSTANTS / operators on both sides.

## Investigator's findings (scrutinize, do NOT trust)
Invariant rows:
${invDigest || '  (none returned)'}
Disparities:
${dispDigest || '  (none returned)'}

## Your task
For EACH invariant, open the Rust (and Python) yourself and assign independent_status:
- confirmed_match — you found the Rust anchor that correctly implements it (quote file:line).
- confirmed_disparity — genuinely missing/wrong in Rust (quote where you looked + the Python it should match).
- investigator_overstated — investigator claimed a disparity that is actually fine.
- investigator_missed — investigator claimed match but it is actually broken/missing → FLAG LOUDLY (this is the primary worry).
- unproven — could not determine; say exactly what blocked you.
Give bilateral evidence (rust + python file:line) for every verdict. Then adjudicate each investigator disparity (confirmed / refuted / adjusted) and add any NEW findings you discover.

## Output
1. Write your verification to: ${verifyPath} (md: invariant verdict table with independent evidence; disparity adjudication; new findings; overall verdict).
2. Return the structured verdicts. verify_path MUST equal "${verifyPath}".

Be a skeptic. Open the files. Quote real file:line + real constants.`
}

// ---------------------------------------------------------------------------
const only = (args && args.only) ? new Set(args.only) : null
const doSynth = !(args && args.synthesize === false)
const areas = only ? AREAS.filter((a) => only.has(a.key)) : AREAS
log(`rust-parity-audit: ${areas.length} area(s) -> ${areas.map((a) => a.key).join(', ')}`)

phase('Investigate')
const results = await pipeline(
  areas,
  (area) => agent(investigatePrompt(area), { label: area.key, phase: 'Investigate', schema: INVESTIGATE_SCHEMA }),
  (inv, area) =>
    agent(verifyPrompt(area, inv || {}), { label: `verify:${area.key}`, phase: 'Verify', schema: VERIFY_SCHEMA })
      .then((ver) => ({ area: area.key, domain: area.domain, title: area.title, investigate: inv, verify: ver }))
      .catch((e) => ({ area: area.key, domain: area.domain, title: area.title, investigate: inv, verify: null, error: String(e) })),
)

const merged = results.filter(Boolean)

// Compact digest for synthesis (full detail is in the per-area .md files).
const digest = merged.map((m) => ({
  area: m.area,
  domain: m.domain,
  title: m.title,
  invariant_rows: (m.investigate?.invariant_rows || []).map((r) => ({ invariant: r.invariant, status: r.status, severity: r.severity, rust: r.rust_evidence })),
  disparities: (m.investigate?.disparities || []).map((d) => ({ id: d.id, title: d.title, severity: d.severity, status: d.status })),
  verdicts: (m.verify?.invariant_verdicts || []).map((v) => ({ invariant: v.invariant, status: v.independent_status, severity: v.severity })),
  disparity_verdicts: m.verify?.disparity_verdicts || [],
  new_findings: m.verify?.new_findings || [],
}))

if (!doSynth) {
  log('pilot mode: skipping synthesis')
  return { mode: 'pilot', areas: merged.map((m) => m.area), digest }
}

phase('Synthesize')
const reportPath = 'docs/reviews/rust_parity/REPORT.md'
const synthSummary = await agent(
  `You are the SYNTHESIS author for a Rust-vs-Python/docs parity audit of the EphemeralOS framework (two Rust workspaces: agent-core/ and sandbox/; ground truth = docs/architecture + Python in backend/src + materialized deleted Python at /tmp/oldpy).

${merged.length} area audits + independent verifications were written as markdown under docs/reviews/rust_parity/areas/ (<key>.md = investigation, <key>.verify.md = independent verification). READ THEM ALL (both files per area).

Machine digest of all results (statuses + independent verifier verdicts):
\`\`\`json
${JSON.stringify(digest, null, 2)}
\`\`\`

Produce ONE authoritative report at ${reportPath}:
1. Executive summary — how faithfully does the Rust port reproduce the framework's KEY DYNAMICS? Headline risks (the things most likely to be real bugs / missed dynamics).
2. CROSS-DOMAIN DISPARITY TABLE ranked by severity: | severity | domain | invariant/dynamic | python anchor | rust status + anchor | verifier verdict | suggested fix |. When the verifier's independent_status disagrees with the investigator, PREFER the verifier and mark the disagreement.
3. Per-domain detail (## Sandbox, ## Agent-core): confirmed disparities, bugs, missing dynamics — each with file:line on both sides.
4. Cross-cutting invariants — reconcile across the areas that touch them: advisor-pass-before-terminal, no-terminal-while-background-running, workflow depth<=2 + planner@depth-2-cannot-defer, isolated-workspace-never-OCC-merged, terminal-tool-called-alone. State the single source of truth + final status for each.
5. Coverage matrix — every area x every named invariant -> final status (match/partial/missing/bug/divergent/unproven). Flag every 'unproven' as a manual-follow-up gap.
6. Prioritized recommended fixes + suggested tests.

Anchor every claim in file:line. This report is the deliverable. After writing it, return a <=12-line executive summary as plain text.`,
  { label: 'synthesize', phase: 'Synthesize' },
)

return { mode: 'full', reportPath, areas: merged.map((m) => m.area), summary: synthSummary, digest }
