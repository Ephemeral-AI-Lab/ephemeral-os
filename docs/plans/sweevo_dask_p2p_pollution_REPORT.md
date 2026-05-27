# SWE-EVO `dask__dask_2023.3.2_2023.4.0` — 11 false-positive P2P failures

## 2026-05-27 implementation correction

The pipeline fix landed in
[`task_center_runner.benchmarks.sweevo.eval._run_test_set_outcome`](../../backend/src/task_center_runner/benchmarks/sweevo/eval.py).
The decisive production-path failure was not solved by P2P allow-listing,
xdist, or splitting the suite. The evaluator was running `pytest.main()` from a
`python - <<'PY'` heredoc. Dask's process-scheduler tests use Python
`multiprocessing`; spawned children tried to reload the parent module from
`/testbed/<stdin>` and crashed with `FileNotFoundError`, surfacing as
`BrokenProcessPool` across the same 11 P2P IDs.

The runner now stages two files inside the sandbox:

1. `/tmp/sweevo_ids_*.json` for the large test-id list.
2. `/tmp/sweevo_pytest_runner_*.py` for a real `pytest.main()` runner guarded by
   `if __name__ == "__main__"`.

That preserves the argv-overflow fix while making multiprocessing spawn safe.
No per-instance Dask P2P allow-list is needed. Full verification:

```text
uv run python backend/scripts/verify_sweevo_migration.py --mode full
F2P expected 0 passed, got 0/61      OK
P2P expected 6246 passed, got 6246/6246  OK
resolved=False fix_rate=0.0000
```

## 2026-05-27 root cause for the 8 unfindable P2P IDs

The 8 unfindable P2P IDs are already malformed in the upstream
`Fsoft-AIC/SWE-EVO` dataset row. The local loader receives `PASS_TO_PASS` as a
list of 6246 strings, and these 8 exact malformed strings are present in the raw
row before `_parse_test_list` runs.

The corruption source is the public SWE-EVO dataset-generation path:

1. `SWE-bench/generate_f2p_p2p.py` runs the pre and gold test logs, parses them
   with `get_logs_eval(...)`, and writes `inst["PASS_TO_PASS"] = p2p`.
2. For `dask/dask`, `MAP_REPO_TO_PARSER` selects `parse_log_dask`, which aliases
   to `parse_log_pytest`.
3. `parse_log_pytest` handles each pytest summary line with `test_case =
   line.split()` and stores `test_status_map[test_case[1]]`.

That whitespace split is not safe for pytest node IDs. Dask has parametrized
node IDs whose parameter display contains spaces, tabs, newlines, or tuple
commas. The generator therefore wrote prefixes instead of valid node IDs.

Examples reproduced in the live `sweevo-dask__dask_2023.3.2_2023.4.0`
container:

| Real pytest summary line starts with | Parser key written to dataset |
|---|---|
| `PASSED ...::test_skiprows_as_list[read_csv-read_csv-files0-str, int, int\n]` | `...::test_skiprows_as_list[read_csv-read_csv-files0-str,` |
| `PASSED ...::test_str_split_no_warning[other index]` | `...::test_str_split_no_warning[other` |
| `PASSED ...::test_to_numeric_on_scalars[5 ]` | `...::test_to_numeric_on_scalars[5` |
| `PASSED ...::test_emscripten_default_scheduler['dask.array', 'Array', 'sync', True]` | `...::test_emscripten_default_scheduler['dask.array',` |

The `test_emscripten_default_scheduler[...]` case also demonstrates a second
effect: several real parametrized cases collapse to the same truncated key, so
the dataset lost multiplicity as well as validity.

**Current TL;DR** — The production evaluator's 11 P2P false positives were
caused by running `pytest.main()` from a stdin heredoc, which breaks Dask tests
that spawn multiprocessing children. Staging a real Python runner file fixes
the single-process pipeline: F2P remains `0/61`, while P2P reports `6246/6246`.
The older investigation below is retained as provenance but is superseded by
the implementation correction above.

---

## 1. Affected tests

```
dask/dataframe/io/tests/test_csv.py::test_to_csv_errors_using_multiple_scheduler_args
dask/dataframe/io/tests/test_csv.py::test_to_csv_keeps_all_non_scheduler_compute_kwargs
dask/dataframe/io/tests/test_csv.py::test_to_csv_warns_using_scheduler_argument
dask/dataframe/io/tests/test_csv.py::test_to_csv_with_get
dask/dataframe/io/tests/test_io.py::test_to_bag
dask/dataframe/io/tests/test_json.py::test_to_json_with_get
dask/dataframe/io/tests/test_parquet.py::test_to_parquet_lazy[fastparquet-processes]
dask/dataframe/io/tests/test_parquet.py::test_to_parquet_lazy[pyarrow-processes]
dask/dataframe/io/tests/test_parquet.py::test_to_parquet_with_get[fastparquet]
dask/dataframe/io/tests/test_parquet.py::test_to_parquet_with_get[pyarrow]
dask/tests/test_base.py::test_persist_array_bag
```

## 2. Evidence the migration is not at fault

| Probe | Result |
|---|---|
| Run all 11 in a fresh pytest invocation (`pytest <11 ids>`) | All 11 **PASSED** in 20.5 s |
| Run any one of the 11 in isolation | PASSED |
| Run the full 6246-id suite via `pytest -n0` in one process | Exactly these 11 FAIL |
| Substitute `-n auto` (xdist, one process per worker) | All 11 expected to PASS (matches dask upstream CI) |

These were reproduced inside the live container
`sweevo-dask__dask_2023.3.2_2023.4.0` at base commit `0cbc46ac89b6`
with the SWE-EVO `test_patch` applied — exactly the state
`SweevoLifecycle.after_run` produces.

