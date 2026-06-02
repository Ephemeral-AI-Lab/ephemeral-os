# impl-eos-skills — runtime skill registry and config-rooted bundled skill loader

> Owning crate in the agent-core workspace. Conforms to ./spec-conventions.md.
> Plan section: ../backend_agent_core_rust_migration_PLAN.md §14 (`eos-skills`),
> with cross-cutting guidance at PLAN lines 1144-1150 ("Plugins and skills").

## 1. Purpose & Responsibility (SRP)

`eos-skills` owns the **runtime skill content** exposed to agents: it defines the
`SkillDefinition` value type, the `SkillRegistry` lookup surface, and a
deterministic loader that reads directory-based skills (`<skill-name>/SKILL.md`
plus an optional `references/*.md` set) from a single configured skill root. Its
sole job is *load skill definitions into an immutable in-memory registry and
answer lookups by name*. It is the source of truth for skill `references` content
that the `load_skill_reference` tool (owned by `eos-tools`) serves.

This crate must **NOT**: own the `load_skill_reference` `ToolSpec`/executor
(that lives in `eos-tools`, see impl-eos-tools.md); know about `AgentDefinition`,
agent-to-skill binding, or allowlist scoping (that is the tool factory's job in
`eos-tools`/`eos-agent-def`); build the row-4 skill message that injects the
skill body at agent launch (that is `eos-workflow`'s context-engine job); perform
any filesystem traversal outside the configured skill directory; or watch/reload
the directory at runtime (load is one-shot at composition).

## 2. Dependencies

- **Upstream crates (depends on):**
  - `eos-types` — `CoreError` participation only if a shared error variant is
    needed; otherwise no type dependency. `eos-skills` does **not** mint domain
    IDs. (Owned types referenced: none structural; see anchor §5.)
  - `eos-config` — resolves the skill-root path (`get_config_skills_dir`
    equivalent) and central-config path resolution. The skill root is a
    config-provided `&Path`, never discovered from `cwd`. This edge also covers
    the `---`-delimited YAML frontmatter split: `parse_markdown_frontmatter` is a
    config-format helper owned by `eos-config` (Python `config/markdown.py` is
    imported by agent-def, skills, and context-engine alike), so `eos-skills`
    calls it rather than re-implementing it. (See impl-eos-config.md for
    `CentralConfig`, path resolution, and the frontmatter split.)
- **Downstream consumers (used by):**
  - `eos-tools` — calls `SkillRegistry::get(&SkillName)` and reads
    `SkillDefinition.references` to serve `load_skill_reference`
    (contract `{skill_name, reference_name}`; see impl-eos-tools.md).
  - `eos-runtime` — constructs the registry once at the composition root, wraps
    it in `Arc`, and injects it into the tool layer (see impl-eos-runtime.md).

- **External crates** (pinned via workspace dependency inheritance,
  `proj-workspace-deps`; declared `{ workspace = true }` in this crate's
  `Cargo.toml`):

  | Crate | Why | rust-skills rule |
  |---|---|---|
  | `thiserror` | the one library error enum `SkillLoadError` (`err-thiserror-lib`, `err-custom-type`); no `Box<dyn Error>` in public signatures | `err-thiserror-lib` |
  | `serde` (`Serialize` only) | `SkillDefinition` is an in-memory value type; `Serialize` is derived solely so the serde serialize-snapshot test (AC-skills-08) can pin its shape. No `Deserialize`: nothing reconstructs a `SkillDefinition` from JSON (the loader builds it from markdown; `eos-tools` reads it in-memory via `registry.get()`). | `api-common-traits` |

  No `tokio`, no `async-trait`: loading is synchronous filesystem I/O performed
  **once** at composition root before the Tokio runtime needs it, and lookups are
  pure in-memory map reads. Keeping this crate runtime-agnostic matches anchor §7
  ("Lower crates are runtime-agnostic"). No `walkdir`/glob crate is pulled in:
  traversal is a single `read_dir` at the configured root plus one `read_dir` of
  each skill's `references/` directory — deliberately shallow (see §8, GC-skills-02).

## 3. Scope & Source Mapping

| Python source | Rust target | What moves / what is dropped |
|---|---|---|
| `backend/src/skills/core/types.py` (`SkillDefinition`) | `src/definition.rs` | Moves: all six fields. `source: str` becomes a `SkillSource` enum (`type-no-stringly`); `path: str \| None` becomes `Option<PathBuf>`; `references: dict[str,str]` becomes `BTreeMap<ReferenceName, String>` for deterministic ordering. |
| `backend/src/skills/core/registry.py` (`SkillRegistry`) | `src/registry.rs` | Moves: `register`, `get`, `list_skills`. `list_skills` already sorts by name; preserve via `BTreeMap<SkillName, SkillDefinition>` keying. |
| `backend/src/skills/bundled/__init__.py` (`get_bundled_skills`, `_parse_skill_metadata`) | `src/bundled.rs` | Moves: directory walk, the skills-specific `_parse_skill_metadata` heading/first-paragraph fallback, reference discovery. Calls `eos-config`'s `parse_markdown_frontmatter` for the `---` split (does not re-implement it). Drops nothing functional; `print`/logging-free. |
| `backend/src/skills/core/loader.py` (`load_skill_registry(cwd=...)`) | `src/loader.rs` | Moves the orchestration. **DROPS the ignored `cwd` parameter** (Python does `del cwd`); the Rust loader takes an explicit `skill_root: &Path` from config (GC-skills-01). |
| `backend/src/config/markdown.py` (`parse_markdown_frontmatter`) | **NOT here** → `eos-config` | The generic `---`-delimited YAML frontmatter split is a config-format helper (Python `config/markdown.py` is imported by agent-def, skills, and context-engine). It is owned by `eos-config` and called from `bundled.rs`; `eos-skills` does not re-implement it (anchor §1: one definition per shared contract). |
| `backend/src/tools/skills/load_skill_reference.py` | **NOT here** → `eos-tools` (`tool.rs`) | The tool `ToolSpec`/executor is owned by `eos-tools`; only its data contract `(skill_name, reference_name)` is anchored against this registry (see §5, §8). |
| `backend/src/tools/skills/_factory.py` (allowlist/agent scoping) | **NOT here** → `eos-tools` | Agent-name → skill-slug resolution and `available` allowlist are tool/agent concerns, not registry concerns. |

**In scope:** `SkillDefinition`, `SkillSource`, `SkillName`, `ReferenceName`,
`SkillRegistry`, `SkillRegistry::load_from_dir`, the skills-specific
`_parse_skill_metadata` heading/first-paragraph fallback, the
`SkillLoadError` enum.
**Out of scope:** the `load_skill_reference` tool surface, agent/skill binding,
the row-4 skill message, runtime reload/watch, any `cwd`/repo-root walk.

## 4. File & Module Layout

```
eos-skills/
  Cargo.toml
  src/
    lib.rs          # crate docs + `pub use` re-exports (proj-pub-use-reexport)
    definition.rs   # SkillDefinition, SkillSource, SkillName, ReferenceName newtypes
    registry.rs     # SkillRegistry: register/get/list_skills over BTreeMap
    loader.rs       # load_skill_registry(skill_root) orchestration entry point
    bundled.rs      # directory walk + references/*.md + _parse_skill_metadata fallback
    error.rs        # SkillLoadError (thiserror)
```

`lib.rs` re-exports the public surface: `SkillDefinition`, `SkillSource`,
`SkillName`, `ReferenceName`, `SkillRegistry`, `SkillLoadError`, and the free
function `load_skill_registry`. `bundled.rs` internals (including the
`_parse_skill_metadata` fallback) are `pub(crate)`
(`proj-pub-crate-internal`) and only the loader entry point is public.

## 5. Contracts Owned Here

Per anchor §5, this crate owns **`SkillDefinition`, `SkillRegistry`, loader**, and
is the implementor of the **`SkillRegistry` seam** in anchor §6 (the
"bundled/config-dir loader"). There is no separate `SkillProvider` trait: the
seam is the registry type itself plus its single `load_from_dir` constructor, and
extension happens by registering more `SkillDefinition`s (OCP) — adding a
speculative trait would violate anchor §1 / YAGNI.

Signature sketches (full field tables in §6):

```rust
pub struct SkillRegistry { skills: BTreeMap<SkillName, SkillDefinition> }

impl SkillRegistry {
    /// Empty registry (api-default-impl: also `Default`).
    pub fn new() -> Self;
    /// Insert one skill, replacing any same-named entry (last-wins, matches Python dict).
    pub fn register(&mut self, skill: SkillDefinition);
    /// Lookup by skill name; `None` if absent.
    pub fn get(&self, name: &SkillName) -> Option<&SkillDefinition>;
    /// All skills in `SkillName` order (BTreeMap guarantees sort; matches Python `sorted`).
    pub fn list_skills(&self) -> impl Iterator<Item = &SkillDefinition>;
    /// Deterministic config-rooted load (the seam's only constructor).
    pub fn load_from_dir(skill_root: &Path) -> Result<Self, SkillLoadError>;
}

/// Composition-root entry point. Thin wrapper that simply calls
/// `SkillRegistry::load_from_dir`; it exists only to give `eos-runtime` a
/// free-function name parallel to the Python `load_skill_registry`. The
/// filesystem logic lives in exactly one place (`load_from_dir`), so this is a
/// rename, not a second implementation (KISS).
pub fn load_skill_registry(skill_root: &Path) -> Result<SkillRegistry, SkillLoadError>;
```

**Object-safety / async note:** `SkillRegistry` is a concrete struct, not a
`dyn` trait; no `#[async_trait]` is needed. The composition root holds it as
`Arc<SkillRegistry>` for cheap sharing (`own-arc-shared`), not behind `dyn`. This
respects anchor §6's preference for `impl Trait`/concrete over `Box<dyn Trait>`
when no heterogeneous storage is required (`anti-type-erasure`).

**Contracts merely USED (references only, not redefined here):**
- `load_skill_reference` `ToolSpec` + executor and the `(skill_name,
  reference_name)` input contract — owned by `eos-tools` (see impl-eos-tools.md
  §Tools, anchor §5). This crate only guarantees the registry shape that contract
  reads against.
- Skill-root path resolution / `CentralConfig` — owned by `eos-config` (see
  impl-eos-config.md).
- `CoreError` / `JsonObject` — owned by `eos-types` (anchor §5).

## 6. Types, Fields & Schemas

### `SkillDefinition` (source: `skills/core/types.py`)

| Field | Rust type | serde notes | Source-of-truth |
|---|---|---|---|
| `name` | `SkillName` | `String` newtype; serde transparent over `String`. The **parsed** name (frontmatter-first, dir-name fallback) and the registry key — a faithful 1:1 port of Python (see "Registry key parity" below). | `types.py` `name: str` ← `_parse_skill_metadata` |
| `description` | `String` | plain | `types.py` `description: str` |
| `content` | `String` | full `SKILL.md` text | `types.py` `content: str` |
| `source` | `SkillSource` | enum, `#[serde(rename_all = "snake_case")]` | `types.py` `source: str` (`"bundled"` in practice) |
| `path` | `Option<PathBuf>` | `#[serde(default)]`; serialize as string | `types.py` `path: str \| None = None` |
| `references` | `BTreeMap<ReferenceName, String>` | `#[serde(default)]`; ordered by stem | `types.py` `references: dict[str,str]` |

These are the same six fields as the Python `@dataclass(frozen=True)`; no field is
added or dropped.

> **Docstring note (`references`):** Python's `references` field carries the
> docstring "Mapping of reference name → file content, lazily loadable", but that
> "lazily loadable" claim is **misleading** — `bundled/__init__.py` reads every
> `references/*.md` **eagerly** at load (lines 33-34). The Rust port deliberately
> keeps eager loading (see §9 non-goals); no behavior change is implied by
> dropping the inaccurate "lazily loadable" wording.

**Registry key parity (and a latent Python inconsistency this crate does NOT
resolve).** This crate keys the registry by `SkillName` = the **parsed** `name`,
exactly as Python's `registry.py` does (`self._skills[skill.name] = skill`). It is
a 1:1 port. There is a known latent inconsistency in the Python tool layer:
`registry.py` keys by the parsed `skill.name` while the consumer
`tools/skills/_factory.py` (line 87) looks skills up by
`AgentDefinition.skill.parent.name` — the **folder slug** — and keys its
`available` allowlist by `skill.name` (line 58). These coincide today because the
frontmatter `name` equals the folder name for all bundled skills (`planner`,
`reducer`, `executor`); a frontmatter `name` ≠ its folder would make the factory's
`registry.get(slug)` return `None` and silently drop the skill from `available`.
That divergence belongs to the tool/allowlist contract, which this crate does not
own: this crate guarantees only `get(&SkillName) -> Option<&SkillDefinition>`
keyed by the parsed name. Reconciling the lookup key, the `available` keying, and
the value the model passes as `skill_name` is **owned by eos-tools** — see
impl-eos-tools.md. (Here `executor` is an on-disk skill-folder slug — content under
`backend/config/skills/executor/` — not the Rust execution-role state name, which
is `generator` per anchor §4.)

Derives `Debug, Clone, PartialEq, Eq` (`api-common-traits`) and `Serialize`
(serialize-snapshot test only; no `Deserialize`). Marked `#[non_exhaustive]`
(`api-non-exhaustive`) since the plan reserves room for future skill metadata.
The Python type is `@dataclass(frozen=True)`; the Rust analogue is an immutable
value type (no `&mut` accessors).

### `SkillSource` (new enum; was a free `str`)

| Variant | serde rename | Meaning |
|---|---|---|
| `Bundled` | `bundled` | loaded from the configured skill root (the only producer today) |

`#[non_exhaustive]` so additional sources (e.g. a future user-dir source) can be
added without breaking match sites at the seam. Replacing the stringly `source`
with an enum follows `type-no-stringly` / `type-enum-states`.

### `SkillName` and `ReferenceName` (validated newtypes)

```rust
/// A skill name — the parsed name (frontmatter-first, dir-name fallback) and the
/// registry key, matching Python `registry.py`. Non-empty, no path separators.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(transparent)]
pub struct SkillName(String);

impl SkillName {
    /// Parse-don't-validate: rejects empty names and names containing a path
    /// separator (`/`, `\\`), a `..` path component, or NUL. A bare `.` is
    /// **allowed** (a `ReferenceName` derived from a `.md` stem like `api.v2`
    /// must round-trip), matching Python — dotted stems key fine into `references`.
    pub fn parse(s: impl Into<String>) -> Result<Self, SkillLoadError> { /* ... */ }
    pub fn as_str(&self) -> &str { &self.0 }
}
```

`ReferenceName` is the same shape, keyed off the file **stem** (matches Python
`ref_file.stem`); it accepts dotted stems like `api.v2`. Both follow
`api-parse-dont-validate` / `type-newtype-validated` / `type-no-stringly`.
The mechanical guarantee behind GC-skills-02 (no traversal escape) is **not** the
newtype validation — it is that model-supplied skill/reference names are used
**only as map keys** (`registry.get(name)` / `references.get(name)`) and are
**never path-joined**, mirroring Python where `skill_dir`/`refs_dir`/`ref_file`
all come from `iterdir`/`glob` and the model's `skill_name`/`reference_name` reach
only dict lookups. The newtype's separator/`..`/NUL rejection is defense-in-depth
on top of that. `Ord` derivation gives the `BTreeMap` its deterministic ordering
for free, replacing Python's explicit `sorted(...)`.

### `SkillLoadError` (this crate's one error enum, `err-thiserror-lib`)

```rust
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum SkillLoadError {
    #[error("skill root is not a directory: {0}")]
    RootNotDir(PathBuf),
    #[error("failed to read skill directory {path}")]
    ReadDir { path: PathBuf, #[source] cause: std::io::Error },
    #[error("failed to read skill file {path}")]
    ReadFile { path: PathBuf, #[source] cause: std::io::Error },
    #[error("invalid skill name {0:?}")]
    InvalidName(String),
}
```

There is **no** `Frontmatter`/malformed-YAML variant: matching Python's
`parse_markdown_frontmatter` (`config/markdown.py` lines 21-24), malformed YAML or
non-dict frontmatter is **swallowed** — it yields empty frontmatter and proceeds
to the `_parse_skill_metadata` heading/first-paragraph fallback rather than failing
the load. Surfacing a load error on one skill's broken YAML would be a behavior
change, so the port does not do it (see §8 and AC-skills-09).

Messages are lowercase, no trailing punctuation (`err-lowercase-msg`); `#[source]`
chains the underlying cause (`err-source-chain`); `#[from]` is intentionally
omitted on the io variants because the same upstream error type maps to
multiple variants (need path context), so conversion is explicit at the call site
via `.map_err(...)` (`err-from-impl` applies only where one-to-one).

**Metadata-fallback parity (must preserve `_parse_skill_metadata`):** the parsed
metadata feeds `name` and `description`, exactly as Python. When frontmatter lacks
`name`, `name` defaults to the directory name; when it lacks `description`, scan
the **full content** (the entire `SKILL.md` text including any frontmatter lines —
Python iterates `content.splitlines()` and discards the post-split `_body`) for the
first `# ` heading (used as `name` iff it is still the default) and the first
non-blank line that does **not** start with `#` or `---`, truncated to 200 chars;
final description fallback is `"Bundled skill: {name}"`. Because frontmatter key
lines such as `name: planner` start with neither `#` nor `---`, Python picks them
up as the description when `description` is absent from frontmatter; scanning the
full content (not the post-frontmatter body) preserves that behavior exactly (see
AC-skills-10). This is a faithful port of the *string outputs* of
`bundled/__init__.py` lines 49-67, including that the parsed `name` is the registry
key.

## 7. Concurrency & State Ownership

- **Runtime:** none owned here. Loading is synchronous `std::fs` I/O run **once**
  at the composition root (`eos-runtime`) before/around runtime startup; this
  crate never spawns a runtime and uses no `async` (anchor §7: lower crates are
  runtime-agnostic). Synchronous `std::fs` is correct precisely because this is
  *not* on an async path (`async-tokio-fs` does not apply — there is no executor
  to block).
- **Shared immutable state:** after `load_from_dir`, the `SkillRegistry` is
  **immutable** and shared as `Arc<SkillRegistry>` (`own-arc-shared`), cloned
  cheaply into the tool layer. The process-global `@lru_cache` registry in
  Python's `_factory._registry()` becomes a single `Arc` constructed once in the
  DI graph — no global mutable singleton, no interior mutability.
- **Shared mutable state:** none. `register` takes `&mut self` and is used only
  during the single-threaded load phase before the registry is frozen into
  `Arc`. There are no locks, so the "never hold a lock across `.await`" rules
  (`async-no-lock-await`, `anti-lock-across-await`) are satisfied vacuously.
- **Channels / cancellation:** none — there is no background work.
- **CPU-bound work:** none of consequence; skill files are small markdown. No
  `spawn_blocking` needed.

State ownership summary: the registry map is **owned** by `SkillRegistry`,
**shared** as `Arc` post-construction, and **never** placed behind a lock.

## 8. Behavior & Invariants

Semantics to preserve (cite source files):

1. **Directory format** (`bundled/__init__.py` 22-44): iterate the skill root in
   sorted order; for each subdirectory, require `SKILL.md`; read its full text as
   `content`. Skip subdirectories without `SKILL.md` (no error). Skip non-dir
   entries.
2. **Reference discovery** (`bundled/__init__.py` 29-34): if `references/` exists
   under a skill dir, read each `*.md` file (sorted) and key it by file **stem**
   into `references`. No recursion below `references/`; no other globs. `.gitkeep`
   and non-`.md` files are ignored — matches `glob("*.md")`.
3. **Metadata** (`bundled/__init__.py` 49-67): frontmatter `name`/`description`
   first; then the heading/first-paragraph fallback; then
   `"Bundled skill: {name}"`. (Detailed in §6.)
4. **Registry semantics** (`registry.py`): `register` is last-wins by key; `get`
   returns `Option`; `list_skills` is key-sorted. The `BTreeMap<SkillName, _>`
   makes ordering an invariant of the data structure, not a per-call sort. The key
   is the **parsed `name`** (`self._skills[skill.name]`), a 1:1 port; the
   loader/allowlist key reconciliation is an eos-tools concern (see §6 "Registry
   key parity" and impl-eos-tools.md), not resolved here.
5. **Loader root** (`loader.py` + `paths.py`): the root is exactly
   `get_config_skills_dir()` ≡ `<repo>/backend/config` `/skills` in source
   checkouts. The Python loader **ignores `cwd`** (`del cwd`). The Rust loader
   takes the resolved root from `eos-config` and performs **no** `cwd`/repo-walk
   (GC-skills-01).
6. **Reference-loading determinism** (PLAN §14 gap + lines 1148-1149): given the
   same skill root, `load_from_dir` produces byte-identical `references` maps in a
   stable (`BTreeMap`) order, with no implicit traversal outside the configured
   directory (GC-skills-02). This is the property the `load_skill_reference` tool
   (eos-tools) depends on: `registry.get(skill_name)?.references.get(reference_name)`
   is the only lookup path, mirroring `load_skill_reference.py` lines 63-82.

**Subtle risks called out in the plan:**
- *cwd ambiguity* (PLAN §14): resolved by GC-skills-01 — single explicit
  config-root, no cwd.
- *implicit traversal* (PLAN §14, line 1149): resolved by GC-skills-02 — model
  names are used only as map keys and are **never path-joined** (mirroring
  Python), and the loader never follows `..` or symlinks outside the root: only
  `read_dir` of the root and each `references/` dir. The path-separator/`..`/NUL
  rejection on the name newtypes is defense-in-depth, not the primary guarantee.
- *empty/missing root*: returns an **empty** registry (Python returns `[]` when
  `_CONTENT_DIR` does not exist). A root path that *exists but is not a directory*
  is `RootNotDir` (fail fast) — a stricter-than-Python parse-don't-validate choice
  justified because a non-dir root is a config error, not a "no skills" state.

## 9. SOLID & Principles Applied

- **SRP:** load + hold skill definitions; nothing about tools, agents, or context
  messages (those cross boundaries — see §1). The crate is one of the smallest in
  the workspace by design.
- **OCP:** behavior extends by registering more `SkillDefinition`s or adding a
  `SkillSource` variant, never by editing a dispatch `match`. The `SkillRegistry`
  is the OCP registry seam (anchor §6).
- **DIP:** consumers (`eos-tools`) depend on the concrete registry's small read
  surface (`get`, `references`); the composition root injects the constructed
  `Arc<SkillRegistry>`. No upstream crate is reached across.
- **ISP:** the public surface is four methods (`new`/`register`/`get`/
  `list_skills`) plus the loader; no god-object.
- **LSP:** `SkillSource`/name newtypes make invalid states unrepresentable; no
  subtype substitution hazards.
- **KISS/YAGNI/DRY:** **no** trait abstraction over the registry (a single
  implementor exists; a trait would be speculative — anchor §1). **No** runtime
  reload, watcher, async, or caching layer (no current caller needs them). The
  `cwd` parameter is dropped rather than carried as dead config. The generic
  `---` frontmatter split is **not** duplicated here: it is a config-format helper
  with multiple consumers today (agent-def, skills, context-engine), so it is owned
  by `eos-config` and called from `bundled.rs` (anchor §1: one definition per
  shared contract). Only the skills-specific `_parse_skill_metadata` fallback lives
  in this crate.
- **Non-goals respected:** no tool visibility enum, no deferred/lazy model-facing
  tool loading (the registry is fully built at composition; references are read
  eagerly into memory, matching Python), no peer-to-peer or orchestration
  concerns. The `load_skill_reference` `ToolSpec` is built concretely in
  `eos-tools` at agent spawn, not here.

## 10. Gap Closeouts (tracked requirements)

- **GC-skills-01 — `cwd` vs config-root decision (PLAN §14 first bullet).**
  *Resolution:* **config-root-only.** The Rust loader signature is
  `load_skill_registry(skill_root: &Path)`; the root is resolved exclusively by
  `eos-config` (the analogue of `get_config_skills_dir()` →
  `<repo>/backend/config/skills`). The Python `cwd` parameter is **removed**, not
  carried — it was always ignored (`del cwd`). There is no repo-root walk and no
  process-`cwd` discovery. Proven by AC-skills-04.
- **GC-skills-02 — deterministic, non-escaping reference loading (PLAN §14 second
  bullet + line 1149).** *Resolution:* references are read by a single
  `read_dir(skill_dir/"references")` filtered to `*.md`, keyed by the file stem
  (`ReferenceName`, dotted stems like `api.v2` accepted), and stored in a
  `BTreeMap` for stable order. The non-escape guarantee is that model-supplied
  skill/reference names are used **only as map keys** (`registry.get` /
  `references.get`) and are **never path-joined** (mirroring Python); the
  separator/`..`/NUL rejection on the name newtypes is defense-in-depth, not the
  primary guarantee. The loader never follows `..` and never resolves symlinks
  outside the root. Two loads of the same tree are byte-identical and identically
  ordered. Proven by AC-skills-02, AC-skills-03, AC-skills-06.
- **GC-skills-03 — single explicit skill root (PLAN lines 1148: "Skill loading
  should have one explicit root").** *Resolution:* exactly one root, passed in;
  `load_from_dir` is the only constructor that touches the filesystem. No
  layered/merged roots. Proven by AC-skills-04.

> **Cross-reference NOTE (not a gap closeout):** the parsed-`name`-key vs
> folder-slug-lookup divergence between `registry.py` and `tools/skills/_factory.py`
> is a real latent Python inconsistency, but it is owned by the eos-tools
> allowlist/`available` contract, not by this crate. PLAN §14's only gap bullets
> for `eos-skills` are GC-skills-01 (cwd vs config-root) and GC-skills-02
> (deterministic, non-escaping reference loading); GC-skills-03 records the single
> explicit root. This crate ports the registry key 1:1 (parsed `name`) and defers
> the reconciliation to impl-eos-tools.md.

## 11. Acceptance Criteria

TDD: write each test first, confirm it fails for the right reason, then implement.
Maps to anchor §11 "Tests to Port First" row `eos-skills` →
"reference-loading determinism".

- **AC-skills-01 — directory-skill parity.** Given a temp skill root with
  `alpha/SKILL.md` (frontmatter `name`/`description`) and `beta/SKILL.md` (no
  frontmatter, with a `# Heading` and a paragraph), `load_skill_registry` returns
  two skills with the expected `name` (frontmatter `name` for `alpha`, heading
  fallback for `beta`, dir-name otherwise), `description`, `content`, and
  `source = Bundled`. *Proving test:*
  `loader::tests::loads_directory_skills_with_metadata_fallback`.
- **AC-skills-02 — reference discovery by stem, sorted.** A skill with
  `references/checklist.md` and `references/rubric.md` (plus a `.gitkeep` and a
  `notes.txt`) yields `references` keyed `{"checklist", "rubric"}` only, in sorted
  order, with file contents intact. *Proving test:*
  `bundled::tests::discovers_only_md_references_keyed_by_stem`.
- **AC-skills-03 — load determinism.** Loading the same fixture root twice
  produces equal `SkillRegistry` values (`assert_eq!`), including identical
  `references` map ordering. *Proving test:*
  `loader::tests::load_is_deterministic`. (Directly proves GC-skills-02.)
- **AC-skills-04 — config-root-only, no cwd.** Changing the process working
  directory between two loads of the same explicit `skill_root` does not change
  the result; the loader exposes no `cwd` parameter. *Proving test:*
  `loader::tests::ignores_process_cwd`. (Proves GC-skills-01/03.)
- **AC-skills-05 — missing vs non-dir root.** A non-existent root yields an
  **empty** registry (`list_skills().count() == 0`); a root path that exists but
  is a file yields `Err(SkillLoadError::RootNotDir)`. *Proving test:*
  `loader::tests::missing_root_empty_nondir_root_errors`.
- **AC-skills-06 — name validation blocks traversal (defense-in-depth).**
  `SkillName::parse("../x")` and `ReferenceName::parse("a/b")` return `Err`; a
  `references/` filename whose stem would contain a separator is rejected, not
  silently keyed. A **dotted** stem like `ReferenceName::parse("api.v2")` is
  **accepted** (a bare `.` is not a separator), matching Python's `ref_file.stem`.
  *Proving test:* `definition::tests::rejects_path_separators_accepts_dotted_stems`.
- **AC-skills-07 — registry contract, keyed by `SkillName`.** `register` is
  last-wins by `SkillName` key; `get(&name)` returns the registered skill or
  `None`; `list_skills` is `SkillName`-sorted. *Proving test:*
  `registry::tests::register_get_list_semantics`.
- **AC-skills-08 — serde serialize snapshot.** Serializing a representative
  `SkillDefinition` (all six fields populated) to JSON matches a committed
  snapshot: field names present, `source` rendered snake_case, `references`
  ordered by key, `path` serialized as a string. This pins the value type's wire
  shape; it makes **no** Pydantic-schema claim (Python's `SkillDefinition` is a
  frozen `@dataclass` with no Pydantic model or JSON schema). *Proving test:*
  `definition::tests::skill_definition_serialize_snapshot`.
- **AC-skills-09 — broken frontmatter falls back, never fails the load.** A
  `SKILL.md` whose `---`-delimited frontmatter block is malformed YAML (or parses
  to a non-dict) **still loads**: frontmatter is treated as empty and the
  description comes from the `_parse_skill_metadata` fallback, matching Python's
  `parse_markdown_frontmatter` swallowing `yaml.YAMLError`. No `SkillLoadError` is
  raised for malformed frontmatter. *Proving test:*
  `bundled::tests::malformed_frontmatter_uses_fallback_description`.
- **AC-skills-10 — name-present/description-absent scans full content.** A
  `SKILL.md` with frontmatter `name` set but no `description` and no `#` heading
  yields the first non-blank line that is not `#`/`---` — which, scanning the full
  content, is a frontmatter key line (e.g. `name: planner`), exactly as Python
  picks it up. *Proving test:*
  `bundled::tests::description_falls_back_to_full_content_lines`.
- **AC-skills-11 — dotted reference stem round-trips.** A `references/api.v2.md`
  file is keyed as `"api.v2"` in `references` (not dropped, not rejected),
  matching Python `ref_file.stem`. *Proving test:*
  `bundled::tests::dotted_reference_stem_is_keyed`.

## 12. Implementation Checklist

Ordered, small, verifiable steps (`small-incremental-changes`):

1. Scaffold crate per anchor §14 (workspace member, inherited deps, workspace
   lints). `cargo build` is green with an empty `lib.rs`.
2. `error.rs`: define `SkillLoadError` (thiserror, `#[non_exhaustive]`).
3. `definition.rs`: `SkillName`/`ReferenceName` newtypes with `parse`
   (separator-rejecting), `SkillSource` enum, `SkillDefinition` struct + derives.
   Write AC-skills-06 first.
4. `registry.rs`: `SkillRegistry` over `BTreeMap`; `new`/`register`/`get`/
   `list_skills`. Write AC-skills-07 first.
5. `bundled.rs`: directory walk + reference discovery + the `_parse_skill_metadata`
   fallback (calling `eos-config`'s `parse_markdown_frontmatter` for the `---`
   split; do not re-implement it). Write AC-skills-02, 09, 10, 11 first; unit-test
   the fallback branches (broken-frontmatter, full-content scan, dotted stem).
6. `loader.rs`: `load_from_dir`/`load_skill_registry(skill_root)` orchestration;
   empty-vs-non-dir handling. Write AC-skills-01, 03, 04, 05 first.
7. `definition.rs`: commit the serde serialize snapshot for `SkillDefinition`.
   Write AC-skills-08.
8. `lib.rs`: `pub use` re-exports; `cargo clippy -D warnings` + `cargo fmt
   --check` clean.

---
**On completion:** update the Progress Tracker in `./overview.md` for row
`eos-skills` per spec-conventions.md §13 (status + date + short note + commit/PR
ref). Do not edit other crates' rows.
