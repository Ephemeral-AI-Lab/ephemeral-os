# Sandbox docs index

| Document | What it is |
|---|---|
| `SPEC.md` | The target architecture spec the sandbox system implements (components, wire protocol, op catalog, lifecycle, recovery, conformance). |
| `API.md` | The public op reference. **Generated** from `../crates/eos-operation/ops.json` via `cargo run -p xtask -- gen-docs`; `check-contract` fails when stale. |
| `contract/01-wire-protocol.md` | FROZEN: the full daemon wire contract (framing, wire messages, auth, limits, error catalog) with source citations. |
| `contract/02-cas-byte-identity.md` | FROZEN: the two CAS content hashes, byte-for-byte, plus the 18 golden cases' law. |
| `contract/03-audit-and-metrics.md` | FROZEN: audit ring buffer + isolated-workspace JSONL channels and `layer_metrics`. Both audit channels were removed from the Rust runtime on 2026-06-11; only `layer_metrics` remains live. |
| `contract/04-shared-models.md` | FROZEN: request/response data-type contract for the verb surface. |
| `contract/06-crate-map-and-invariants.md` | FROZEN: historical crate map and invariants from the migration. |

The live, binding artifacts between the host and box sides are
`../crates/eos-operation/ops.json` plus `../contract/` (`PROTOCOL.md` +
`fixtures/`). The `docs/contract/` files above are the frozen historical
contracts they were distilled from; they do not change.