## 3. Common factor across the 11

Every failing test exercises the **`processes` scheduler** in one of three
shapes:

- Direct parametrize: `[*-processes]` on `test_to_parquet_lazy`
- Direct argument: tests with `with_get`, `scheduler_args`,
  `multiple_scheduler_args`, `non_scheduler_compute_kwargs` —
  all of which build a compute call that targets the `processes`
  scheduler
- Indirect: `test_to_bag`, `test_persist_array_bag` use `to_bag()` /
  `persist(..., scheduler="processes")` under the hood for the
  default-scheduler path

The dask `processes` scheduler instantiates `multiprocessing.Pool`, which
on Linux uses `fork()` as the default start method.

## 4. Root cause

**Fork-unsafety after a multi-threaded library has been loaded into the
parent process.** Once an earlier test has imported `pyarrow`,
`fastparquet`, `numpy` (built against OpenMP), `pandas`, or any other
library that spawns background threads at import time, the parent
Python process has live non-main threads. POSIX `fork()` only duplicates
the calling thread, so the child wakes up in a state where:

- Locks held by those threads are forever-locked
- TLS for those libraries is half-initialized
- atexit / signal handlers may double-fire

Common manifestations in dask: the child worker hangs on a pickled
arrow/parquet open, or fails to re-init `fastparquet`'s internal
caches, or the parent's `Pool.apply_async` raises a serialization /
process-died error.

Upstream dask hits the same hazard and works around it by running the
suite with `pytest -n auto` (pytest-xdist gives every worker its own
fresh Python process where the `processes` scheduler can `fork()` from
a clean slate). The dask upstream CI config does this. The SWE-EVO
dataset's `test_cmds` does not.

## 5. Why the migration surfaced it now

The SWE-EVO benchmark row pins:

```
test_cmds = "pytest --continue-on-collection-errors -n0 -rA --color=no"
```

That `-n0` forces single-process execution and triggers the fork hazard.

The legacy benchmark code path embedded all `test_ids` inline into the
docker-exec heredoc; with 6246 P2P IDs the bash argv overflowed
(`exec /bin/bash: argument list too long`, exit 255) **before pytest ever
ran**. The legacy code treated this as "0 passed of 6246" — i.e. all
6246 reported as broken — so the 11 fork-pollution failures were
invisible inside that 6246-wide blast radius.

The migration's
[`task_center_runner.benchmarks.sweevo.eval._run_test_set`](../../backend/src/task_center_runner/benchmarks/sweevo/eval.py)
fixed the argv overflow by:

1. Staging test IDs as a JSON file via the chunked-base64 helper.
2. Running `pytest.main()` in-process so the IDs never traverse a
   second `execve` boundary.
3. Retrying with unfindable IDs dropped (8 IDs in the dask dataset are
   parametrize-string truncations from dataset generation).

Now pytest actually completes; the 11 fork failures become visible.
They were always there.

## 6. Suggested follow-ups (out of scope for the migration)

Pick exactly one. None of them belong in
`task_center_runner.benchmarks.sweevo` — they're scoring / dataset
concerns.

1. **Patch the dataset's `test_cmds`** to `-n auto` (or `--forked`) for
   instances whose P2P set includes multiprocessing tests. Requires a
   small static check on test IDs that route through `processes`.
2. **Per-instance allow-list at scoring time** — record the 11
   environment-dependent failures as known-flaky against this base
   commit; the scorer treats them as "pass" rather than "broken P2P".
3. **Drop the 11 from the dataset's P2P field** — they're not stable
   regression signals when the suite is forced into single-process
   execution. Lowest-effort, but removes signal entirely.
4. **Pre-import isolation** — in `evaluate_sweevo_result`, split the
   P2P set into "fork-touching" and "safe" buckets and run each bucket
   in its own `pytest.main()` invocation. Costs ~30 s extra per run
   because each bucket re-imports dask, but keeps the dataset
   unchanged.

Option 2 has the best evidence-to-cost ratio: the 11 failing tests are
a fixed set per instance, easy to verify on a per-instance basis, and
the allow-list lives in benchmark scoring code where it belongs.

## 7. Reproduction recipe

```bash
# Container must already exist (created by a prior verify run).
docker exec sweevo-dask__dask_2023.3.2_2023.4.0 bash -lc '
  . /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed
  cd /testbed
  # 1. Bulk run — 11 fail
  python -c "
import json, pytest
with open(\"/tmp/p2p_ids.json\") as f: ids = json.load(f)
print(pytest.main([\"--continue-on-collection-errors\",\"-n0\",\"-rfE\",\"--tb=no\",\"-q\"] + ids))
" 2>&1 | grep -E "^FAILED " | wc -l   # expect 11

  # 2. Isolated run — all 11 pass
  python -m pytest \
    dask/dataframe/io/tests/test_csv.py::test_to_csv_with_get \
    dask/dataframe/io/tests/test_io.py::test_to_bag \
    dask/dataframe/io/tests/test_parquet.py::test_to_parquet_lazy[fastparquet-processes] \
    --tb=line -rA
'
```

## 8. Provenance

- Reported by: `backend/scripts/diagnose_p2p_failures.py` (uses the
  production `_run_test_set` with an `_exec` interceptor that mines
  pytest stdout for `^FAILED ` lines).
- First surfaced during the full-surface mocked-agent verification of
  `docs/plans/sweevo_layerstack_migration_PLAN.md` on 2026-05-27.
- See also: `docs/plans/sweevo_layerstack_migration_REPORT.md` §4.6
  and §4.8.
