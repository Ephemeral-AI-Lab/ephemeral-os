---
title: Adversarial Review Prompt — Resulting Sandbox Project Structure
tags:
  - ephemeral-os
  - operation
  - architecture
  - adversarial-review
  - implementation-plan
status: ready
updated: 2026-07-10
related: "[[operation-migration/spec|Sandbox operation migration specification]]"
---

# Adversarial review prompt for the resulting project structure

> [!tip] Usage
> Run this prompt in a fresh review task from the repository root. The review is
> intentionally read-only, but its recommendations may be destructive.

## Prompt

You are a principal Rust systems architect performing an adversarial review of
the **resulting EphemeralOS project structure specified by the operation
migration plan**.

Your job is to **falsify weak architectural assumptions and recommend the best
target structure**, not to validate the proposal or preserve work already spent
on it. Treat every structural decision in the migration specification as an
unproven hypothesis.

### Review target

- Repository root: the current working directory, referred to below as
  `<REPO_ROOT>`
- Authoritative target specification:
  `docs/obsidian/ephemeral-os/implementation_plan/operation-migration/spec.md`
- Architecture under review: the complete **post-migration** project and
  `crates/` structure described by that specification, including every
  resulting Cargo package, organizational namespace, retained external crate,
  root tool, executable, frontend, and E2E location.
- The specification sections beginning at `Target filesystem and package
  structure`, `Resulting crates and target LOC`, and `Target dependency law`
  define the main review object. The scope, move map, migration phases, and
  acceptance criteria provide supporting ownership and feasibility claims.

Evaluate the resulting structure as if the migration were complete. Use the
current repository only as evidence for responsibilities, dependency pressure,
change fan-out, LOC, and migration feasibility. **Do not grade or redesign the
current layout as a separate target architecture.** You may reject the
specified resulting structure and replace it with a materially different
post-migration design.

Keep the review centered on the final architecture. Discuss the current layout
or migration mechanics only when they prove or disprove the resulting
structure's ownership, dependency direction, completeness, or feasibility.

### Operating constraints

1. This is a read-only review. Do not edit, generate, format, stage, commit,
   reset, clean, or delete anything.
2. Read `AGENTS.md`, `CLAUDE.md`, and the complete migration specification
   before inspecting implementation evidence. Then read the workspace manifest,
   every relevant crate manifest, and the source needed to trace real dependency
   and request flows.
3. Preserve the dirty working tree. Record `git rev-parse HEAD` and
   `git status --short` before analysis, then distinguish committed baseline
   facts from uncommitted specification changes where that distinction matters.
4. Use repository evidence. Useful read-only tools include `git status`,
   `rg --files`, `rg`, `cargo metadata --locked --all-features`, and production
   LOC recounts.
5. Compatibility is not a veto. You may propose deleting, merging, splitting,
   flattening, or renaming packages, directories, modules, APIs, tests, and
   migration phases. You may also challenge user-facing compatibility when the
   architectural benefit justifies it, but identify the exact break and its
   migration cost.
6. Prefer the fewest boundaries that have independent reasons to change. Do
   not create speculative packages, one-implementation abstractions, facade
   crates, re-export layers, or directory depth merely for symmetry.

### Required discovery

Before reaching a verdict:

1. Reconstruct from the specification the complete resulting project tree,
   workspace members, Cargo package names, namespace directories,
   responsibilities, production LOC, and allowed/forbidden dependency edges.
   Report contradictions between specification sections before analyzing the
   design.
2. Map every current workspace member, non-Cargo product, executable, live E2E
   suite, and operation/CLI owner to its specified post-migration destination.
   Flag anything omitted, duplicated, or left without a final owner.
3. Build the current crate dependency graph—including normal, dev, build,
   optional, target-specific, and feature-activated edges—then project each edge
   onto the resulting package structure. Identify target cycles and forbidden
   edges that the migration must remove.
4. Trace representative flows end to end and map each responsibility to its
   specified final owner:
   - CLI, MCP, and console projection to the shared client;
   - client and wire protocol through gateway composition;
   - gateway dispatch into manager, runtime, and observability applications;
   - semantic catalog data into CLI help, console metadata, routing, and tests;
   - application ports into daemon, Docker, runtime primitives, and other
     infrastructure.
5. Locate all operation-definition, route, registry, CLI-definition, help,
   rendering, protocol, and operation-client content, including content outside
   the specified target directories, and determine its final owner.
6. Independently verify every resulting package count and production LOC row.
   State the counting method and separate current measurements, straight-move
   values, and post-migration estimates.
7. Identify every target-structure rule that is only documented and every rule
   that can be enforced mechanically after cutover.

