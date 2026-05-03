# Stack Overlay Experiment

Standalone prototype for the per-call overlay layer stack. This code is
experimental and intentionally separate from production `backend/src`.

The prototype validates:

- bounded depth-100 manifests,
- relative `lowerdir` mount option generation,
- lease/refcount retention for old shell snapshots,
- squash to a compact checkpoint,
- OCC-style same-path conflict rejection,
- non-conflicting stale-shell writes accepted by default.

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

Run a live Linux mount probe inside a namespace-capable sandbox:

```bash
python -m stack_overlay.experiments mount-probe --depth 100 --iterations 1000
```

The mount probe uses direct `mount(2)` by default because the current Daytona
image's util-linux `mount(8)` fails on deep overlay lowerdir stacks that the
kernel accepts. Use `--method mount8` only as a negative-control comparison.
Use relative lowerdirs for Daytona-like environments. Absolute long paths can
still hit option-string limits earlier than the depth-100 target.
