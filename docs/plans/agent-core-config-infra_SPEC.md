# SPEC: agent-core Config Infrastructure

Status: DRAFT
Date: 2026-06-05
Owner workspace: `agent-core/`
Scope: `agent-core/config`, `agent-core/crates/eos-config`, and a crate-root
`src/config.rs` in every non-test agent-core crate that owns tunable constants.

This spec redesigns `agent-core` configuration to mirror the **completed
`sandbox/` config system** (`docs/plans/sandbox-config-infra_SPEC.md`), adapted
to three explicit owner decisions:

1. **No central config.** The `eos-config::CentralConfig` composition root is
   removed. `eos-config` becomes a *generic loader only* — file read, path
   policy, YAML parse, merge — exactly like `sandbox/crates/eos-config`.
2. **Per-module schema.** Every non-test crate that owns configurable constants
   defines a crate-root `src/config.rs` typed schema and consumes it via
   `doc.section::<crate::config::FooConfig>("foo")`. Child modules consume their
   parent crate's sub-config; they do not define their own `config.rs`.
3. **No environment variables.** Config comes from files only. The `EOS__*`
   nested overlay, the `_LEGACY_ENV_MAP` adapters, and the `EPHEMERALOS_*` path
   discovery are all removed. Config selection is by *which file*, never by env
   or CLI flag — **zero env reads anywhere, including secrets.**

**Two-layer file model (resolved — see §5.0):**
- **`agent-core/config/config.yml`** — committed baseline (defaults, *no
  secrets*), loaded **first**. Every value mirrors the Rust `Default`.
- **`agent-core/config/prd.yml`** — **gitignored** production override holding
  secrets (API keys) + deployment values (db path, provider); merged **over** the
  baseline when present, override wins.
- **per-submodule / per-test `local.yml`** (any custom name) — an override loaded
  **explicitly by a test loader**, merged over the baseline. Mirrors the sandbox
  flow (`config.yml` first, `local.yml` overwrites).

Per-crate typed sections; static contracts left in Rust.

---

## 1. Architecture: generic loader + parent-owned section schemas

```
        agent-core/config/config.yml          (committed baseline — defaults, no secrets)
                 │   merge (override wins, recurse/replace)
        ┌────────┴────────┬──────────────────────────────┐
        │                 │                              │
   prd.yml (gitignored)   <crate>/tests/**/<name>.yml    (any custom-named override)
   secrets + deploy        loaded explicitly by tests
        ▼
  eos-config (GENERIC LOADER — owns NO schema)
    ConfigDocument(serde_yaml::Value)   load()  /  load_with_override(path)
    ConfigPath (baseline + override policy)   merge (recurse/replace)
        │  doc.section::<T>("name")  →  typed, deny_unknown_fields, validated
        ▼
  each crate's  src/config.rs  (OWNS its typed schema + Default + validate)
    eos-db::config::DatabaseConfig
    eos-sandbox-host::config::SandboxConfig  (embeds eos-sandbox-api::config::SandboxTimeouts)
    eos-llm-client::config::{ProvidersConfig, LlmClientConfig}   eos-engine::config::EngineConfig
    eos-tools::config::ToolsConfig        eos-workflow::config::WorkflowConfig
    eos-audit::config::AuditConfig        eos-obs-collector::config::ObsConfig
    eos-plugin-catalog::config::PluginCatalogConfig   eos-skills::config::SkillsConfig
    eos-runtime::config::RuntimeConfig
        │
        ▼
  composition root (eos-runtime entry) loads prd.yml ONCE, deserializes the
  sections it needs, and injects each typed sub-config into the owning crate.
```

This is the same split the sandbox workspace shipped: `eos-config` owns
*loading*; runtime crates own *schema + validation*. The differences from
sandbox are deliberate and limited to: a different baseline path
(`agent-core/config/prd.yml`) and **no env layer** (sandbox already has none, so
this is parity, not a divergence).

### Ownership rules (carried verbatim from the sandbox model)

