---
title: O(1) Shared Workspace Base For N Sandboxes
tags:
  - ephemeral-os
  - layerstack
  - performance
  - sandbox
status: implementation_plan
updated: 2026-06-30
---

# O(1) Shared Workspace Base For N Sandboxes

## Goal

Create `n` identical sandboxes from one workspace snapshot without copying the
base repo `n` times.

Target shape:

```text
workspace_root
  -> one host-side parallel single-pass copy+hash
  -> one immutable shared base layer
  -> read-only bind of that base into n sandboxes
  -> private per-sandbox writable overlay and layerstack state
```

Disk complexity changes from:

```text
current: O(n * base_repo_size)
target:  O(1 * base_repo_size) + O(n * sandbox_delta)
```

## Current Baseline

Current `create_sandbox` is already optimized, but still copies once per
sandbox:

- 32 Rayon workers for directory walk and file copy.
- 1 MiB reusable buffer per worker.
- Each file is copied and SHA-256 hashed in one read pass.
- The root hash is computed from manifest entries and per-file hashes, without
  rereading file bytes.

Measured on `ephemeral-agent` (`863M`), `n=5`, serial `sandbox-cli` creates:

```text
create_1: 4.128s
create_2: 2.301s
create_3: 2.019s
create_4: 2.019s
create_5: 2.007s
total:    13.489s

each container writable size: 918MB
5 containers writable total:  4.588GB
```

The warm timing looks good because source reads are cached, but every sandbox
still writes its own base copy.

## Final Filesystem Shape

Keep `/eos/layer-stack` as the layerstack control directory. Merge the base
view into it as a read-only `base/` bind. Keep the runtime overlay mount and
overlay scratch outside layerstack state.

```text
/eos/layer-stack/
  base/                       # read-only bind mount, shared by n sandboxes
    B000001-base/             # full copied workspace tree
  manifest.json               # per-sandbox writable active manifest
  workspace.json              # per-sandbox writable workspace binding
  layers/                     # per-sandbox published delta layers
  staging/                    # per-sandbox temp publish directories
  .layer-metadata/            # per-sandbox local layer metadata

/eos/workspace/
  overlay_mount/              # overlayfs mountpoint exposed as /workspace
  upper/                      # per-sandbox live uncommitted writes
  work/                       # per-sandbox overlayfs workdir
```

`overlay_mount/` is a mountpoint, not another base copy. It should not be
space-costly by itself. The space-costly per-sandbox paths are `upper/` for
live writes and `layers/` for published sandbox deltas.

## Manifest Shape

Use relative layer paths so the current layer path validation can stay simple.
The shared base is visible at `base/B000001-base` inside the sandbox.

Initial per-sandbox manifest:

```json
{
  "schema_version": 1,
  "version": 1,
  "layers": [
    {
      "layer_id": "B000001-base",
      "path": "base/B000001-base"
    }
  ]
}
```

After the sandbox publishes a local delta layer:

```json
{
  "schema_version": 1,
  "version": 2,
  "layers": [
    {
      "layer_id": "L000002-xxxxxxxx",
      "path": "layers/L000002-xxxxxxxx"
    },
    {
      "layer_id": "B000001-base",
      "path": "base/B000001-base"
    }
  ]
}
```

Only `base/` is shared. `manifest.json`, `workspace.json`, `layers/`,
`staging/`, and `.layer-metadata/` stay private to each sandbox.

## Host Base Builder

Reuse the existing layerstack copy algorithm. Do not use `rsync` plus
`tar | shasum` in the production path.

Required behavior:

1. Build in a host temp directory.
2. Walk and copy with the existing parallel worker pool.
3. Hash file bytes during the copy loop.
4. Compute the root hash from manifest entries.
5. Publish atomically to a shared base cache keyed by the layerstack root hash.
6. If another process already published the same hash, discard the temp build
   and reuse the existing base.

