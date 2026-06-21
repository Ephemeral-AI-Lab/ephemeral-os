# Sandbox Help Command And Operation Metadata Implementation Prompt

Use this prompt to implement the scoped help command and richer operation
metadata described by:

```text
docs/refactoring/sandbox-help-command-operation-spec.md
```

```text
You are working in:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Implement the scoped sandbox CLI help command and family-aware operation
metadata described by:

docs/refactoring/sandbox-help-command-operation-spec.md

This is an implementation task, not a review. Complete the work end to end:
code, tests, docs, and verification.

Before editing, read:

- docs/refactoring/sandbox-help-command-operation-spec.md
- docs/refactoring/sandbox-cli.md
- docs/refactoring/sandbox-protocol.md
- docs/refactoring/sandbox-runtime.md
- crates/sandbox-protocol/src/operation_spec.rs
- crates/sandbox-protocol/src/catalog.rs
- crates/sandbox-manager/src/operation/mod.rs
- crates/sandbox-manager/src/operation/impls/mod.rs
- crates/sandbox-manager/src/operation/impls/management/mod.rs
- crates/sandbox-runtime/operation/src/public/mod.rs
- crates/sandbox-runtime/operation/src/public/command/mod.rs
- crates/sandbox-runtime/operation/src/public/command/service/impls/mod.rs
- crates/sandbox-gateway/src/cli/request_builder.rs
- crates/sandbox-gateway/src/cli/output.rs
- crates/sandbox-gateway/tests/gateway_cli.rs

Important context:

- The active spec supersedes older phase-7 flat manual/catalog instructions.
- Use `help`, not `manual`.
- Do not keep or add a `sandbox-cli manual` compatibility alias.
- Do not add filesystem discovery or build-script autoloading.
- Family and operation registration is explicit Rust metadata through static
  slices.
- Runtime help usage/examples must not show `--sandbox-id`.
- Manager operations are only:
  - `create_sandbox`
  - `destroy_sandbox`
  - `list_sandboxes`
  - `inspect_sandbox`
- Runtime operations are only:
  - `exec_command`
  - `write_command_stdin`
  - `poll_command`
  - `read_command_lines`
  - `cancel_command`

Worktree rules:

- Start with `git status --short --untracked-files=all`.
- The worktree may already contain unrelated staged or unstaged changes.
- Do not revert, reset, checkout, or reformat unrelated files.
- If a file you need to edit already has unrelated changes, inspect it and work
  with the current content.
- Keep changes scoped to the help/operation metadata implementation.

Required implementation shape:

1. Protocol metadata model

   Update `sandbox-protocol` so the operation catalog can describe families and
   detailed operation pages.

   Add or update:

   ```rust
   pub struct OperationFamilySpec {
       pub id: &'static str,
       pub title: &'static str,
       pub summary: &'static str,
       pub description: &'static str,
   }

   pub struct OperationSpec {
       pub name: &'static str,
       pub family: &'static str,
       pub summary: &'static str,
       pub description: &'static str,
       pub args: &'static [ArgSpec],
       pub cli: Option<CliSpec>,
       pub related: &'static [&'static str],
   }

   pub struct OperationCatalog {
       pub operation_execution_space: OperationExecutionSpace,
       pub families: &'static [&'static OperationFamilySpec],
       pub operations: &'static [&'static OperationSpec],
   }
   ```

   Add owned document equivalents:

   ```rust
   pub struct OperationFamilyDocument { ... }
   pub struct OperationSpecDocument { ... }
   pub struct OperationCatalogDocument { ... }
   ```

   Catalog JSON must include:

   ```json
   {
     "operation_execution_space": "runtime",
     "families": [],
     "operations": []
   }
   ```

   Catalog decoding must reject:

   - duplicate family ids;
   - an operation whose `family` does not exist;
   - duplicate operation names;
   - a `related` operation name that does not exist in the same catalog.

2. Protocol help renderer

   Replace old manual renderer vocabulary with help vocabulary. Prefer a new
   `crates/sandbox-protocol/src/help.rs` if no help module exists.

   Provide protocol-owned rendering/search helpers:

   ```rust
   pub fn render_catalog_help(catalog: &OperationCatalogDocument) -> String;
   pub fn render_operation_help(
       catalog: &OperationCatalogDocument,
       operation: &str,
   ) -> Result<String, HelpRenderError>;
   pub fn search_operation_help(
       catalog: &OperationCatalogDocument,
       query: &str,
   ) -> Vec<OperationSearchResult>;
   ```

   Rendering rules:

   - Overview groups operations by `catalog.families` order.
   - Operations inside a family preserve catalog operation order.
   - Detailed pages include family, description, usage, arguments, examples,
     and related operations when present.
   - Unknown operation lookup returns an error and suggestions; it must not
     silently render the overview.

3. Runtime family ownership

   Implement the runtime `Command` family at the family boundary:

   ```text
   crates/sandbox-runtime/operation/src/public/command/mod.rs
   ```

   Add:

   ```rust
   pub(crate) const COMMAND_FAMILY: OperationFamilySpec = OperationFamilySpec {
       id: "command",
       title: "Command",
       summary: "Run, interact with, inspect, and cancel commands.",
       description: "Run, interact with, inspect, and cancel commands inside the active sandbox runtime.",
   };
   ```

   Expose:

   ```rust
   pub(crate) const fn operation_families() -> &'static [&'static OperationFamilySpec];
   ```

   Thread runtime family metadata through:

   ```text
   crates/sandbox-runtime/operation/src/public/mod.rs
   crates/sandbox-runtime/operation/src/lib.rs
   ```

   Keep command operation specs as local `SPEC` constants in:

   ```text
   crates/sandbox-runtime/operation/src/public/command/service/impls/*.rs
   ```

   Add `family`, `description`, and `related` to each runtime operation spec.
   Runtime command CLI usage/examples used for help must not include
   `--sandbox-id`.

4. Manager family ownership

   Implement the manager `Management` family at:

   ```text
   crates/sandbox-manager/src/operation/impls/management/mod.rs
   ```

   Add:

   ```rust
   pub(crate) const MANAGEMENT_FAMILY: OperationFamilySpec = OperationFamilySpec {
       id: "management",
       title: "Management",
       summary: "Create, destroy, list, and inspect sandbox records.",
       description: "Create, destroy, list, and inspect sandbox records. Daemons are managed as part of sandbox lifecycle behavior, not as standalone manager operations.",
   };
   ```

   Move manager operation specs out of:

   ```text
   crates/sandbox-manager/src/operation/specs.rs
   ```

   into the matching family operation modules:

   ```text
   crates/sandbox-manager/src/operation/impls/management/create_sandbox.rs
   crates/sandbox-manager/src/operation/impls/management/destroy_sandbox.rs
   crates/sandbox-manager/src/operation/impls/management/list_sandboxes.rs
   crates/sandbox-manager/src/operation/impls/management/inspect_sandbox.rs
   ```

   Each manager operation module should own a local `SPEC` plus its dispatch
   function, matching the runtime pattern:

   ```rust
   pub(crate) const SPEC: OperationSpec = OperationSpec {
       name: "create_sandbox",
       family: "management",
       summary: "Create a host-side sandbox record and runtime sandbox.",
       description: "Create a host-side sandbox record, create the runtime sandbox, and start its daemon.",
       args: CREATE_SANDBOX_ARGS,
       cli: Some(CREATE_SANDBOX_CLI),
       related: &["list_sandboxes", "inspect_sandbox", "destroy_sandbox"],
   };
   ```

   Then make:

   ```text
   crates/sandbox-manager/src/operation/impls/management/mod.rs
   ```

   own the family-local `FAMILIES`, `SPECS`, and dispatch `OPERATIONS` slices.

   Make:

   ```text
   crates/sandbox-manager/src/operation/impls/mod.rs
   ```

   aggregate:

   ```rust
   operation_families()
   operation_specs()
   operation_entries()
   ```

   After the move, `crates/sandbox-manager/src/operation/specs.rs` must not own
   individual operation specs. Keep it only if it is still useful as catalog
   wiring; otherwise remove it and update exports.

5. Catalog constructors

   Update catalog constructors so they receive explicit family and operation
   slices:

   ```rust
   OperationCatalog::new(
       OperationExecutionSpace::Manager,
       impls::operation_families(),
       impls::operation_specs(),
   )

   OperationCatalog::new(
       OperationExecutionSpace::Runtime,
       public::operation_families(),
       public::operation_specs(),
   )
   ```

   Keep manager and runtime catalogs separate:

   - manager catalog contains only manager operations;
   - runtime catalog contains only runtime operations.

6. Gateway CLI help command

   Update the CLI behavior to support:

   ```text
   sandbox-cli manager help
   sandbox-cli manager help create_sandbox

   sandbox-cli runtime help
   sandbox-cli runtime help exec_command
   ```

   Preserve parser help separately:

   ```text
   sandbox-cli manager --help
   sandbox-cli runtime --help
   ```

   `help` is reserved and cannot be used as an operation name.

   `manager help` should render from manager catalog metadata already available
   to the CLI/gateway process. Do not add a public catalog-discovery manager
   operation for this.

   `runtime help` should use the selected/default sandbox runtime catalog path.
   If no default sandbox is available, fail directly with:

   ```text
   runtime help requires a default sandbox
   ```

   Unknown operation help should fail with suggestions, for example:

   ```text
   unknown runtime operation for help: exec

   Did you mean:
     exec_command
       Start a command in a workspace.

   Use:
     sandbox-cli runtime help
   ```

7. Tests

   Add or update focused tests covering:

   - protocol catalog JSON includes `families`;
   - protocol decoding rejects duplicate family ids;
   - protocol decoding rejects operations referencing missing families;
   - protocol decoding rejects duplicate operation names;
   - protocol decoding rejects `related` references to missing operations;
   - protocol help overview groups by family;
   - protocol detailed help renders one operation;
   - protocol unknown operation lookup returns suggestions;
   - manager catalog exposes only `Management` and the four manager operations;
   - runtime catalog exposes only `Command` and the five command operations;
   - `sandbox-cli manager help` renders grouped manager help;
   - `sandbox-cli manager help create_sandbox` renders detailed help;
   - `sandbox-cli runtime help` renders grouped runtime help;
   - `sandbox-cli runtime help exec_command` renders detailed help;
   - runtime help output does not contain `--sandbox-id`;
   - `sandbox-cli manual` is rejected.

8. Documentation

   Update active docs that describe the CLI/protocol:

   ```text
   docs/refactoring/sandbox-cli.md
   docs/refactoring/sandbox-protocol.md
   docs/README/sandbox-runtime.md
   ```

   Do not rewrite historical phase prompts except where an active doc link is
   broken or directly misleading for the new implementation.

Verification:

Run:

```sh
cargo fmt --check -p sandbox-protocol -p sandbox-manager -p sandbox-gateway -p sandbox-runtime
cargo check -p sandbox-protocol -p sandbox-manager -p sandbox-gateway -p sandbox-runtime --tests
cargo test -p sandbox-protocol -p sandbox-manager -p sandbox-gateway -p sandbox-runtime
git diff --check
```

Also run targeted text checks:

```sh
rg -n "sandbox-cli manual|\\bmanual\\b|OperationFamilySpec|COMMAND_FAMILY|MANAGEMENT_FAMILY|--sandbox-id" \
  crates/sandbox-protocol crates/sandbox-manager crates/sandbox-runtime/operation crates/sandbox-gateway docs/refactoring docs/README
```

Interpretation:

- `manual` should not remain in active command names or renderer names.
- `--sandbox-id` may still exist for real manager operations and runtime
  invocation/config behavior, but must not appear in runtime help usage or
  examples.
- Historical prompt files may still mention old phase behavior; do not treat
  them as active implementation requirements unless they are referenced by the
  new spec.

Final response:

- Summarize the implemented structure.
- List tests/verification commands and results.
- Mention any pre-existing failures separately.
- Mention any unrelated worktree changes you intentionally left untouched.
```
