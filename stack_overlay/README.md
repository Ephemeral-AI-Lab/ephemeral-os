# Stack Overlay Experiment

Standalone prototype for the per-call overlay layer stack. This code is
experimental and intentionally separate from production `backend/src`.

The prototype validates:

- bounded depth-100 manifests,
- relative `lowerdir` mount option generation,
- lease/refcount retention for old shell snapshots,
- squash to a compact checkpoint,
- OCC-style same-path conflict rejection,
- non-conflicting stale-shell writes accepted by default,
- upperdir capture into OCC for large files and package-install-shaped trees.

Run local model tests:

```bash
uv run pytest stack_overlay/tests -q
```

Run the no-privilege simulation:

```bash
uv run python -m stack_overlay.experiments simulate
```

Run the synthetic E4-E14 suite:

```bash
uv run python -m stack_overlay.experiments suite --profile quick
```

The suite emits in-flight progress logs on stderr and writes the final JSON
report on stdout. Use `--quiet` to suppress progress logs. Profiles:

- `quick`: short debug run for local iteration.
- `standard`: larger synthetic run.
- `doc-count`: uses the documented iteration counts where practical; expect it
  to be slow because this prototype squashes synchronously.

Reference local run from 2026-05-04:

```bash
uv run python -m stack_overlay.experiments suite --profile standard
```

Result: `11 passed` in `79.74s`.

| Workload | Result |
| --- | --- |
| E8 shell-op proxy | p50 `10.945ms`, p99 `204.046ms` against a `250ms` p99 budget |
| Large upperdir file -> OCC | `2 MiB` file, capture `0.816ms`, OCC merge `1.781ms` |
| Large JSON diff -> OCC | `100` changes in `18.516ms`; `1,000` in `202.454ms`; `5,000` in `1.101s` |
| npm-install-shaped upperdir -> OCC | `1,601` changes, capture `95.089ms`, OCC merge `377.309ms` |
| pip-target-install-shaped upperdir -> OCC | `1,440` changes, capture `83.310ms`, OCC merge `277.595ms` |

The install workloads are deterministic synthetic upperdirs shaped like package
manager output. They do not run networked `npm install` or `pip install`.

Run a live Linux mount probe inside a namespace-capable sandbox:

```bash
python -m stack_overlay.experiments mount-probe --depth 100 --iterations 1000
```

The mount probe uses direct `mount(2)` by default because the current Daytona
image's util-linux `mount(8)` fails on deep overlay lowerdir stacks that the
kernel accepts. Use `--method mount8` only as a negative-control comparison.
Use relative lowerdirs for Daytona-like environments. Absolute long paths can
still hit option-string limits earlier than the depth-100 target.
