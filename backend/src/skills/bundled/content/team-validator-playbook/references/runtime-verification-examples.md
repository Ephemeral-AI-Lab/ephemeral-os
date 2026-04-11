# Runtime Verification Examples

Use this reference before the first `daytona_codeact` verification command on a benchmark lane.

## Rules

- Must run the exact payload command through `shell("...")` inside `daytona_codeact`.
- Must print or persist the first command's exit code and relevant output from that same `shell(...)` run.
- If the exact payload command exits `0`, must return PASS from that evidence.
- If output capture is awkward, may redirect inside `shell("...")` and inspect the saved file with a structured read.
- Must not switch to `subprocess.run(...)`, `subprocess.Popen(...)`, or helper Python wrappers just because `shell(...)` output is short.
- Must not rerun a green command with `--collect-only`, `ls`, or extra probes to "confirm" the pass.
- Must not rerun a failing broad regression command once it already printed the failing ids you need.

## Few-shot examples

- Example: payload verify is `pytest dask/tests/test_cli.py dask/tests/test_compatibility.py --continue-on-collection-errors -n0 -rA --color=no`.
  Run `result = shell("pytest dask/tests/test_cli.py dask/tests/test_compatibility.py --continue-on-collection-errors -n0 -rA --color=no", timeout=180)`.
  Print `result.exit_code` plus a short stdout or stderr tail from that same run, then decide PASS or FAIL.
  Do not retry with `subprocess.run(...)` because the first output looked sparse.
- Example: the exact pytest command passes, but stdout is long and the adapter truncates it.
  Run one `shell("pytest ... > /tmp/validator.out 2>&1; code=$?; echo EXIT_CODE:$code")`, then inspect `/tmp/validator.out` with a structured read if you need the last lines.
  Do not rerun pytest with `--collect-only`, and do not inspect unrelated files to convince yourself the pass was real.
- Example: the exact broad command fails during collection and already names the failing import path.
  Return that collection failure as the verdict evidence from the first run.
  Do not narrow to a single test or rerun a second pytest command just to get cleaner text.
