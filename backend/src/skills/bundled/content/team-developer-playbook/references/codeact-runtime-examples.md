# CodeAct Runtime Examples

Use this reference before the first `daytona_codeact` verification or reproduction command on a benchmark lane.

## Rules

- The preferred benchmark-lane repo-command form is direct `daytona_codeact(command="...", timeout=N)`.
- If you truly need multi-step Python mode, keep repo commands inside `shell("...")` and still avoid `subprocess`.
- Must keep repo commands repo-root-relative and treat `Unknown tool` as your own Daytona tool-name error before retrying with the exact tool name.
- Must not start pip-install loops or ad hoc environment mutation unless repo bootstrap evidence proves the lane owns that setup.
- Must judge pass/fail from `shell(...)["exit_code"]`, not wrapper metadata.
- Must not inspect benchmark test files with `daytona_read_file(...)` before the first exact `daytona_codeact(command="...", timeout=N)` repro; use the named node, scout note, and runtime traceback first.
- If a probe returns manifest `status: error`, traceback text, or no trustworthy exit code, simplify the next retry instead of broadening.
- If pytest says a named node is missing, exits `4`, or collects `0` items, report that exact control failure or hand the file surface back to replanning.

## Examples

- Wrong first probe: `subprocess.run(...)` or a helper that shells out to pytest. Right first probe: `daytona_codeact(command="pytest pkg/tests/test_hdf.py -x", timeout=120)`.
- If a permission test runs as root and `chmod` still leaves the file readable, treat that as still-red runtime evidence and do not skip or rewrite the verify file.
