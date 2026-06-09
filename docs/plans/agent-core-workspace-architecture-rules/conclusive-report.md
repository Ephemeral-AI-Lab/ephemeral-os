# Agent-Core Workspace Architecture Rules - Conclusive Report

Status: Structural closeout complete
Date: 2026-06-09
Owner: agent-core workspace

## Verdict

The architecture-rules migration is structurally closed at the current
10-crate, 167-module shape. The final module budget is satisfied because 167 is
within the accepted 150-170 target range, and the enforcing workspace-guard
budget gate passes with `EOS_WORKSPACE_GUARD_FINAL_LAYOUT=1`.

The current generated inventory reports:

| Metric | Current |
| --- | ---: |
| Crates | 10 |
| Modules | 167 |
| Items | 1407 |
| Methods | 903 |

This closes the architecture-shape question. Any further reductions in
`eos-engine`, `eos-types`, `eos-workflow`, `eos-db`, or `eos-llm-client` are
owner-local follow-up work, not blockers for the global architecture-rules
budget.

## Evidence

Source inventory:
`agent-core/docs/class-inventory/html/assets/inventory.json`, generated
2026-06-09 19:30:24 CST.

Verification run for this report:

| Command | Result | Evidence |
| --- | --- | --- |
| `cargo test -p workspace-guard` | Passed | crate inventory, layout, DAG, naming, public-surface, service-boundary, and module-budget guard tests passed |
| `EOS_WORKSPACE_GUARD_FINAL_LAYOUT=1 cargo test -p workspace-guard module_budget_report_is_available -- --nocapture` | Passed | final layout gate enabled; total modules reported as 167; advisory per-crate overages reported without failing |

The second command reported these module counts:

| Crate | Modules |
| --- | ---: |
| `eos-agent-core-server` | 9 |
| `eos-agent-run` | 7 |
| `eos-db` | 15 |
| `eos-engine` | 32 |
| `eos-llm-client` | 15 |
| `eos-sandbox-port` | 23 |
| `eos-testkit` | 6 |
| `eos-tool` | 14 |
| `eos-types` | 28 |
| `eos-workflow` | 18 |
| **Total** | **167** |

The generated class inventory resolves to the full current status:

| Crate | Modules | Items | Methods |
| --- | ---: | ---: | ---: |
| `eos-agent-core-server` | 9 | 20 | 8 |
| `eos-agent-run` | 7 | 57 | 37 |
| `eos-db` | 15 | 120 | 79 |
| `eos-engine` | 32 | 256 | 218 |
| `eos-llm-client` | 15 | 162 | 62 |
| `eos-sandbox-port` | 23 | 157 | 49 |
| `eos-testkit` | 6 | 14 | 4 |
| `eos-tool` | 14 | 350 | 143 |
| `eos-types` | 28 | 163 | 173 |
| `eos-workflow` | 18 | 108 | 130 |
| **Total** | **167** | **1407** | **903** |

## Closing State

| Area | Conclusion |
| --- | --- |
| Crate map | Closed at the approved 10 target crates |
| Retired crates | Guarded by workspace-guard crate inventory rules |
| Module budget | Closed at 167 modules, within the 150-170 range |
| Final layout gate | Passing with `EOS_WORKSPACE_GUARD_FINAL_LAYOUT=1` |
| Per-crate caps | Advisory only; remaining overages are documented owner-local follow-ups |
| Item and method counts | Current regenerated totals are 1407 items and 903 methods |
| Broad workspace tests/clippy | Not re-run for this report; keep the Phase 06 tracker as the source of truth for those wider gates |

Final call: the global architecture-rules plan has reached its target shape.
Future cleanup should be opened as targeted owner-crate work instead of
reopening the global crate-map/module-budget migration.