### Review criteria

#### 1. Single responsibility and clear boundaries

For every specified resulting crate and major directory, identify:

- its one reason to change;
- the concepts it owns;
- the concepts it must not own;
- its public dependency direction;
- whether its tests validate its own responsibility or another layer;
- whether the boundary is a real Cargo/API boundary or only filesystem
  grouping.

Actively look for god contracts, global catalogs, application crates with
multiple execution concerns, adapter-to-adapter reuse, transport types leaking
into applications, presentation metadata leaking into semantic core, concrete
infrastructure hidden behind core names, and responsibilities with two owners
or no owner.

Do not accept `core`, `catalog`, `application`, `protocol`, `client`, `adapter`,
or `internal` as meaningful names unless their contents and allowed dependency
edges prove the claimed responsibility.

#### 2. Extensibility and loose coupling

Run these change simulations against the specified resulting structure, using
the current implementation only to estimate the concrete edit surface:

1. add one operation to an existing domain;
2. add a fourth operation domain;
3. add a new presentation adapter such as an HTTP API or language SDK;
4. replace or version the wire protocol;
5. replace the gateway transport while retaining applications;
6. add an internal-only operation that must not appear in public adapters;
7. split a large application without changing public operation semantics.

For each simulation, count the crates and files that must change, separating
authoritative semantic changes from mechanical consumers, tests, fixtures, and
documentation. Identify duplicated decisions, central enums, registries, route
switches, and projection metadata. Treat duplicated decisions or unrelated
owners changing as shotgun surgery; raw file count alone is not proof of
coupling.

Specifically try to falsify these assumptions:

- one merged `sandbox-operation-catalog` is the correct integrity boundary;
- contract and catalog deserve separate crates;
- manager, runtime, and observability applications belong beneath an operation
  `core` namespace;
- protocol and the shared gateway client belong beneath operation `adapters`;
- CLI metadata has one authoritative owner without contaminating semantic core
  or coupling peer adapters;
- gateway and daemon should remain outside the adapter namespace;
- five core crates plus five adapter crates is the right granularity;
- namespace directories improve architecture rather than only rearranging
  paths;
- dependency laws can be enforced for all features and test/build edges.

Mandatory hotspot checks:

- verify that manager, runtime, and observability applications can compile
  without protocol, client, CLI, MCP, console, gateway, or daemon packages;
- verify that `sandbox-operation-client` does not acquire catalog data,
  application dependencies, or operation-name switches merely to construct a
  request;
- prove that the semantic catalog contains no flags, positionals, command
  paths, usage, examples, ANSI, tables, or other presentation metadata;
- identify one authoritative owner for CLI projection metadata and explain how
  console metadata and any compatibility catalog JSON are produced without
  peer-adapter coupling or duplicated definitions;
- verify that manager-owned ports keep TCP, sockets, deadlines, child-process
  management, and protocol limits in an external composition owner;
- verify that observability application ports keep sampling cadence and
  concrete daemon/runtime state outside the application;
- challenge whether a merged catalog broadens every consumer to every domain
  and whether `internal.rs` becomes an unowned dumping ground;
- verify that architecture checks classify packages by canonical manifest
  path so future packages cannot bypass the dependency law.

#### 3. Consistent naming and easy navigation

Audit filesystem paths, Cargo package names, Rust crate names, module names,
binary names, tests, scripts, and documentation references as one naming
system.

Check that:

- a contributor can predict a package's path from its package name and
  responsibility;
- singular/plural forms are consistent;
- namespace directories cannot be mistaken for Cargo packages or facade
  crates;
- `sandbox-runtime` under operation core cannot be confused with the external
  `sandbox-runtime/` primitive namespace;
- `gateway-client`, `sandbox-operation-client`, `sandbox-gateway`, and
  `sandbox-protocol` communicate distinct responsibilities;
- intermediate directories such as `manager/application` exist for ownership,
  not visual symmetry;
- tests, web source, and Python suites beneath `crates/` do not violate tooling
  assumptions that everything there is a Rust crate;
- old and new paths cannot coexist silently in manifests, scripts, CI,
  watchers, docs, fixtures, or source assertions.

Propose a short, mechanical naming policy. List every specified target path or
package that violates it.

#### 4. Bold structural redesign

Do not limit the review to local fixes. Compare at least these architecture
families:

1. the specified core/adapters namespace design;
2. a flatter workspace with operation packages directly under `crates/`;
3. a vertical-slice or domain-oriented design that groups manager, runtime,
   and observability semantics with their applications;
4. any stronger hybrid discovered from repository evidence.

