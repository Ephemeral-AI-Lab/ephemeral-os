---
name: team-root-planner-playbook
description: Playbook for the root_planner agent. Analyze, cluster, scout owner rows, synthesize, then submit a schema-valid root DAG with submit_plan(...).
---

# Team Root Planner Playbook

Produce the top-level task DAG from the user request. Finish with exactly one `submit_plan(...)` call.

<Forbid Rule>
Never plan test suite or test-file related tasks.
Never assign subagents to explore test suites or test files.
</Forbid Rule>

| Route | Use when |
| --- | --- |
| `developer` | Atomic slice: exact owner + one mechanism + small failure surface. |
| `team_planner` | Broad, matrix, clustered, complex, or unresolved row. |
| `validator` | Same-payload verification after producer lanes. |

| Gate | Action |
| --- | --- |
| Every owner row in the cluster ledger | Dispatch one production scout per row (deep file or directory map); unrelated rows use separate parallel scouts. |
| Test-only evidence | Keep in request/spec context; do not inspect, scout, or assign test paths. |

## Stage Flow

```text
Caption: root planner stage machine. References load only at the stage that uses them.

user request
  |
  v
analyze -> cluster -> scout -> synthesize -> DAG
  |         |          |        |             |
request   owner rows  notes    draft lanes   submit_plan(...)
```

| Stage | Output |
| --- | --- |
| Analyze | Split request facts from test/benchmark evidence and production clues. |
| Cluster | Owner ledger grouped by changelog axes: owner, mechanism, API, engine, format. |
| Scout | One production row per scout; unrelated rows go in one parallel wave. |
| Synthesize | After the scout wave returns, load reference and draft lanes. |
| DAG | Submit the schema-valid task graph. |

## 1-2. Analyze + Cluster

```text
Caption: every owner row is a scout target until a scout returns. Pre-scout, all production claims are guesses from test names.

request
  |-- commands / benchmark ids / failing tests -> evidence (spec only)
  |-- exact production file or symbol ---------> scout target (deep)
  |-- broad family / matrix / migration -------> scout target (directory)
  `-- guessed or test-derived owner -----------> scout target (directory)
```

| Check | Root-planner action |
| --- | --- |
| Clustering | Group by changelog axes (owner, mechanism, API, engine, format). F2P/P2P ids are acceptance criteria, not grouping axes. |
| Cluster name | One cluster = one owner family. Multi-owner names like "CLI/Config/Compat" or "Storage I/O" are defects — split before scout. |
| Benchmark evidence | Keep tests and ids in evidence/spec, not workspace or scout targets. |

Routing stops at owner rows; HDF, JSON, parquet, groupby, utils, CLI, config, and compatibility are separate rows unless live evidence proves a tight producer-consumer pair.

## 2. Scout

```text
Caption: scout fan-out is proportional: one row per call, package maps for broad rows, no parent-dir batching.

owner ledger
  |-- exact file/symbol row ------> one deep single-path scout
  |-- package/engine row ---------> one superficial directory scout
  |-- unrelated rows -------------> separate scouts in one parallel wave
  `-- still broad after map ------> team_planner handoff
```

| Scout shape | Use when |
| --- | --- |
| Single/multi-path | One owner row or one tight coupled pair (engine+adapter, producer+consumer); same parent directory is insufficient. |
| Directory | Package, subsystem, engine matrix, or package-like import path; keep superficial. |
| Row wave | Independent production families; separate scouts in one parallel wave, never one batched call. |
| Forbidden batch | Split `cli.py`+`config.py`+`compat.py`, HDF+JSON/parquet, groupby+utils, and HDF+parquet+groupby into separate row scouts. |

Use `input.target_paths` (not `prompt`); production paths only; missing/disproved → directory scout or handoff. Never call workspace/scout tools on test paths.

## 4-5. Synthesize + DAG

Enter after the scout wave returns. Load the synthesize reference; synthesize scout findings into the DAG — it need not mirror clustering.

```text
Caption: root routing during synthesis.

atomic + small surface -> developer
broad / matrix cluster -> team_planner sibling
same-payload evidence  -> validator with production scopes
```

| Draft check | Expected result |
| --- | --- |
| Coverage | Every named cluster has a producer owner or sibling `team_planner`; tiny slices stay separate. |
| Developer lanes | Exactly one production owner file (or one tight coupled pair within one mechanism); ≥2 unrelated owner files in `scope_paths` (e.g. `cli.py`+`config.py`+`compat.py`, HDF+parquet+groupby) force a `team_planner` lane instead — a CLI→config→compat call chain is not "one mechanism". |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. |
| Validators | Required when any producer lane writes a same-payload suite; depend on every such producer; `scope_paths` are production surfaces. |
| Payload | `id`, `agent`, `spec`, `deps`, and `scope_paths` only. |

Run this checklist, then make `submit_plan({ "new_tasks": [...] })` the final assistant action: no summary, output, parent ids, trailing prose, or later tool calls.
