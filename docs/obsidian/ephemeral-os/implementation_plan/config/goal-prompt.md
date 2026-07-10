> **Historical execution prompt (operation-layout exempt, 2026-07-11):** The
> completed goal and its commands retain the paths used when the work landed;
> use the maintained specs and current `e2e/` tree for new work.

/goal Execute the config consolidation implementation plan in ephemeral-os, phase by phase, gated on acceptance criteria.

Design truth — read all three, in order of authority; all live in docs/obsidian/ephemeral-os/implementation_plan/config/:
- implementation-plan.md (phases, work items, acceptance boxes)
- spec.md (schema shape, wiring, validation, decisions)
- cli-e2e-test-spec.md (e2e family design)

Mission: move hardcoded policy values (timeouts, caps, widths, limits) into the typed YAML config (crates/sandbox-config/src/configs/), retire the EOS_* env side channels, and build the cli-operation-e2e-live-test/config/ pytest family proving every knob end to end. Contracts stay in Rust per config/README.md.

Iron rules:
- GATE: a phase is done only when EVERY acceptance box in implementation-plan.md is checked with evidence (passing command, empty grep, green pytest id). Check boxes in the plan file as you go — it is the tracker. Never start phase N+1 early.
- Each phase commits to main. config/prd.yml never changes; bench.yml only in phase 1.
- Every new field: #[serde(default=...)] preserving today's exact constant, deny_unknown_fields, validation in configs/validate.rs. prd.yml stays minimal.
- Leaf crates (protocol, observability, layerstack, namespace-execution) never gain a sandbox-config edge: inject at construction (ObserverConfig precedent).
- One tuning path: a YAML field's env var dies in the same change that lands it.
- SOLID/SRP, prefer less, no inline comments in src/, tests only under tests/.
- token_ttl_s lands in phase 2 with ProtocolLimits (plan resolution note).

Phase 0 — e2e harness first, no Rust changes. Lane A: daemon/runtime/runner/observability sections re-read from daemon_config_yaml_path on EVERY create_sandbox (installer.rs:49) — rewrite generated file + create new sandbox. Lane B: manager section loads at gateway start — restart per arm, slow-marked. Build the config/ family: helpers.make_config (pyyaml deep-merge, pytest tmp only), family gateway fixture + baseline-restore finalizer, `config` marker. Land test_daemon_reload.py (rewrite-applies-to-next-sandbox, mount mask, setup_timeout_s 0.001 fails session, observability toggle), test_validation.py (bad daemon YAML → structured create error + rollback; bad manager YAML → gateway fails to start), test_manager_section.py (container_env nonce, memory_bytes vs cgroup memory.max), skip-marked TestPhase1/2/3. Prove baseline restore: manager/ passes after config/.

Phase 1 — bench-path knobs: runtime.layerstack {remount_sweep_width, export_chunk_bytes, spool_zstd_level}, manager.export {3 caps}, daemon.http.export {frame_bytes, channel_frames}. Delete sweep_width() env fn + export_apply env_cap fns; bench.yml + ab_driver.py substitute the YAML key directly. Unskip TestPhase1. Grep gate: zero EOS_ strings.

Phase 2 — daemon limits: daemon.server extensions via a ProtocolLimits value type (Default = today's constants), token_ttl_s, daemon.http.forward timeouts, observability {max_line_bytes, shared sampling budget, views limits}. Unskip TestPhase2.

Phase 3 — runtime op caps: runtime.{command,file,namespace_execution} fields per spec tier 3. COMMAND_ENGINE_SETUP_TIMEOUT_S collapses into workspace.setup_timeout_s (grep-empty gate). Unskip TestPhase3.

Phase 4 — host surfaces: gateway section (flag > YAML > default, unit-tested), console section + --config-yaml, manager.docker timing, observability_snapshot, local_daemon. Update config/README.md.

Every phase: cargo build && cargo test && cargo clippy --all-targets && cargo fmt green; schema tests pin defaults + edge rejections; pytest -m config green; squash/export/file regressions green.

Done when: all plan boxes checked incl. cross-phase list; test_phase_knobs.py has zero skips; spec.md's maximal YAML loads in one schema test; specs' status flipped; all on main citing the plan.
