/goal Implement **Phase 2, Stage 1 (manager surface)** of the `sandbox-e2e-live-test` crate — build-to-green against the spec. Implement code; do not redesign.

## Source of truth
`docs/e2e/sandbox-e2e-live-test-phase-2-spec.md` is the fixed spec — read it in full first; follow its §2 tree and §3 per-leaf table verbatim. **Live code wins** on any conflict; verify each cited `file:line` before relying on it. Build on the live Phase 1 skeleton (`src/{assertion,cli_client,config,fixtures,gateway,lib}.rs`, `tests/support/mod.rs`, `build.rs`, `tests/{manager,runtime}.rs`, the M1 leaf). `assertion.rs` has only `ok`/`field` today.

## Deliverables (Stage 1 only)
1. **`src/assertion.rs`** — add `err_kind_at(rec, kind, exit)`: read `rec.response()` for the error object, assert `error.kind == kind` AND `rec.exit_code == exit` (live routing: `cli_client.rs:72-77`, `output.rs:266-272`). Keep `ok`/`field`.
2. **`src/report.rs`** (NEW, SRP) — write `reports/{id}/exchange.jsonl`: a `{"schema_version":1}` header line, then one JSON line per `CallRecord` `{argv, request, response, exit_code, stdout, stderr, latency_ms}`. `request` is always `null` (black-box; `cli_client.rs:13,81`).
3. **`src/fixtures.rs`** — add `Harness::run_root() -> &Path` over the existing field (`fixtures.rs:15`); give `Sandbox` a `RefCell<Vec<CallRecord>>` exchange buffer seeded with the create record; flush via `report::` on `Drop` (drop already holds a `Harness` handle, `fixtures.rs:101`). `provision_sandbox`/`Sandbox` otherwise unchanged.
4. **Leaf tests** — one file per case under `tests/manager/<family>/<op>/<case>.rs`: M2 `lifecycle/list_sandboxes`, M3 `lifecycle/inspect_sandbox`, M5 `lifecycle/destroy_sandbox`, M4 `observability/get_observability_tree`, N1 `routing/scope_and_dispatch`. Use the exact paths, `#[test]` fn names, fixtures, invocations, and asserted fields from §3. Mirror the M1 leaf shape. `build.rs` is **unchanged** — a new case = a new file the walker discovers.

## Fences (do not cross)
- **Black-box only**: every op via `CliClient::manager` over the gateway socket. No internal-crate deps, no test-injected runtime. Capture `/id` from the create response; never predict an id or its format.
- **Stage fence**: **drive zero runtime ops.** Do NOT author `tests/runtime/**` (R2–R8, N2 are Stage 2, deferred). Leave the dormant R1 file untouched.
- **Scope**: introduce only `exchange.jsonl`. No `result.json`/`summary.json`/observability/orchestrator work (Phases 3–4). Keep the skip path a bare `return` that writes nothing.
- Discriminate success by **absence of the top-level `error` key**; assert field presence + type + invariants and CLI exit codes (0/1/2). Manager error kinds: `unknown_op`/`invalid_request`/`internal_error`.

## Conventions
SRP, one job per unit. **No inline comments in production code** (doc comments OK; test-intent comments OK). External deps via `dep.workspace = true` (reuse `anyhow`/`serde`/`serde_json` — add none). Clippy must pass: no new `unwrap_used`/`dbg_macro`/`undocumented_unsafe_blocks`.

## Acceptance (in order)
1. `cargo build -p sandbox-e2e-live-test` and `cargo clippy -p sandbox-e2e-live-test --all-targets` exit 0.
2. `cargo test -p sandbox-e2e-live-test` with `EOS_E2E_RUN_ROOT` unset: every leaf skips, nothing written.
3. Green: with an externally started real-runtime gateway, hand-write `run-manifest.json` (`{schema_version, gateway_socket, run_id, image}`), then `EOS_E2E_RUN_ROOT=$dir cargo test -p sandbox-e2e-live-test --test manager -- --test-threads=4` → M1–M5 + N1 pass and `reports/{id}/exchange.jsonl` is written per sandbox.

Report what you changed, the test output, and any spec-vs-live-code conflict you hit.
