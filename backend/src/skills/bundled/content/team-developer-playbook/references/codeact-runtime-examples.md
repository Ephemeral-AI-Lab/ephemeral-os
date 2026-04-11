# CodeAct Runtime Examples

Use this reference before the first `daytona_codeact` verification or reproduction command on a benchmark lane.

## Rules

- Must execute repo commands through `shell("...")` inside `daytona_codeact`.
- The first benchmark reproduction or verify call should usually be a minimal `shell("...")` snippet, not a Python mini-program.
- Must default to repo-root-relative commands inside the shell string.
- Must not prepend guessed repo roots like `cd /testbed &&` when the sandbox cwd is already injected; only `cd` into a real repo subdirectory when the command truly needs it.
- Must not replace a simple repo command with raw Python `subprocess.run(...)` boilerplate.
- Must treat missing tools, missing modules, or unsupported flags as runtime evidence first.
- Must not start pip-install loops or ad hoc environment mutation unless repo bootstrap evidence proves the lane owns that setup.
- Never treat ambient package installation as the default next step just because the first verify command failed.
- After one existing-environment probe, must either use the working command form or switch to source-and-test reading on the owned repo surface.
- Never turn an install rejection into a `pip` -> `pip3` -> `conda` -> `uv` retry ladder.
- Must treat the object returned by `shell("...")` as a mapping with keys like `stdout`, `stderr`, and `exit_code`.
- Must not use `git status`, `git log`, `git diff`, `git show`, `git blame`, `git stash`, `git checkout`, `git restore`, or temporary revert probes to prove whether a sibling failure was "already there".
- If the live source file looks different from what you expected, treat the live file plus payload evidence as authoritative and diagnose that code path directly.
- If `shell("...")` output is sparse or truncated, must capture it through shell redirection plus a structured read instead of abandoning the shell helper.
- Must not broaden from a named failing id or bounded payload command to a much larger suite unless the payload itself assigns that broader command.
- If pytest says a named node is missing, report that exact node mismatch or hand the file surface back to replanning; do not run collect-only or hunt similar names unless the payload already owns the full file.

## Few-shot examples

- Example: payload verify command is `python -m pytest dask/tests/test_cli.py -x`.
  Call `daytona_codeact` with code like `shell("python -m pytest dask/tests/test_cli.py -x", timeout=120)`.
  Do not wrap that same command in Python `subprocess.run(...)`.
- Example: payload verify command is `pytest pkg/tests/test_hdf.py -x`, and you feel like writing a helper script first.
  Wrong: `import subprocess` plus `subprocess.run(["pytest", "pkg/tests/test_hdf.py", "-x"])`.
  Right: `result = shell("pytest pkg/tests/test_hdf.py -x", timeout=120)`.
  Keep the first runtime probe in the shell helper form unless you truly need to parse a prior shell result.
- Example: `result = shell("pytest pkg/tests/test_cli.py -x", timeout=120)` already ran and you need the exit code.
  Read `result["exit_code"]` or `result["stdout"]`.
  Do not use `result.stdout`, `result.stderr`, or `result.returncode` because `shell("...")` does not return a subprocess object.
- Example: `pytest` is missing from `PATH`.
  Probe once with `shell("which pytest || python -m pytest --version || which uv", timeout=30)`, then retry with the working command form if one exists.
  If no runner exists, stop probing the ambient environment and move to `daytona_read_file`, `daytona_grep`, and owned-source diagnosis instead of installing pytest.
  Do not pivot into `subprocess`, and do not assume the benchmark fix is to install pytest.
- Example: the exact verify command dies during import because a module like `yaml` or `tlz` is missing before the named test loads.
  Treat that as still-red runtime evidence on the current lane, then inspect the owned test file, owned production file, and nearby repo import path before guessing about the environment.
  If the repository clearly owns the import path, keep diagnosing repo code; if the miss is purely ambient and outside repo ownership, surface runtime mismatch or replan evidence after that one probe.
  Do not start a generic `pip install ...` loop just because `ModuleNotFoundError` appeared first.
- Example: `python -m pytest ...` fails with `No module named pytest`, then a later probe fails with `ModuleNotFoundError: yaml`.
  Do one command-form probe, then stop environment improvisation.
  Read the named test and owned source files, localize the likely code path, and keep progress on the repo surface or escalate as ambient mismatch if the lane cannot legitimately fix it in code.
- Example: your owned target passes, but another lane reports an unrelated parquet or config failure and you want to prove your patch did not cause it.
  Stay on your owned verify command and `ci_scoped_status(...)` evidence.
  Do not run `git status`, `git log`, `git show`, `git blame`, `git stash`, or temporary revert commands inside the benchmark repo to argue about blame.
- Example: a live import shim or compatibility module looks wrong and you are tempted to compare it with `HEAD`.
  Read the live file, inspect the immediate import chain, and keep the fix on the payload-owned surface.
  Do not use `git show HEAD:path`, `git checkout -- path`, or `git restore --source=HEAD` to reconstruct history before you patch.
- Example: `shell("pytest ...")` returns a green exit code but the adapter shows little stdout.
  Capture the output in the shell command itself, such as `shell("pytest ... > /tmp/verify.out 2>&1; code=$?; echo EXIT_CODE:$code")`, then inspect `/tmp/verify.out` with a structured read if needed.
  Do not switch back to `subprocess.run(...)` or a Python wrapper just to print nicer output.
- Example: payload verify is `pytest pkg/tests/test_io_json.py::test_records_roundtrip -x`, and pytest says the node is not found.
  Surface that exact missing node or hand the exact file path back to replanning if the parent lane owned the file.
  Do not run `--collect-only`, grep for similar names, or silently swap in a nearby test id.
- Example: payload verify is `pytest dask/dataframe/tests/test_groupby.py::test_groupby_unique[disk-uint8] -x`.
  Keep reproductions and post-edit verifies on that named test or the exact payload command.
  Do not jump to the entire `dask/dataframe/tests/test_groupby.py` module or a broad `-k` sweep unless the payload already told you to own that larger surface.
