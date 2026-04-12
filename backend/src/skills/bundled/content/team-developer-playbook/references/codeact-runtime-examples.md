# CodeAct Runtime Examples

Use this reference before the first `daytona_codeact` verification or reproduction command on a benchmark lane.
## Rules

- The only benchmark-lane repo-command form inside `daytona_codeact` is direct `shell("...")`; if the verify command is already known, the first reproduction or verify call should be that exact shell snippet, not `subprocess`, `os.system`, `check_output`, `Popen`, or a Python wrapper that only replays the same command.
- Must default to repo-root-relative commands inside the shell string, and must not prepend guessed repo roots like `cd /testbed &&` when the sandbox cwd is already injected.
- Must treat missing repo tools, missing modules, or unsupported flags as runtime evidence first, but treat `Unknown tool` as your own Daytona tool-name error and retry with the exact tool name.
- Must not start pip-install loops or ad hoc environment mutation unless repo bootstrap evidence proves the lane owns that setup.
- After one existing-environment probe or one guardrail rejection, must either use the working direct-shell command form or switch to source-and-test reading on the owned repo surface.
- Must treat the object returned by `shell("...")` as a mapping with keys like `stdout`, `stderr`, and `exit_code`, judge pass/fail from that mapping, and print the same run's `exit_code` plus exact PASS/FAIL lines before summary; if `exit_code` is nonzero and a tail sample looks green, extract `FAILED` or short-summary lines before concluding anything. `daytona_codeact` manifest output, wrapper `RC`, and `__CODEX_EXIT_CODE__` only describe wrapper health.
- If a `daytona_codeact` snippet returns manifest `status: error`, Python traceback text, or no trustworthy `shell(...)["exit_code"]`, treat that probe as failed instrumentation and simplify the next retry to one smaller direct shell command or one `python - <<'PY'` block executed through `shell("...")`.
- Must not use `git status`, `git log`, `git diff`, `git show`, `git blame`, `git stash`, `git checkout`, `git restore`, upstream-commit lookups, or temporary revert probes to prove whether a sibling failure was "already there"; if you already did, treat later `not found`, missing-symbol, or half-edited-file behavior as a poisoned lane and replan from a healthy checkpoint.
- If the live source file looks different from what you expected, a `daytona_edit_file` search misses, or tests invoke `pkg.cli.name` directly, treat the live file, exported object shape, and sibling startup imports as authoritative, rebuild the edit from current text, and keep repo writes on edit/write tools instead of `daytona_codeact`.
- If `shell("...")` output is sparse, truncated, or piped through `head`/`tail`, must use `set -o pipefail` or the exact verify command before treating `exit_code` as success; sampled reproductions are preview-only.
- Must not broaden from a named failing id or bounded payload command to a much larger suite unless the payload itself assigns that broader command, and must not narrow by `-k`, `--ignore`, `--deselect`, xfail/skip edits, or broader-suite-minus-one-node tricks to verify around a still-red owned failure.
- For aggregate, dtype, or MultiIndex-shape failures, the first custom runtime probe must print both the pandas expected object and the immediate intermediate object consumed by the shared builder named by CI/LSP evidence; broad suite grep output is not localization.
- If pytest says a named node is missing, exits 4, or collects 0 items, report that exact control failure or hand the file surface back to replanning; do not treat wrapper success as proof, and do not run collect-only or hunt similar names unless the payload already owns the full file.
- If the failure happens during import, warning-filter parsing, collection, or permission-gated config loading, the first runtime verify after an edit must prove that exact import/readability path is healthy before any broader sweep; if a private symbol still has internal startup importers, keep that path quiet and never move it behind a warning-producing `__getattr__` before changing the public warning contract, and if the lane runs as root, do not classify a permission test as ambient until one live source read proves the owned gate really depends on root semantics.
- If a shared-module edit creates a new import or collection crash, fix that crash on the same chain before resuming unrelated diagnosis.
- If a broader verify first crashes in a shared non-owned file and the live text looks half-edited or contradictory, confirm that exact traceback once, then widen one step on the same chain or surface a blocker; do not use git/history probes to decide whether the breakage was older than your lane.
- When the owned assertion is `pytest.warns(...)` or `pytest.raises(..., match=...)`, verify the live import/export object model, unsupported-engine ordering, or regex behavior before changing warning paths, error strings, or deprecated public facades.
## Few-shot examples
- Example: payload owns `pkg/tests/test_parquet.py::test_nullable`, that node stays red, and a temporary revert or alternate backend run suggests it may predate your patch.
  Keep that exact node in scope and trace the live failing boundary from the current command; do not run `--deselect`, `--ignore`, `-k "not test_nullable"`, or claim green from a broader suite with that red node removed.