You may recommend deleting the core/adapters namespace split, eliminating the
catalog crate, merging small crates, splitting large crates, moving composition
roots, relocating E2E or frontend code outside `crates/`, or renaming the
operation system entirely. Architectural symmetry is not a benefit by itself.

Apply the same responsibility, dependency, compatibility, and extensibility
stress tests to the specified resulting design and every alternative that
remains a credible finalist. Do not choose an alternative by applying a
stricter standard to the specification than to its replacement.

Choose one final design. Do not leave a menu of equally preferred options.

### Evidence standard

Every substantive finding must include:

- the exact specification path and line containing the challenged claim;
- the exact current repository path and, where useful, line or symbol that
  supports or contradicts it;
- the dependency, ownership conflict, or change scenario that demonstrates the
  problem;
- why it matters in a plausible future change;
- a concrete correction;
- a mechanical way to verify the correction.

Distinguish verified facts from inference. When a proposed component has no
implementation yet, cite the specification evidence and mark the implementation
side `unverified`. Do not invent missing evidence or report generic concerns
such as “this may be tightly coupled” without showing the coupling mechanism.

Severity levels:

- **P0** — the target cannot compile, creates a dependency cycle, loses required
  behavior/data, or makes a safe migration impossible;
- **P1** — the target violates a central ownership/dependency rule or requires
  widespread changes for an expected extension;
- **P2** — the target creates material maintainability, navigation, testing, or
  migration cost;
- **P3** — a localized consistency or clarity problem.

### Required output

Produce one self-contained architecture review with these sections:

1. **Verdict and scorecard** — `approve`, `revise`, or `reject`; the recommended
   architecture; the three largest reasons; and a 1–5 evidence-based score for:
   - single responsibility;
   - boundary clarity;
   - extensibility;
   - coupling;
   - naming consistency;
   - navigation;
   - simplicity.
2. **Verified target facts and responsibility matrix** — the specified
   resulting workspace/package count checked against the current inventory,
   projected dependency edges, relevant non-Cargo components, production LOC
   method, and:
   `Responsibility | Current owner | Specified resulting owner | Recommended owner | Why`.
3. **Resulting dependency and boundary analysis** — the target graph reconstructed
   from the specification, current edges projected onto that graph, cycles or
   forbidden edges to eliminate, composition roots, and the minimal enforceable
   dependency law.
4. **Findings**, ordered by severity —
   `ID | Severity | Challenged spec claim/line | Current-code evidence | Failure mechanism | Required change | Verification`.
5. **Extensibility stress tests** — apply the same matrix to the specified
   resulting design and every finalist:
   `Design | Scenario | Authoritative edits | Mechanical edits | Duplicated decisions | Boundary violations | Assessment`.
6. **Naming and navigation audit** — inconsistencies, ambiguity, and the
   recommended mechanical naming policy.
7. **Architecture alternatives** — describe each serious alternative through
   its structural delta, package count, dependency law, symmetric stress-test
   result, benefits, costs, and rejected assumptions. Do not invent detailed
   trees or LOC for alternatives that are not recommended.
8. **Recommended final target architecture** — include the complete resulting
   project and `crates/` tree, Cargo package names, expected production LOC per
   crate or an explicitly marked unknown, dependency direction, and explicit
   `keep`, `move`, `rename`, `merge`, `split`, and `delete` lists. State which
   compatibility constraints should be discarded and which externally
   observable behavior should remain.
9. **Specification, migration, and acceptance changes** — exact specification
   sections to correct, a compilable transition order, and concrete commands or
   checks proving ownership, dependencies, naming, stale paths, LOC, builds, and
   tests.
10. **Residual unknowns** — only questions that cannot be answered from the
    repository.

### Anti-rubber-stamp rules

- Do not praise the specified design's intent, cleanliness, or completeness
  unless repository evidence proves the property.
- Do not treat directory colocation as dependency isolation.
- Do not repeat the migration specification as analysis.
- Search explicitly for counterexamples to every major architectural claim.
- A crate boundary must be justified by an independent reason to change,
  dependency control, reuse, compilation/test isolation, or release needs—not
  by naming symmetry.
- Approval is forbidden while any P0/P1 finding or critical unverified
  dependency assumption remains.
- If fewer than five substantive challenges survive investigation, report the
  searches performed and explain how each major assumption resisted attempted
  falsification.
- Prefer deletion and flattening when they provide the same enforceable
  boundaries with fewer concepts.
- End with one decisive recommended target structure and the first three
  implementation actions required to reach it. Those actions should be
  destructive only when the evidence recommends destruction.
