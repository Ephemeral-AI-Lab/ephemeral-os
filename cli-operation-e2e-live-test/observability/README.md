# observability (placeholder)

Observability is **not implemented this round**. `test_observability.py` is a
skipped test so the suite stays runnable end to end.

## Why it's empty for now

Observability is best verified *through* runtime activity: create state with the
runtime/manager families, then assert that observability reports it. Wiring the
assertions before there is runtime state to observe would test nothing useful.

## Where it plugs in later

Observability operations route to a sandbox's daemon by id and return
structured JSON — the same no-log-scraping contract the rest of the suite uses:

- `sandbox-cli observability snapshot --sandbox-id <id>` — live sandbox state:
  workspaces, layer counts, in-flight executions, latest resource samples.
- `sandbox-cli manager get_observability_tree [--sandbox-id <id>]` — manager-side
  aggregate across ready sandboxes (already wrapped as
  `get_observability_tree` in `manager/management/helpers.py`).

Planned shape, mirroring the per-family layout used by `manager/` and `runtime/`:

```
observability/
└── snapshot/
    ├── __init__.py
    ├── helpers.py        # snapshot(sandbox_id), etc.
    └── test_snapshot.py
```

Assertion helpers (e.g. `assert_has_execution` / `assert_no_execution`, working
on the parsed snapshot dict) will live alongside the family or in `core/`, and be
used to *wrap* the runtime tests, for example:

1. `exec_command` a long-lived command inside a session.
2. `observability snapshot --sandbox-id <id>` shows that command under the
   workspace's `active_namespace_executions`.
3. After the command finishes / the session is destroyed, the snapshot no longer
   lists it.

That replaces any temptation to grep daemon logs with structured state checks.
