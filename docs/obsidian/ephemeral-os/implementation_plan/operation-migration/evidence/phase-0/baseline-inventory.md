# Phase 0 baseline inventory

Execution baseline: `main` at
`df37fe31a229bdfaf029ac994599f088efabd8b3` on 2026-07-10. The production
source tree was clean at measurement time; unrelated Obsidian workspace
state and migration-document edits were present but do not affect the
measurements.

The specification's allocation tables use `cc5f9974e` as their reference
revision. Both that reference and the current execution baseline are shown
below so the intervening configuration work is not silently folded into the
planning reference.

## Cargo metadata

Snapshot command:

```bash
cargo metadata --format-version 1 | jq . > \
  docs/obsidian/ephemeral-os/implementation_plan/operation-migration/evidence/phase-0/cargo-metadata.json
```

Integrity check:

```bash
cargo metadata --format-version 1 | jq . | shasum -a 256
shasum -a 256 \
  docs/obsidian/ephemeral-os/implementation_plan/operation-migration/evidence/phase-0/cargo-metadata.json
```

Both hashes were:

```text
b34d735809c750687438cf13feae86c38e5bb30d33250782c5cbc111d9a720c8
```

Metadata summary:

- 20 workspace packages: 19 under `crates/` plus `xtask`.
- 8 binary targets: `sandbox-console`, `sandbox-daemon`, `sandbox-gateway`,
  `sandbox-manager-cli`, `sandbox-observability-cli`,
  `sandbox-runtime-cli`, `sandbox-mcp`, and `xtask`.
- Only `sandbox-cli` declares features: `default`, `manager`, `runtime`, and
  `observability`.
- 59 path dependency edges: 51 normal, 8 dev, 0 build; 3 normal edges are
  optional.

The summary was derived from the committed JSON with:

```bash
jq '
  . as $m
  | ([ $m.packages[]
       | select(.id as $id | ($m.workspace_members | index($id)))
       | .dependencies[]
       | select(.path != null) ]) as $edges
  | {
      workspace_packages: ($m.workspace_members | length),
      binary_targets: ([
        $m.workspace_members[] as $id
        | $m.packages[] | select(.id == $id)
        | .targets[] | select(.kind | index("bin")) | .name
      ] | sort),
      feature_bearing_packages: ([
        $m.workspace_members[] as $id
        | $m.packages[]
        | select(.id == $id and (.features | length > 0))
        | {name, features}
      ]),
      workspace_dependency_edges: ($edges | length),
      normal_edges: ([$edges[] | select(.kind == null)] | length),
      dev_edges: ([$edges[] | select(.kind == "dev")] | length),
      build_edges: ([$edges[] | select(.kind == "build")] | length),
      optional_edges: ([$edges[] | select(.optional)] | length)
    }' cargo-metadata.json
```

## Production Rust LOC

Counting rule: physical lines of tracked `src/**/*.rs` plus a crate-root
`build.rs`, including comments and blanks and excluding tests, fixtures,
manifests, and generated files. No measured package currently has a
crate-root `build.rs`.

Command pattern, run once per package manifest directory and revision:

```bash
git grep -n -e '^' REV -- ":(glob)PACKAGE_DIR/src/**/*.rs" | wc -l
```

