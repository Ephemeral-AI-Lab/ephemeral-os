# Phase 7 Prompt: Stabilize Catalog And Manual Contract

Use this prompt after phase 6 has completed.

```text
You are working in:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Implement phase 7 only: stabilize the catalog and manual contract shared by
`sandbox-protocol`, `sandbox-manager`, and `sandbox-gateway-cli`.

The goal is to make manager and runtime operation spaces discoverable by
humans, agents, and the CLI through one protocol-owned catalog/manual shape.
Do not rename runtime support packages and do not add new runtime operations in
this phase.

Before editing, read:

- docs/refactoring/sandbox-implementation-guide.md
- docs/refactoring/sandbox-protocol.md
- docs/refactoring/sandbox-manager.md
- docs/refactoring/sandbox-gateway-cli.md
- docs/refactoring/sandbox-manager-daemon-split.md
- docs/refactoring/sandbox-runtime.md

Required starting state:

- `crates/sandbox-protocol` exists.
- `sandbox_protocol::Request` exists.
- `sandbox_protocol::Response` exists.
- `sandbox_protocol::OperationCatalog` exists.
- `sandbox_protocol::OperationExecutionSpace` exists.
- `crates/sandbox-manager` exists.
- `crates/sandbox-manager/src/server` exists.
- `crates/sandbox-gateway-cli` exists.
- `crates/sandbox-gateway-cli/src/manual.rs` exists.
- `crates/sandbox-gateway-cli/src/request_builder.rs` exists.
- `crates/sandbox-daemon` exists.
- `crates/sandbox-runtime/operation` exists.
- There is no active `OperationRequest`, `OperationResponse`,
  `SandboxRequest`, `RoutedRequest`, `ManagerRequest`, `OperationTarget`, or
  `invoke_sandbox_daemon`.

If this starting state is not true, stop and report which earlier phase is
missing or incomplete. Do not implement phase 7 on top of a routed-request
wrapper or the old `OperationRequest` / `OperationResponse` API.

Phase goal:

- Make `OperationCatalog` the one catalog contract for manager and runtime
  operation discovery.
- Keep the only execution-space selector as `operation_execution_space`.
- Keep `OperationFamily` as documentation grouping only.
- Ensure manager catalog output contains only manager operations.
- Ensure runtime catalog output contains only runtime operations.
- Ensure catalog JSON is produced and parsed by protocol-owned helpers rather
  than duplicated ad hoc structs in manager/gateway.
- Ensure CLI/manual text is rendered from cataloged `OperationSpec` data.
- Keep `sandbox-gateway-cli` as a protocol client that talks only to
  `sandbox-manager`.

Packages changed:

```text
sandbox-protocol
sandbox-manager
sandbox-gateway-cli
```

Keep in `sandbox-protocol`:

- `OperationCatalog`
- `OperationExecutionSpace`
- `OperationSpec`
- `ArgSpec`
- `CliSpec`
- Shared catalog JSON conversion/parsing helpers.
- Shared manual helper functions over catalog/spec data.

Keep out of `sandbox-protocol`:

- Manager operation lists.
- Runtime operation lists.
- Socket clients or listeners.
- Manager dispatch.
- Daemon/runtime dispatch.
- Command, workspace, layerstack, overlay, namespace, or container semantics.

Keep in `sandbox-manager`:

- Concrete manager operation specs.
- Manager operation dispatch.
- `describe_manager_operations`.
- `describe_daemon_operations` or the already-established equivalent runtime
  catalog operation.
- Forwarding sandbox-scoped runtime requests to the sandbox daemon.

Keep in `sandbox-gateway-cli`:

- CLI parsing.
- Config discovery.
- Manager client transport.
- Request construction from catalog specs.
- Manual rendering orchestration.
- Output and exit-code behavior.

Implementation steps:

1. Check current status:

   ```sh
   git status --short
   ```

2. Verify the phase 6 starting state:

   ```sh
   test -d crates/sandbox-protocol
   test -d crates/sandbox-manager/src/server
   test -d crates/sandbox-daemon
   test -d crates/sandbox-runtime/operation
   test -d crates/sandbox-gateway-cli
   test -f crates/sandbox-gateway-cli/src/manual.rs
   test -f crates/sandbox-gateway-cli/src/request_builder.rs
   rg -n "Request|Response|OperationCatalog|OperationExecutionSpace" crates/sandbox-protocol/src
   rg -n "OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|invoke_sandbox_daemon" crates/sandbox-protocol/src crates/sandbox-manager/src crates/sandbox-gateway-cli/src
   ```

   The final `rg` command should return no matches.

3. Run and record baseline results:

   ```sh
   cargo fmt --check -p sandbox-protocol -p sandbox-manager -p sandbox-gateway-cli
   cargo check -p sandbox-protocol -p sandbox-manager -p sandbox-gateway-cli --tests
   cargo test -p sandbox-protocol -p sandbox-manager -p sandbox-gateway-cli
   ```

   If any command fails, record that it was pre-existing and continue only if
   the failure is unrelated to catalog/manual stabilization.

4. Move shared catalog JSON shape into `sandbox-protocol`.

   Add protocol-owned serializable/parsing helpers for the JSON document shape
   currently exchanged between manager and gateway:

   ```json
   {
     "operation_execution_space": "manager",
     "operations": [
       {
         "name": "create_sandbox",
         "family": "run",
         "summary": "Create a host-side sandbox record and runtime sandbox.",
         "args": [
           {
             "name": "sandbox_id",
             "kind": "string",
             "required": true,
             "help": "Sandbox id.",
             "default": null,
             "cli": {
               "flag": "--sandbox-id",
               "positional": null
             }
           }
         ],
         "cli": {
           "path": ["manager", "create_sandbox"],
           "usage": "sandbox manager create_sandbox --sandbox-id ID",
           "examples": ["sandbox manager create_sandbox --sandbox-id sbox-1"]
         }
       }
     ]
   }
   ```

   The exact Rust type names are flexible, but keep them protocol-owned. For
   example:

   ```rust
   pub struct OperationCatalogDocument { ... }
   pub struct OperationSpecDocument { ... }

   pub fn catalog_to_value(catalog: OperationCatalog) -> serde_json::Value;
   pub fn catalog_from_value(value: &serde_json::Value) -> Result<OperationCatalogDocument, CatalogDecodeError>;
   ```

   Do not make `sandbox-protocol` own concrete manager or runtime operation
   lists.

5. Replace manager-local catalog JSON building with protocol helpers.

   - Remove duplicate `catalog_value`, operation-execution-space-name, family-name, and
     arg-kind-name serialization code from `sandbox-manager` if the protocol
     helper now owns it.
   - `describe_manager_operations` must return a catalog with:

     ```json
     "operation_execution_space": "manager"
     ```

   - `describe_daemon_operations` must return a catalog with:

     ```json
     "operation_execution_space": "runtime"
     ```

   - The manager catalog must not include runtime operations such as
     `exec_command`, `poll_command`, `read_command_lines`,
     `write_command_stdin`, or `cancel_command`.
   - The runtime catalog must not include manager operations such as
     `create_sandbox`, `list_sandboxes`, or `destroy_sandbox`.

6. Replace gateway-local catalog document parsing with protocol helpers.

   - Remove duplicate catalog/spec/arg document structs from
     `sandbox-gateway-cli` if the protocol helper now owns them.
   - Keep request construction in `sandbox-gateway-cli`, but make it consume
     the protocol-owned catalog document.
   - Preserve the execution-space validation:

     ```text
     sandbox manager ...  -> catalog operation_execution_space must be manager
     sandbox runtime ...  -> catalog operation_execution_space must be runtime
     ```

7. Stabilize manual rendering.

   - Manual/help output must be generated from catalog/spec data.
   - Do not duplicate operation argument descriptions by hand in gateway.
   - Keep the two canonical sections:

     ```text
     Sandbox Manager Operations
     Sandbox Runtime Operations
     ```

   - Runtime manual text should say runtime, not daemon, even when the manager
     operation that fetches runtime specs still talks to a daemon internally.
   - `OperationFamily` may be shown as grouping metadata, but it must not be
     used as the manager-vs-runtime routing selector.
   - The only operation-execution-space selector in catalog output is
     `operation_execution_space`.

8. Add or tighten tests.

   In `sandbox-protocol`, add tests for:

   - `catalog_to_value` emits `operation_execution_space`.
   - `catalog_from_value` rejects missing or unknown `operation_execution_space`.
   - Serialized operation specs include `name`, `family`, `summary`, `args`,
     and `cli`.
   - No `owner`, `target`, `route`, `implementation_owner`, or
     `operation_target` field is emitted.

   In `sandbox-manager`, add or tighten tests for:

   - Manager catalog contains only manager operations.
   - Runtime catalog forwarding preserves `operation_execution_space = runtime`.
   - `describe_manager_operations` serializes CLI metadata through protocol
     helpers.
   - `describe_daemon_operations` uses the daemon client trait and serializes
     runtime catalog through protocol helpers.

   In `sandbox-gateway-cli`, add or tighten tests for:

   - Manual renders manager and runtime sections from catalog documents.
   - Manual output includes `sandbox manager ...` examples from catalog specs.
   - Manual output includes `sandbox runtime ...` examples from catalog specs.
   - Manager request construction rejects a runtime catalog.
   - Runtime request construction rejects a manager catalog.
   - Runtime `--sandbox-id` remains scope selection and is not added to
     `request.args`.

9. Update docs only where needed:

   - `docs/refactoring/sandbox-protocol.md`
   - `docs/refactoring/sandbox-gateway-cli.md`
   - `docs/refactoring/sandbox-manager.md`
   - `docs/refactoring/sandbox-implementation-guide.md`

   Keep docs aligned with:

   ```text
   Request
   Response
   OperationCatalog
   OperationExecutionSpace
   operation_execution_space
   manager vs runtime
   ```

   Do not rewrite older phase prompts unless they actively contradict the
   current target shape.

Non-goals:

- Do not implement phase 8 runtime support package renames.
- Do not rename `command`, `workspace`, `namespace-process`, `layerstack`,
  `overlay`, or `config`.
- Do not change command operation behavior.
- Do not add new runtime operations.
- Do not add direct gateway dependencies on `sandbox-manager`,
  `sandbox-daemon`, `sandbox-runtime`, `command`, `workspace`, `layerstack`,
  `overlay`, or `namespace-process`.
- Do not add `OperationRequest`, `OperationResponse`, `SandboxRequest`,
  `RoutedRequest`, `ManagerRequest`, `OperationTarget`, or
  `invoke_sandbox_daemon`.
- Do not introduce a second catalog selector beside `operation_execution_space`.

Acceptance checks:

```sh
rg -n "OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|invoke_sandbox_daemon" crates/sandbox-protocol/src crates/sandbox-manager/src crates/sandbox-gateway-cli/src
rg -n "\"owner\"|\"target\"|\"route\"|\"implementation_owner\"|\"operation_target\"" crates/sandbox-protocol/src crates/sandbox-manager/src crates/sandbox-gateway-cli/src
rg -n "operation_execution_space" crates/sandbox-protocol/src crates/sandbox-manager/src crates/sandbox-gateway-cli/src
rg -n "sandbox_manager::|sandbox_daemon::|sandbox_runtime::|command::|workspace::|layerstack::|overlay::|namespace_process::" crates/sandbox-gateway-cli/src
cargo fmt --check -p sandbox-protocol -p sandbox-manager -p sandbox-gateway-cli
cargo check -p sandbox-protocol -p sandbox-manager -p sandbox-gateway-cli --tests
cargo test -p sandbox-protocol -p sandbox-manager -p sandbox-gateway-cli
cargo clippy -p sandbox-protocol -p sandbox-manager -p sandbox-gateway-cli --all-targets --no-deps -- -D warnings
```

The first, second, and fourth `rg` commands should return no matches. The
`operation_execution_space` scan should show the single catalog selector.

Final response requirements:

- Summarize the catalog/manual contract changes.
- State whether phase 6 starting-state checks passed.
- State whether baseline checks had pre-existing failures.
- State final verification commands and results.
- Call out that agents and CLI choose `manager` or `runtime` first, then an
  operation from that catalog.
- Call out that `OperationFamily` is documentation grouping only.
- Do not claim phase 8 package renames were done.
```
