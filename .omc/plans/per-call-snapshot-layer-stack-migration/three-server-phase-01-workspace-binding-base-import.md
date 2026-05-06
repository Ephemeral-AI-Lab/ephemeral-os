# Phase 01 - Workspace Binding and Base Import

**Status:** draft implementation plan
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

## 1. Task Specification

Create the durable workspace binding owned by `layer-stack-server`, import the
assigned workspace once, and make guarded reads use the layer-stack active
manifest instead of the real `/testbed`.

Implementation scope:

```text
add WorkspaceBinding and workspace.json
add deterministic /testbed base import with import report
add import policy budgets for files, bytes, symlinks, and special files
bind workspace_root=/testbed to layer_stack_root outside /testbed
route guarded read_file to layer-stack read APIs
fail closed when binding or active manifest is missing
```

Out of scope:

```text
no OCC mutation routing
no command execution mount namespace
no Git or gitignore classification inside layer-stack
no recovery reimport/rebase API yet
```

Exit condition:

```text
setup can bind and import /testbed into manifest version 1, read_file can read
seeded content from layer-stack, and layer-stack import has no Git-aware policy.
```

## 2. Main Data Objects

```text
WorkspaceBinding
  workspace_root: /testbed
  layer_stack_root: /tmp/eos-sandbox-runtime/layer-stack
  active_manifest_version
  active_root_hash
  base_import_manifest_version
  base_import_root_hash
  import_report

ImportPolicy
  include/exclude rules
  max_files
  max_total_bytes
  max_single_file_bytes
  max_symlink_count
  max_symlink_depth

ImportReport
  included_paths
  skipped_paths
  oversized_paths
  special_file_rejections
  total_files
  total_bytes
  duration
```

## 3. File/Folder Structure Change

Target additions:

```text
backend/src/sandbox/layer_stack/
+-- workspace.py
+-- importer.py
+-- import_policy.py
+-- metrics.py

backend/src/sandbox/runtime/
+-- layer_stack_server.py
+-- layer_stack_handlers.py

backend/tests/unit_test/test_sandbox/test_layer_stack/
+-- test_workspace_import.py
+-- test_workspace_binding.py
```

Expected updates:

```text
backend/src/sandbox/control/ops/setup.py
backend/src/sandbox/api/tool/read.py
backend/src/sandbox/api/status/__init__.py
```

## 4. Workflow Demonstration

```text
status.create_sandbox(project_dir="/testbed")
  -> provider creates sandbox with real /testbed
  -> setup_after_create(...)
  -> start layer-stack-server
  -> layer-stack-server bind_workspace("/testbed", layer_stack_root)
  -> import_workspace_base()
       walk real /testbed by ImportPolicy
       write base layer L000001
       write manifest version 1
       write workspace.json with active root and import report
  -> read_file("src/a.py")
       layer-stack-server reads active manifest
       merged view returns content from layer-stack
```

Failure behavior:

```text
layer_stack_root inside /testbed        -> reject binding
existing manifest without reset         -> reject import
path escapes workspace through symlink  -> reject or report explicitly
oversize file beyond policy             -> fail or report explicitly
missing workspace binding on read       -> fail closed
```

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `WorkspaceBinding` | Names the durable binding between the assigned workspace and layer-stack storage. |
| `workspace_root` | The guarded workspace path, default `/testbed`. |
| `layer_stack_root` | Runtime storage location outside `workspace_root`. |
| `ImportPolicy` | Makes the first import deterministic and auditable. |
| `ImportReport` | Prevents silent omissions of generated, oversized, or special paths. |
| no Git names | Layer-stack stores bytes and manifests; OCC owns Git/gitignore policy later. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_workspace_import.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_workspace_binding.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_read.py -q
```

Required assertions:

- empty stack imports `/testbed` to manifest version 1
- import stores root hash and explicit import report
- repeated import fails unless explicit reset/recovery is requested
- read after import uses layer-stack content only
- import code contains no `.gitignore`, `check-ignore`, or tracked/untracked
  classification branches
