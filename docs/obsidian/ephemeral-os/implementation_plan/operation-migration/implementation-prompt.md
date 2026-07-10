/goal Implement the sandbox operation ownership migration exactly as specified, phase by phase, until every phase gate in the phase plan is approved.

Authoritative documents (read both fully before any change):
- Specification: docs/obsidian/ephemeral-os/implementation_plan/operation-migration/spec.md
- Execution tracker: docs/obsidian/ephemeral-os/implementation_plan/operation-migration/phase-plan.md

The specification owns the design; the phase plan owns execution state. Also read README.md and CLAUDE.md and obey repository rules: work directly on main (no branches or worktrees), no inline comments in production code, no test code under src/, SOLID/SRP, prefer less.

Sequencing (hard rule)
- Execute phases strictly in order: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8.
- A phase may not start until the previous phase's acceptance checklist is fully checked and its dashboard row is marked approved.
- Within Phase 0, capture all baselines BEFORE the destructive purge/move step.

Per-phase loop (repeat for every phase)
1. Set the phase's dashboard status to "in progress" with the date.
2. Work the phase's Change list top to bottom, checking items off as they land. Respect the atomic steps: the Phase 2 catalog merge, the Phase 6 namespace conversion, and the Phase 6 multiplexer cutover are each one commit.
3. Keep the workspace green after every commit: cargo check --workspace --all-targets --all-features plus the phase's focused tests.
4. When the change list is done, run the standing gate: cargo test --workspace --all-features; cargo clippy --workspace --all-targets --all-features -- -D warnings; cargo fmt --all -- --check.
5. Verify every acceptance criterion with its named command. Paste each command and a result excerpt into the phase's Progress log (Date | Item | Command/evidence | Result | Deviations) before checking its box. Evidence is a command, not a claim.
6. Set status "gate review", then "approved" with the date; only then is the next phase unblocked.

Fidelity rules
- No compatibility shims, aliases, re-exports, or duplicate trees; the migration is intentionally source-breaking. Preserve binary names, CLI syntax, help, exit codes, MCP schemas, and console APIs except the four approved behavior changes listed in the spec.
- Enforce the spec's normative dependency-law table at all times; applications never depend on sandbox-protocol, the client, adapters, or composition roots. Namespace directories (sandbox-operations/, sandbox-observability/, sandbox-runtime/) never gain a Cargo.toml.
- The merged catalog has per-domain features (manager/runtime/observability); CLI features forward to them; run catalog integrity tests with --all-features and verify per-binary feature closure with cargo tree.
- CLI projection integrity is bidirectional. The visibility chokepoints (console public-only validation, manager-router internal rejection) must have tests.
- If implementation must deviate from the spec, stop, amend the spec section in the same commit, and record it in the phase Progress log and the Deviation register. Silent divergence fails the gate.
- Commit at natural checkpoints with clear messages. Other agents may edit this repo concurrently: touch only what the current phase requires and never revert changes you did not make.

Completion
Done means: all nine dashboard rows approved; cargo run -p xtask -- operation-architecture-check passes along with its self-tests; the full verification matrix and the live E2E proof (full e2e/ suite per RUNNING.md plus bin/start-sandbox-docker-gateway --rebuild-binary and the six-operation smoke) are recorded in Phase 8's progress log; the spec's acceptance checkboxes are checked from that evidence; the spec status is flipped to adopted and the legacy CLI migration plan is marked superseded. If blocked on a decision only a human can make, record the blocker in the phase's Progress log and stop rather than guessing.