The published cache entry should contain the directory that gets mounted as
`/eos/layer-stack/base`, including `B000001-base/` under it:

```text
host shared cache/<root_hash>/base/
  B000001-base/
    ...
```

That host path is mounted read-only into each sandbox:

```text
host shared cache/<root_hash>/base
  -> /eos/layer-stack/base:ro
```

The previous raw O(1) prototype was slower because it used a two-pass shell
shape:

```text
rsync copy:      7.593s
tar | shasum:    9.981s
total build:    17.660s
```

The production builder should be close to the current in-sandbox copy+hash cost
because it uses the same single-pass copy+hash algorithm, just on the host and
only once per `root_hash`.

## Manager Flow

Proposed CLI:

```sh
bin/sandbox-cli --progress manager create_sandbox \
  --image ubuntu:24.04 \
  --workspace-bind-root /path/to/workspace \
  --count 5
```

`--count N` means build or reuse one shared base and create `N` sandboxes from
it. The single-sandbox path can stay unchanged until the shared path is proven.

Manager flow:

1. Build or reuse the shared base cache for `workspace_root`.
2. Create `count` stopped containers with no host workspace bind.
3. Mount that sandbox's private `/eos/layer-stack` writable.
4. Mount the shared base cache read-only at `/eos/layer-stack/base`.
5. Seed `manifest.json` and `workspace.json` in the private stack root.
6. Start all daemons.
7. Daemon sees the preseeded base binding and skips `ensure_workspace_base`
   copying.
8. Runtime workspace is materialized from layerstack at
   `/eos/workspace/overlay_mount`, then exposed as `/workspace`.

Expected progress logs:

```text
shared workspace base building <workspace_root>
shared workspace base built root_hash=<hash> bytes=<bytes>
creating runtime sandboxes count=5
workspace base mounted from shared cache root_hash=<hash>
sandbox is ready
```

No sandbox in this path should log `copying workspace /workspace into base
layer`, because the copy already happened once before the containers start.

## Safety Rules

- The shared base cache is immutable after publish.
- `/eos/layer-stack/base` is read-only in every sandbox.
- A sandbox can publish new layers only under its private `layers/` directory.
- Publish temp files go only under the sandbox's private `staging/` directory.
- Local metadata goes only under the sandbox's private `.layer-metadata/`
  directory.
- Destroying one sandbox must not remove a shared base that another sandbox
  references.
- GC may remove a base only when no live sandbox record references its
  `root_hash`.
- Host workspace changes after base build do not affect existing sandboxes.
- User `exec_command` tests must validate the materialized `/workspace` view,
  not `/eos` internals.
- The manager/container-side base mount at
  `/eos/layer-stack/base/B000001-base` must be read-only.

## Live E2E Proofs

### Baseline: Current O(n) Create

Use the real manager path:

```sh
bin/start-sandbox-docker-gateway --rebuild-binary

workspace=/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-agent
for i in 1 2 3 4 5; do
  /usr/bin/time -p \
    bin/sandbox-cli --progress manager create_sandbox \
      --image ubuntu:24.04 \
      --workspace-bind-root "$workspace"
done

docker ps -as --filter label=eos.sandbox_id \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Size}}\t{{.Status}}'
docker system df
```

Pass condition:

```text
5 sandboxes are ready
progress shows 5 workspace base builds
container writable size grows roughly n * base size
```

Reference result from 2026-06-30:

```text
n=5 total create time: 13.489s
container writable:    4.588GB
```

### Target: Shared Base O(1) Create

Use the new shared-base mode:

```sh
bin/start-sandbox-docker-gateway --rebuild-binary

workspace=/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-agent
/usr/bin/time -p \
  bin/sandbox-cli --progress manager create_sandbox \
    --image ubuntu:24.04 \
    --workspace-bind-root "$workspace" \
    --count 5 \
    > /tmp/eos-o1-create.json

docker ps -as --filter label=eos.sandbox_id \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Size}}\t{{.Status}}'
docker system df
du -sh "$EOS_SHARED_BASE_CACHE"
```

