# CodeAct Runtime Examples

Use this reference before the first `daytona_codeact` verification or reproduction command on a benchmark lane.

## Rules

- The only benchmark-lane repo-command form inside `daytona_codeact` is direct `shell("...")`.
- Must keep repo commands repo-root-relative; do not prepend guessed roots.
- Must treat missing repo tools, missing modules, or unsupported flags as runtime evidence first, but treat `Unknown tool` as your own Daytona tool-name error and retry with the exact tool name.
- Must not start pip-install loops or ad hoc environment mutation unless repo bootstrap evidence proves the lane owns that setup.
- Must treat the object returned by `shell("...")` as a mapping with keys like `stdout`, `stderr`, and `exit_code`.
- Must judge pass/fail from that same run's `exit_code`; wrapper status and `__CODEX_EXIT_CODE__` are instrumentation only.
- If a `daytona_codeact` snippet returns manifest `status: error`, Python traceback text, or no trustworthy `shell(...)["exit_code"]`, treat that probe as broken instrumentation and simplify the next retry.
- If `shell("...")` output is sparse or piped through `head` or `tail`, use `set -o pipefail` or rerun the exact command before treating `exit_code` as success.
- Must not broaden from a named failing id or bounded task command to a much larger suite, and must not narrow by `-k`, `--ignore`, `--deselect`, or skip edits to verify around a still-red failure.
- If pytest says a named node is missing, exits 4, or collects 0 items, report that exact control failure or hand the file surface back to replanning.
- If the failure happens during import, warning-filter parsing, collection, or permission-gated config loading, the first runtime verify after an edit must prove that exact path is healthy before any broader sweep.

## Few-shot examples

- Example: the task says `pytest pkg/tests/test_hdf.py -x`, and you feel like writing a helper script first.
  Wrong first probe: `subprocess.run(...)` or a helper that shells out to pytest.
  Right first probe: `result = shell("pytest pkg/tests/test_hdf.py -x", timeout=120)`.
- Example: the shell wrapper looks green, but the command may have failed.
  Emit a packet like `{"exit_code": 1, "phase": "collection", "failed_ids": ["pkg/tests/test_hdf.py"], "next_question": "which import edge owns the crash?"}` from the same run's output.
- Example: the exact node stays red and a broader suite minus that node goes green.
  Keep the exact node in scope. Do not use `--deselect`, `--ignore`, or `-k "not ..."`.
- Example: a permission test runs as root and `chmod` still leaves the file readable.
  Treat that as still-red runtime evidence, inspect the owned loader or access gate, and do not skip or rewrite the verify file.