| Rule | Consequence for agent-core |
|---|---|
| Schema is parent-owned | A crate may define `src/config.rs`; child modules consume `crate::config::SubConfig`. No nested `config.rs`. |
| Merge: object recurse, scalar replace, **array replace**, missing inherits | One `merge.rs` in `eos-config` (already exists in sandbox form). |
| Unknown key / wrong type = error | Every section derives `#[serde(deny_unknown_fields)]`. |
| Static contracts stay in Rust | Protocol versions, wire sentinels, schema versions, `/eos` mount layout, kernel/procfs paths, package/skill layout names — **never** YAML. |
| Validation lives with the owner | Range/contradiction checks move out of `eos-config::validation` into each section type's own `validate()`. |
| **One top-level section per crate** | Each owning crate maps to exactly one `prd.yml` key (as in sandbox: `eos-daemon`→`daemon`, `eos-runner`→`runner`). A *lower* crate may define a typed sub-config that its *parent* embeds — e.g. `eos-sandbox-api::config::SandboxTimeouts` is embedded into the single `sandbox` section owned by `eos-sandbox-host`, not exposed as its own top-level key. |

---

## 2. File layout

```
agent-core/config/config.yml       # NEW — committed baseline (defaults, no secrets); replaces deleted ephemeralos.yaml
agent-core/config/prd.yml          # NEW — gitignored production override (secrets + deploy values)
agent-core/config/README.md        # NEW — documents the model (mirror sandbox/config/README.md)
agent-core/.gitignore              # add: config/prd.yml  (and any *.local.yml convention)
agent-core/crates/<crate>/tests/**/<name>.yml   # per-test overrides, custom-named, YAML-only, no Rust schema
```