Pass condition:

```text
5 sandboxes are ready
progress shows 1 shared base build and 5 shared base mounts
no final sandbox logs "copying workspace /workspace into base layer"
shared base size is about one workspace base
container writable size stays near per-sandbox metadata/delta size
total disk is O(1 base) + O(n tiny)
```

Initial performance gate:

```text
ephemeral-os workspace_root cold shared-base create: 2-4s
target total time <= 60% of same-run O(n) baseline
target container writable <= 10% of same-run O(n) baseline
shared base cache <= 1.2x workspace base size
```

The gate is relative because Docker Desktop cache warmth changes absolute
seconds. The `ephemeral-os` 2-4s number is a target for the optimized
host-side single-pass copy+hash path, not a result from the raw `rsync` smoke
test.

### Target: Exec And Workspace Materialization E2E

The shared-base benchmark must also prove that command execution works and that
the first command is not paying a hidden workspace copy. Test the user-visible
`/workspace` materialization, not `/eos` internals.

Do not assert `test ! -e /eos`: the current runner masks `/eos` with an empty
read-only tmpfs, so the mountpoint exists even though layerstack descendants are
not exposed.

```sh
echo base-probe > "$workspace/.eos-base-probe"

python3 - <<'PY' >/tmp/eos-o1-ids.txt
import json
data = json.load(open("/tmp/eos-o1-create.json"))
for sandbox in data["sandboxes"]:
    print(sandbox["id"])
PY

while read -r sandbox_id; do
  echo "sandbox=$sandbox_id"

  /usr/bin/time -p \
    bin/sandbox-cli runtime --sandbox-id "$sandbox_id" exec_command \
      'pwd; test -d /workspace; grep -q base-probe /workspace/.eos-base-probe; ls /workspace >/dev/null'
done < /tmp/eos-o1-ids.txt
```

Pass condition:

```text
pwd/test/grep/ls command succeeds for every sandbox
/workspace contains the base workspace content
exec wall time is near normal runtime exec time, not near base-copy time
container writable size does not jump by one workspace base after exec
```

Reference gate:

```text
first exec p95 <= same-run O(n) first-exec p95 + 500ms
post-exec container writable <= pre-exec writable + 20MB per sandbox
```

This catches an implementation that defers the copy until first `exec_command`.

### Target: Exec Layer-Depth Benchmark

Add one opt-in live E2E benchmark:

```text
cli-operation-e2e-live-test/runtime/command/test_exec_command_layer_depth_benchmark.py
```

Purpose: prove the shared base bind does not add measurable command latency as
layer depth grows.

Default knobs:

```text
E2E_IMAGE=ephemeral-agent
E2E_EXEC_BENCH_DEPTHS=1,10,50,100
E2E_EXEC_BENCH_SAMPLES=5
```

Test flow:

1. Create one sandbox from a small temp workspace.
2. Grow layer depth with tiny one-shot write commands. One-shot
   `exec_command` publishes; persistent workspace sessions do not.
3. At each target depth, time repeated one-shot read commands such as
   `test -f README.md && printf ok`.
4. Log one JSON row per depth:

```json
{"depth": 50, "samples": 5, "min_ms": 0, "p50_ms": 0, "p95_ms": 0, "max_ms": 0}
```

Run both shapes on the same host, image, workspace, depth list, and sample
count:

```sh
bin/start-sandbox-docker-gateway --rebuild-binary

E2E_IMAGE=ephemeral-agent \
E2E_EXEC_BENCH_DEPTHS=1,10,50,100 \
E2E_EXEC_BENCH_SAMPLES=5 \
pytest -s \
  cli-operation-e2e-live-test/runtime/command/test_exec_command_layer_depth_benchmark.py
```

Pass condition:

```text
all commands complete with status=ok and exit_code=0
shared-base p95 at each depth <= same-run O(n) p95 + max(500ms, 15%)
shared-base writable size does not grow by one base copy during the benchmark
```