| Source owner | Path | `cc5f9974e` | Execution baseline | Delta |
| --- | --- | ---: | ---: | ---: |
| `sandbox-runtime` | `crates/sandbox-runtime/operation` | 6,024 | 6,024 | 0 |
| `sandbox-observability` | `crates/sandbox-observability` | 1,582 | 1,582 | 0 |
| `sandbox-protocol` | `crates/sandbox-protocol` | 1,203 | 1,203 | 0 |
| `sandbox-runtime-layerstack` | `crates/sandbox-runtime/layerstack` | 6,146 | 6,146 | 0 |
| `sandbox-runtime-namespace-execution` | `crates/sandbox-runtime/namespace-execution` | 2,416 | 2,416 | 0 |
| `sandbox-runtime-namespace-process` | `crates/sandbox-runtime/namespace-process` | 3,460 | 3,460 | 0 |
| `sandbox-runtime-overlay` | `crates/sandbox-runtime/overlay` | 489 | 489 | 0 |
| `sandbox-runtime-operations` | `crates/sandbox-operations/runtime` | 441 | 441 | 0 |
| `sandbox-runtime-workspace` | `crates/sandbox-runtime/workspace` | 3,678 | 3,678 | 0 |
| `sandbox-config` | `crates/sandbox-config` | 1,501 | 1,744 | +243 |
| `sandbox-daemon` | `crates/sandbox-daemon` | 3,224 | 3,224 | 0 |
| `sandbox-manager` | `crates/sandbox-manager` | 3,266 | 3,308 | +42 |
| `sandbox-manager-operations` | `crates/sandbox-operations/manager` | 244 | 244 | 0 |
| `sandbox-observability-operations` | `crates/sandbox-operations/observability` | 278 | 278 | 0 |
| `sandbox-cli` | `crates/sandbox-cli` | 1,305 | 1,305 | 0 |
| `sandbox-mcp` | `crates/sandbox-mcp` | 414 | 414 | 0 |
| `sandbox-console` | `crates/sandbox-console` | 1,160 | 1,245 | +85 |
| `sandbox-gateway` | `crates/sandbox-gateway` | 572 | 630 | +58 |
| `sandbox-provider-docker` | `crates/sandbox-provider-docker` | 1,988 | 1,995 | +7 |
| **Crates total** | | **39,391** | **39,826** | **+435** |
| `xtask` | `xtask` | 1,439 | 1,439 | 0 |

The spec allocation reference is therefore 39,391 production lines under
`crates/`; the actual Phase 0 execution baseline is 39,826. The 435-line
difference is entirely accounted for by intervening committed configuration
work in `sandbox-config`, manager, console, gateway, and the Docker provider.

For context outside the Cargo package table, the maintained frontend has 59
tracked TS/TSX/CSS/HTML files and 6,424 lines, and the provider example has
one tracked Rust file and 82 lines. The current non-test total is 46,332
lines excluding `xtask`, or 47,771 including it.

## E2E and generated-content inventory

Commands:

```bash
git ls-files 'cli-operation-e2e-live-test/**' | wc -l
git ls-files 'cli-operation-e2e-live-test/**' | rg -c '/test-reports/'
git ls-files 'cli-operation-e2e-live-test/**' | rg -v '/test-reports/' | wc -l
git archive --format=tar HEAD \
  ':(glob)cli-operation-e2e-live-test/**/test-reports/**' \
  | tar -xOf - | wc -l
git ls-files --stage 'web/console/*.tsbuildinfo'
```

Results:

- 8,064 files are tracked under the old E2E root.
- 7,977 are generated reports containing 4,274,972 lines.
- 87 are maintained files containing 19,846 lines: 70 Python files/18,571
  lines, 12 Markdown files/1,241 lines, one INI/28 lines, one text file/2
  lines, `.gitignore`/4 lines, and two empty `.gitkeep` files.
- The generated report split is 467 files/164,731 lines for manager export,
  6,435/4,086,513 for manager squash, 445/9,186 for runtime command, and
  630/14,542 for runtime workspace-session.
- Two TypeScript build-info files are tracked:
  `web/console/tsconfig.app.tsbuildinfo` (2,037 bytes, blob
  `d9e8d690bd5f62008b53d49ebe9cf4834ad1f997`) and
  `web/console/tsconfig.node.tsbuildinfo` (47 bytes, blob
  `07317691a412978bf25e756d05d0e0b95f4f825b`).
- No durable `*.tsbuildinfo` ignore rule exists at the baseline.

These counts define the destructive Phase 0 purge/move input. They were
captured before any report deletion, build-info deletion, or E2E relocation.