**Loader API** (`eos-config`, mirrors sandbox's two-function surface):
- `load() -> ConfigDocument` — production: read committed `config.yml`, then merge
  the gitignored `config/prd.yml` override **if present** (override wins).
- `load_with_override(path) -> ConfigDocument` — tests: read `config.yml`, then
  merge the explicit override file at `path` (any custom name). Replaces sandbox's
  `.test.yml`-suffix policy with an explicit caller-chosen path; `ConfigPath` still
  rejects a path that resolves to the baseline itself.

`config.yml` top-level shape (one section per owning crate):

```yaml
version: 1
database:       { ... }   # eos-db
sandbox:                  # eos-sandbox-host OWNS the section; `timeouts` type contributed by eos-sandbox-api
  docker:        { ... }
  overlay:       { ... }
  host_timeouts: { ... }  # host-side waits (spawn/readiness/git/bootstrap/…)
  timeouts:      { ... }  # per-verb RPC budgets (eos-sandbox-api::config::SandboxTimeouts)
providers:      { anthropic: {...}, openai: {...}, minimax: {...}, retry: {...} }  # eos-llm-client
llm_client:     { ... }   # eos-llm-client
engine:         { budget: {...}, advisor: {...} }   # eos-engine
tools:          { ... }   # eos-tools
workflow:       { ... }   # eos-workflow (incl. attempt knobs)
audit:          { ... }   # eos-audit
obs:            { ... }   # eos-obs-collector
plugin_catalog: { ... }   # eos-plugin-catalog
skills:         { ... }   # eos-skills
runtime:        { ... }   # eos-runtime (config/data/logs dirs)
```

---

## 3. Configurable-constant inventory (170 findings, classified)

Source: full-crate scan (10 parallel scanners + a completeness critic). The
discriminator is **tunable** (a deployment/test would reasonably change it) vs
**static contract** (changing it breaks correctness, not just behavior — stays
in Rust). Crates with no tunable surface get **no `config.rs`**.

### 3.1 Crates that get a `config.rs` (tunables to externalize)

| Crate / section | Tunables (current value → location) | Notes |
|---|---|---|
| **eos-db** `database` | url, pool_size(5), busy_timeout_ms(5000), wal(true), foreign_keys(true), echo(false) | Already modeled today as `DatabaseConfig`; **move the type into eos-db**. `DatabaseUrl` newtype + sqlite-only parse moves with it. |
| **eos-sandbox-host** `sandbox` (owner) — `.docker` / `.overlay` / `.host_timeouts` | docker.{daemon_tcp,privileged,no_privilege,default_snapshot}, default_provider, default_language("python"); overlay tmpfs options ("rw,exec,size=2g,mode=1777") + `disable_overlay_writable_tmpfs` flag; host_timeouts: DAEMON_SPAWN(20), READINESS(30), TCP_DEFAULT(60), BUNDLE_UPLOAD_JOIN(60), ENSURE_WORKSPACE_BASE(180), RUNTIME_READY(60), GIT_PROBE(10)/INSTALL(120), RUNNING_PROBE(10), PLUGIN_PACKAGE_UPLOAD(30), inline bootstrap probes(15s)/finalize(30s) | eos-sandbox-host **owns the whole `sandbox` section** (it is the parent crate). host_timeouts are **pure host-side waits** — the daemon doesn't care how long the host waits. The two `EOS_DOCKER_OVERLAY_*` **env reads become YAML fields**. cap_add/security_opt/loopback-bind are *borderline security posture* — see §5. |
| **eos-sandbox-api** → `sandbox.timeouts` sub-config | READ_FILE(60), WRITE_FILE(60), EDIT_FILE(20), EXEC_DEFAULT(60), EXEC_DISPATCH_GRACE(30), CONTROL(15), ISOLATED(180), isolated_exit_grace(5.0) | eos-sandbox-api defines the `SandboxTimeouts` type but **does not own a top-level key**; eos-sandbox-host embeds it as `sandbox.timeouts`. Finer-grained siblings of the existing `sandbox.timeout_s`. |
| **eos-llm-client** `llm_client` + `providers` | DEFAULT_MAX_TOKENS(32768), **missing HTTP client timeout** (reqwest has none today — a real gap), error-body truncation(500); per-provider {base_url, model} | retry policy already centralized as `RetryConfig` — keep. Provider base_urls are currently **hardcoded in eos-runtime** (anthropic/openai) while minimax is config — unify to `providers.{anthropic,openai,minimax}.{base_url,model}`. |
| **eos-engine** `engine` | EOS_COMMAND_HEARTBEAT_MS(1000) [env→YAML], budget tiers 75/100/125% + hard-ceiling 1.5× multiplier, advisor caps (MAX_TRANSCRIPT_MESSAGES 40, MAX_TOOL_RESULT_CHARS 4096, MAX_TRANSCRIPT_BYTES 24576, MAX_BASH_COMMAND_CHARS 500), fan-in channel floor(16)/factor(2) | **Budget cluster coupling:** the 1.5× ceiling is encoded twice (`loop_.rs:26` gate + `notifications/mod.rs:51` reminder) and the 125% tier must never exceed it — model the tiers + ceiling as **one validated struct** so they can't drift. |
| **eos-tools** `tools` | MAX_READ_FILE_LINES(200), default_yield_ms(1000), check_subagent default(5), exit grace(5.0), DEFAULT_MAX_WORKFLOW_DEPTH(1) | MAX_YIELD_TIME_MS(30000) and the 1..=10 page bound are **schema-coupled** (duplicated as `schemars(range)` annotations) — see §5. |
| **eos-workflow** `workflow` | max_concurrent_task_runs(8), default_attempt_budget(2) | `max_concurrent_task_runs` exists today as `attempt.max_concurrent_task_runs`; the in-crate fallback `8` (launch.rs:276) **duplicates** the config default and can drift — single-source it. `default_attempt_budget` literal physically lives in `eos-state::AttemptBudget::default()` but its config home is workflow. |
| **eos-audit** `audit` | BufferedJsonlSink channel capacity (injected by runtime, **no default today**), audit JSONL output path | Both are chosen at the eos-runtime call site with no central home — a textbook "should be config" gap. |
| **eos-obs-collector** `obs` | RunnerGateSettings.strict_audit_loss(true), require_resource_sample(true) | Two gate feature-flags. |
| **eos-plugin-catalog** `plugin_catalog` | PLUGIN_OP_TIMEOUT_MS(150000), LSP_SETUP_TIMEOUT_MS(60000) | Everything else (LSP ids, version, package-tree filenames, ppc_protocol_version) is **static package contract**. |
| **eos-skills** `skills` | DESCRIPTION_MAX_CHARS(200) — borderline | Layout names (SKILL.md, references/) are **static contract**. |
| **eos-runtime** `runtime` | config/data/logs dir defaults (`.ephemeralos`, `<cfg>/data`, `<cfg>/logs`) | Today resolved via `EPHEMERALOS_*_DIR` env — under decision #3 these become YAML fields with a fixed default. See §5 bootstrap-path note. |

### 3.2 Crates that get **no** `config.rs`

| Crate | Why |
|---|---|
| **eos-types** | Leaf value primitives (typed IDs, `UtcDateTime`, `JsonObject`). Everything is static contract (RFC3339, UUIDv4, id prefixes). |
| **eos-state** | DTO/persistence layer. Only `AttemptBudget::default()=2` — its config home is `workflow`. |
| **eos-agent-def** | Per-profile knobs (`tool_call_limit`, `model`) already live in the bundle's `.md` YAML frontmatter — not eos-config's surface. Loader layout names are static. |
| **eos-testkit** | Test infrastructure (`[dev-dependencies]` only). |
| **eos-config** | Becomes the loader itself — owns no section schema. |

### 3.3 Static contracts that LOOK like config but stay in Rust

Protocol/wire: `DAEMON_PROTOCOL_VERSION`, `PROTOCOL_VERSION`, `ppc_protocol_version`,
`ANTHROPIC_VERSION` (2023-06-01), `x-api-key` header, `THIN_CLIENT_*`/`EMPTY_RESPONSE_MESSAGE`
sentinels, `EDIT_CONFLICT_{CODES,MARKERS}`, `DAEMON_INTERNAL_ERROR_PREFIX`,
`WORKSPACE_BINDING_MISMATCH`, daemon env-var **names** (`EOS_DAEMON_TCP_*`).
Schema/version floors: audit `SCHEMA_VERSION`, daemon `manifest_version >= 1`.
Layout/ABI: `/eos` tmpfs mount + `/eos/...` daemon paths, `/proc/self/{io,status}`,
plugin package-tree filenames, skill `SKILL.md`/`references/`, `EOSD_SHA256` pins.
Security/algorithm: `DESTRUCTIVE_SHELL_PATTERN`, advisor strip-tool set,
exponential-backoff base, `sleep infinity` keep-alive.

**Borderline — flag for an explicit decision (see §5):** provider endpoint paths
(`/v1/messages`, `/v1/responses`), the `/eos/...` daemon path constants,
`DAEMON_TCP_INTERNAL_PORT(37657)`, `cap_add`/`security_opt`/loopback bind,
`BLOCKED_GIT_SUBCOMMANDS`, `LSP_SERVICE_ID`(pyright), agent-def bundle layout names.

---

## 4. Migration from the in-progress `CentralConfig`

> ⚠️ The current `eos-config` (CentralConfig + database/sandbox/providers/attempt
> sections, `loader.rs`, `env.rs`, `paths.rs`, `validation.rs`, schema-parity
> harness) is another agent's in-progress Python port. This spec **redirects**
> that work to the loader-only model rather than tearing it down — the typed
> section structs are largely reused, they just **move to their owning crates**.

| Current `eos-config` file | Disposition |
|---|---|
| `config.rs` (`CentralConfig`) | **Delete.** No composition root. |
| `loader.rs` (`defaults<YAML<env<init`) | **Replace** with sandbox-style `load_prd()`/`load_test_override()` over `ConfigDocument`. |
| `env.rs` (`EOS__*`, `_LEGACY_ENV_MAP`) | **Delete** (decision #3, no env). |
| `paths.rs` (`EPHEMERALOS_*` discovery) | **Delete** env discovery; replace with `ConfigPath::prd()` (fixed `agent-core/config/prd.yml`). Dir defaults move to `eos-runtime::config`. |
| `validation.rs` (central validate) | **Distribute:** each section validates itself (`DatabaseUrl::parse`, docker contradiction, range checks). |
| `database.rs` / `sandbox.rs` / `providers.rs` / `attempt.rs` | **Move** to `eos-db` / `eos-sandbox-host` / `eos-llm-client` / `eos-workflow` `src/config.rs`. |
| `markdown.rs` (`parse_markdown_frontmatter`) | **Move out** — it is a frontmatter helper, not config infra; relocate to its consumer (`eos-agent-def`/`eos-skills`) or a small shared util. |
| `error.rs` | **Generalize** to loader errors (read/parse/merge/section-deserialize/invalid-override-path), mirroring sandbox `ConfigError`. Section-specific errors move with their sections. |
| `schema_parity` tests + `tests/fixtures/*python_schema.json` | **Retire** — Python parity is no longer the contract; replace with per-section default/round-trip tests. |

### Phased execution (do not start a phase until the prior one's checks pass)

| Phase | Work | Verify |
|---|---|---|
| 0 | This spec + inventory sign-off | spec committed |
| 1 | Add `agent-core/config/prd.yml` + README; no code wiring yet | files present; **every value sourced from the current Rust constant/`Default` impl, NOT the deleted `ephemeralos.yaml`** — the two have already drifted (e.g. `providers.retry.status_codes` is `{429,500,502,503,529}` in the Rust default vs `…504` in the old yaml). A round-trip test (Phase 3) pins each section's default. |
| 2 | Rewrite `eos-config` as the generic loader (`ConfigDocument`/`ConfigPath`/`merge`/`section`); delete `CentralConfig`/`loader`/`env` | `cargo test -p eos-config`, `cargo check -p eos-config --all-targets` |
| 3 | Add `src/config.rs` to each owning crate (move `database`/`sandbox`/`providers`/`attempt`; add `engine`/`tools`/`audit`/`obs`/`plugin_catalog`/`skills`/`sandbox_host`/`runtime`) with `Default` + `validate()` | `cargo test -p <crate> config` per crate |
| 4 | Wire production loading: `eos-runtime` entry calls `load_prd()` once, deserializes sections, injects typed sub-configs (replaces hardcoded consts at call sites; remove the `EOS_*` env reads) | `cargo check -p eos-runtime --all-targets`; root request smoke test |
| 5 | Wire test overrides: per-test `*.test.yml` for crates that need non-prod values | targeted `cargo test` per crate |
| 6 | Remove env/CLI config selection: delete `env.rs`/env path discovery; grep-confirm no `EOS__`/`EPHEMERALOS_*`/`env::var` config reads remain (credentials excepted, §5) | `rg` clean scan recorded in this doc |
| 7 | Static-contract audit: confirm §3.3 constants stayed in Rust; resolve §5 borderlines | `rg` scan; no protocol/schema/layout constant in `prd.yml` |
| 8 | Docs + full verification | README refreshed; `cargo clippy --workspace --all-targets -- -D warnings`; targeted E2E |

---

## 5. Open boundary decisions

### 5.0 RESOLVED — secrets + overrides via a gitignored override file

**Decision (owner, 2026-06-05):** zero env reads, including secrets. The
committed `config.yml` carries only non-secret defaults; a **gitignored
`config/prd.yml`** (and any custom-named `local.yml`) carries secrets +
deployment values and is **merged over** the baseline, override wins. This is
the literal "no env variable" outcome — API keys live in the untracked override
file, read through the same merge machinery as the test overrides, never from
`std::env`.

Consequences threaded through this spec: `config.yml` is the committed baseline;
`prd.yml` is gitignored; the loader exposes `load()` (baseline + gitignored
`prd.yml` if present) and `load_with_override(path)` (baseline + explicit
test/local override of any name); the `providers` section's per-provider
`api_key` (and any secret-bearing field) is populated **only** from the override
file, so `config.yml` ships those fields empty.

### 5.1 Lower-stakes borderlines (recommend keep-in-Rust; resolve during Phase 7)

- **`/eos/...` daemon paths + `DAEMON_TCP_INTERNAL_PORT(37657)`** — host↔daemon
  layout contract, not operator knobs. Keep in Rust (matches sandbox Phase-7).
- **Schema-coupled tool caps** (`MAX_YIELD_TIME_MS`, `1..=10` page bound) —
  duplicated as `schemars(range(...))` + runtime guards; externalizing risks
  drift. Keep hardcoded (or generate the annotation from the const).
- **Provider endpoint paths** (`/v1/messages`, `/v1/responses`) — fixed by the
  API version the codec targets. Keep in Rust; only `base_url` (config) varies.
- **Security posture** (`cap_add`, `security_opt`, loopback bind) — load-bearing
  for namespace/overlay ops. Keep in Rust; revisit if a hardened-deploy story
  appears.

---

## 6. Acceptance criteria

- `eos-config` exposes only `ConfigDocument`, `ConfigPath`, `ConfigError`,
  `load_prd()`, `load_test_override()`, `section::<T>()` — **no `CentralConfig`,
  no `EnvMap`, no env or path-discovery surface.**
- Every crate in §3.1 owns a crate-root `src/config.rs` with a
  `deny_unknown_fields` + `Default` + `validate()` section type; no child module
  defines a `config.rs`.
- `agent-core/config/prd.yml` deserializes cleanly into every section; defaults
  match the constants they replace (round-trip test per section).
- No production `env::var` config read remains: `rg "EOS__|EPHEMERALOS_|env::var"`
  over `agent-core/crates/*/src` is clean. (Under §5.0 Option B this is absolute,
  including secrets; only if Option C is chosen does a single credential read in
  `eos-llm-client` survive, and it must be explicitly listed here.)
- Static contracts in §3.3 remain in Rust; `prd.yml` contains no protocol,
  schema-version, mount-layout, or wire constant.