### Safety E2E

After creating two shared-base sandboxes from the same `root_hash`, prove
`/workspace` materialization is sandbox-local after writes:

```sh
bin/sandbox-cli runtime --sandbox-id "$sandbox_a" exec_command \
  'printf sandbox-a > /workspace/.eos-sandbox-local'

bin/sandbox-cli runtime --sandbox-id "$sandbox_a" exec_command \
  'grep -q sandbox-a /workspace/.eos-sandbox-local'

bin_sandbox_b_result=0
bin/sandbox-cli runtime --sandbox-id "$sandbox_b" exec_command \
  'test ! -e /workspace/.eos-sandbox-local' || bin_sandbox_b_result=$?
test "$bin_sandbox_b_result" -eq 0
```

Expected:

```text
sandbox A sees its own published workspace delta
sandbox B does not see sandbox A's workspace delta
```

Prove the internal base mount is read-only through manager/container-side
inspection, not through `exec_command`. Add or extend a manager diagnostic that
reports the shared base source, target, `root_hash`, and readonly flag; the
E2E asserts `target=/eos/layer-stack/base` and `readonly=true`.

Then mutate a normal workspace file in sandbox A and prove sandbox B does not
see it unless the change is explicitly published through the layerstack flow.

Also mutate the original host `workspace_root` after create and prove neither
sandbox changes, because the source workspace is not mounted into the running
sandboxes.

```sh
probe="$workspace/.eos-host-mutation-probe"
echo host-only > "$probe"

bin/sandbox-cli runtime --sandbox-id "$sandbox_a" exec_command \
  'test ! -e /workspace/.eos-host-mutation-probe'
bin/sandbox-cli runtime --sandbox-id "$sandbox_b" exec_command \
  'test ! -e /workspace/.eos-host-mutation-probe'

rm -f "$probe"
```

## Raw Docker Storage Smoke Test

This is not a full EOS test, but it proves the disk shape:

```sh
tmp=$(mktemp -d /tmp/eos-o1-bench.XXXXXX)
base="$tmp/layer-stack/base"
mkdir -p "$base"
rsync -a /Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-agent/ \
  "$base/B000001-base"/

for i in 1 2 3 4 5; do
  docker run -d \
    --label eos.o1bench=true \
    --mount type=bind,src="$base",dst=/eos/layer-stack/base,readonly \
    ubuntu:24.04 sleep 300
done

docker ps -as --filter label=eos.o1bench=true \
  --format 'table {{.ID}}\t{{.Names}}\t{{.Size}}\t{{.Status}}'
```

Reference result from 2026-06-30:

```text
host shared base:       863M
5 containers writable:  180.2kB total
each container:         12.3kB writable
readonly check:         ro for all 5
```

This smoke test proves O(1) storage only. It does not prove EOS daemon startup,
layerstack metadata seeding, overlay mount setup, or runtime publish safety.

## Implementation Notes

Minimal code path:

1. Add a manager-side shared base cache service.
2. Expose a layerstack builder API that builds `base/B000001-base` into a temp
   cache entry and returns `{root_hash, base_mount_path, metadata}`.
3. Extend Docker create to accept an optional read-only bind for
   `/eos/layer-stack/base`.
4. Seed per-sandbox `manifest.json` and `workspace.json` before daemon start.
5. Extend daemon startup to skip base copy when valid preseeded metadata exists.
6. Add `--count` only after the single-sandbox shared-base internals work.

Do not add a custom content store yet. The first useful version is one
immutable base directory, a read-only bind, a tiny per-sandbox manifest, and
normal private publish directories.

## Open Risk

If the shared base lives on a macOS host path, disk is O(1), but runtime reads
may be slower than Docker-native storage. Keep this as a measured tradeoff:
start with the simple bind design, then benchmark read-heavy workloads before
adding a Docker-native cache path.