- Example: payload verify command is `pytest pkg/tests/test_hdf.py -x`, and you feel like writing a helper script first.
  Wrong first probe: `import subprocess`, `os.system`, or a helper that shells out to `pytest pkg/tests/test_hdf.py -x`.
  Right first probe: `result = shell("pytest pkg/tests/test_hdf.py -x", timeout=120)`.
  After one guardrail rejection, switch directly to that shell helper form and do not spend a second runtime attempt replaying the same command through Python on another file.
- Example: the log shows `EXIT_CODE=137`, `timeout`, `Killed`, or only `{"manifest": "...", "status": "ok"}` while the worker never prints `result["exit_code"]` or any PASS/FAIL lines.
  Treat that as unfinished evidence. Trust `shell(...)["exit_code"]` only from the same run, print the real runtime output, and do not narrate a pass from wrapper success.
- Example: `shell(...)["exit_code"]` is nonzero, but the last 100 lines of stdout show only PASS lines because the failing nodes and short summary scrolled earlier.
  Do not call that "passing so far". Search the same stdout for `FAILED`, `ERROR`, or `short test summary info`, then anchor on those exact failing ids before any more source reads or reruns.
- Example: a custom `daytona_codeact` probe prints `{"status":"error"}` or a Python traceback, while the wrapper still reports `__CODEX_EXIT_CODE__=0`.
  Treat the probe as broken instrumentation, not repo evidence, and rewrite the next attempt as one direct `shell("pytest ...")` command or one `shell("python - <<'PY' ... PY")` block that prints the expected object and exits cleanly.
- Example: payload owns permission or readability tests, the lane runs as root, and `chmod` still leaves files readable.
  Treat that as still-red runtime evidence on the current lane, then inspect the owned loader and its access gate before blaming the environment.
  If the source checks access or mode bits, patch or replan from that gate; if it only relies on `open()` or `os.listdir()`, prove why the contract should still fail under root before calling the issue ambient.
- Example: payload owns `pkg/tests/test_compat.py::test_deprecation`, and a private symbol now warns during `pkg/__init__.py` import because a deprecation edit moved it behind `pkg.compat.__getattr__`.
  Verify the exact node plus one narrow import-smoke command after the edit, then read the live internal importer and keep the public deprecated facade warning on explicit use.
  If `pkg/__init__.py` or `pkg/base.py` still imports that name, switch startup callers to the supported quiet path in the same fix packet or restore the quiet internal path first.
  Do not bypass startup or repo pytest config with `-c /dev/null`, `--override-ini`, `-p no:warnings`, speculative caller-stack filtering, or a warning-producing `__getattr__` that still fires during package import.
- Example: you feel tempted to run `git log`, `git show`, or `git stash` to find an upstream fix, compare pre/post state, or argue that the failure was pre-existing.
  Do not use git archaeology or workspace mutation on a benchmark lane. Read the live owner file and current failing tests, query the active call chain, then patch or replan from the live boundary.
  If you already mutated the workspace and the target later turns into `not found`, `0 items`, or a missing helper, stop treating that sandbox as trustworthy and resume from a healthy checkpoint.
- Example: `groupby.value_counts` and empty-partition siblings fail with the same wrong MultiIndex shape.
  Query the shared result-builder first, then print the pandas reference result and the immediate object passed into that builder on the exact failing node.
