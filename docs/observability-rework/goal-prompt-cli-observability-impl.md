> **Frozen historical prompt (operation-layout exempt, 2026-07-11):** Do not
> execute this completed goal verbatim. Its five-view multiplexer, CLI paths,
> and ownership statements predate the concrete-operation migration.

/goal Implement the observability CLI to match docs/observability-rework/cli-observability.md — the source of truth for the operation family (§2), the rendered help (§3), and the command matrix (§4). Target the post-review surface: FIVE views (snapshot, trace, events, cgroup, layerstack), --sandbox-id required on every view, id-valued flags carry -id, and NO raw view.

Tasks, smallest first:

1) CLI specs — crates/sandbox-gateway/src/cli/observability_specs.rs
- SPECS today holds only LAYERSTACK_SPEC, SNAPSHOT_SPEC, CGROUP_SPEC. Add TRACE_SPEC and EVENTS_SPEC verbatim from §2 (args, usage, examples, related). Do NOT add a raw spec.
- trace: arg name "trace_id", flag --trace-id, optional, default "last".
- events: args name (--name), since_ms (--since-ms), last_n (--last-n, Integer, optional, no default).
- layerstack: rename arg workspace→workspace_id, flag --workspace→--workspace-id.
- Keep --sandbox-id required via the shared SANDBOX_ID_ARG.

2) Daemon router — crates/sandbox-daemon/src/observability/view.rs
- Remove the raw arm (raw_view_response + raw_filter); raw is no longer a view. Drop Reader::raw/raw_lines if unused afterward.
- trace_view_response: read param "trace_id" (was "trace"); fix the fault text to "--trace-id"; RESOLVE the "last" sentinel to the most recent root trace (today it looks up a trace literally named "last").
- layerstack_view_response: read "workspace_id" (was "workspace").
- events_view_response: after the events fold, read "last_n" and truncate to the newest N. last_n is NOT a RawFilter field — apply it post-fold.

3) Request mapping — request_builder.rs is already view-generic (build_observability_request forwards each arg as a param + injects view); no per-view code. Confirm the new flags map to params trace_id / last_n / workspace_id.

4) Output — the daemon returns lightly-structured JSON per view (existing { "view": ... } shape) and output.rs JSON-dumps it. The §4 rendered human shapes are the eventual target, but ship JSON first; do NOT add per-view renderers in this pass. If/when a renderer lands, build the trace waterfall first (the one view unreadable as JSON).

5) Tests — crates/sandbox-gateway/tests/gateway_cli.rs
- Catalog + per-op help golden tests for all five views (substring style per §3: each operation page, --trace-id, --last-n, --workspace-id).
- Request-builder tests: flags → correct params; events --last-n truncates newest-N; trace --trace-id last resolves to the newest root; missing --sandbox-id errors; an unknown flag for a view is rejected.

Constraints: respect crate boundaries (README.md); SOLID/SRP, one job per unit; no inline comments in src/; tests live in tests/, never src/. cargo build, cargo test, cargo clippy --all-targets, cargo fmt must all pass. Additive, localized edits — touch only what the task needs. Do NOT reintroduce raw or the --id flag.
