# eos-coding-agent (parked)

Host material extracted from the SDK during the `eos-agent-sdk` re-shape
(`docs/plans/agent-core-to-sdk-and-coding-agent-split/eos-agent-sdk_SPEC.md`,
internal-architecture table §5). This tree is **not wired**: no workspace,
no build, imports still reference the old `@eos/*` internal packages.
Implementing `eos-coding-agent_SPEC.md` (same plan directory) turns it into
the real host project; until then it is source material parked at the
destinations that spec's §2 layout names.

| Parked location | Origin (in `eos-agent-core`, now `eos-agent-sdk`) | Coding-agent spec disposition |
|---|---|---|
| `.eos-agents/` | repo root `.eos-agents/` | profiles + policy scripts + operator config (§2) |
| `packages/workflows/pursuit/{src,tests,db,contracts}` | `packages/pursuit`, `packages/db`, `contracts/src/pursuit.ts` | pursuit provider (§8); `agent-launcher.ts` port gets deleted, `launcher.ts`/`outcome-fns.ts`/`provider.ts` written fresh |
| `packages/scripts/` | `packages/scripts` | `executeJsonCommand` powers subprocess→callback hook wrapping (§5 SDK spec) |
| `packages/tools/legacy/` | `packages/tool/src/tools/*` (agent, background, pursuit, submission families + advisory/description prompts) | rewritten as host tools over the SDK surface (§5) |
| `packages/app/config/` | agent-runtime config/profile loaders (`config-root`, `config-file`, `hook-config`, `notification-rules-config`, profile loader/registry, file-based `llm-client-registry`) | moved verbatim (§9) |
| `packages/app/config/notification-triggers/` | `@eos/notification` trigger engine + tests | reference for `compileNotificationRules` → `turnBoundary` hook entries (§3) |
| `packages/app/hooks/` | `@eos/tool` subprocess hook protocol + runner | host-side hook command wrapping (§7) |
| `packages/app/pursuit-context-scripts.ts` | agent-runtime | moved verbatim (§2) |
| `packages/app/legacy/` | old `runtime.ts` (composition root + pursuit wiring + launch port), `run-registry.ts`, `transcript.ts` byte reader | reference for `app/main.ts`, the host run map, and `read_agent_run_transcript` |
| `packages/app/legacy-tests/`, `e2e/` | host-flavored unit tests + the whole e2e suite (+ cache vitest config) | the e2e suite moves with the host (§10 step 3) |
| `packages/testkit/eos-agents.ts` | `@eos/testkit` fixture-path helper | `.eos-agents` fixture building moves to the coding agent (SDK spec §5) |
