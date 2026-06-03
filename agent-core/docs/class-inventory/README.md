# agent-core Class Inventory

Generated inventory of every module-scope **struct, enum, trait, union, and
`pub` type alias** under `agent-core/crates/*/src/`, organized by crate. One
file per crate.

**380 types** across **124 files** in **15 crates**.

Declarations are enumerated with ripgrep; field, variant, trait-item, derive,
and line data is read directly from the Rust source. One-line purposes come
from `///` doc comments, or a reviewer summary where a doc comment is absent.
Only **module-scope** types are inventoried — test-only (`#[cfg(test)]` /
`mod tests`) and fn-local helper types are excluded.

> This is a generated reference, distinct from any hand-curated
> architecture/ownership memory layer.

**Rust → "class" mapping.** A struct/enum/trait/union/type-alias plays the role
of a class. `#[derive(...)]` ↔ Python decorators; a trait's supertraits
(`: Send + Sync`) ↔ base classes; struct fields / enum variants / trait items ↔
fields; inherent `impl` methods ↔ the collapsed `Methods (N)` block. The
`vis / attrs` field column carries `pub` visibility and serde attributes (Rust
has no per-field defaults).

| Crate | Types | struct / enum / trait / alias | Files | Inventory |
|-------|------:|-------------------------------|------:|-----------|
| `eos-types` | 19 | 15 / 1 / 1 / 2 | 4 | [eos-types.md](./eos-types.md) |
| `eos-config` | 13 | 10 / 2 / 0 / 1 | 8 | [eos-config.md](./eos-config.md) |
| `eos-state` | 33 | 12 / 12 / 8 / 1 | 10 | [eos-state.md](./eos-state.md) |
| `eos-db` | 16 | 15 / 1 / 0 / 0 | 9 | [eos-db.md](./eos-db.md) |
| `eos-audit` | 14 | 10 / 3 / 1 / 0 | 7 | [eos-audit.md](./eos-audit.md) |
| `eos-llm-client` | 22 | 13 / 7 / 1 / 1 | 9 | [eos-llm-client.md](./eos-llm-client.md) |
| `eos-agent-def` | 8 | 5 / 3 / 0 / 0 | 3 | [eos-agent-def.md](./eos-agent-def.md) |
| `eos-sandbox-api` | 34 | 28 / 4 / 1 / 1 | 5 | [eos-sandbox-api.md](./eos-sandbox-api.md) |
| `eos-skills` | 6 | 4 / 2 / 0 / 0 | 3 | [eos-skills.md](./eos-skills.md) |
| `eos-tools` | 92 | 73 / 11 / 8 / 0 | 19 | [eos-tools.md](./eos-tools.md) |
| `eos-engine` | 31 | 21 / 7 / 1 / 2 | 12 | [eos-engine.md](./eos-engine.md) |
| `eos-workflow` | 34 | 28 / 3 / 1 / 2 | 15 | [eos-workflow.md](./eos-workflow.md) |
| `eos-sandbox-host` | 24 | 15 / 5 / 3 / 1 | 8 | [eos-sandbox-host.md](./eos-sandbox-host.md) |
| `eos-plugin-catalog` | 20 | 18 / 2 / 0 / 0 | 5 | [eos-plugin-catalog.md](./eos-plugin-catalog.md) |
| `eos-runtime` | 14 | 10 / 1 / 1 / 2 | 7 | [eos-runtime.md](./eos-runtime.md) |
| **Total** | **380** | **277 / 64 / 26 / 13** | **124** | |

Crates are listed in dependency order: `eos-types` is the leaf; `eos-runtime`
is the composition root that wires the rest together.

> The `parity` workspace member (not under `crates/`) is a Phase-0 parity
> test/fixture harness — its types live in `tests/` and it exposes no
> module-scope public types, so it is intentionally excluded from this
> inventory.
