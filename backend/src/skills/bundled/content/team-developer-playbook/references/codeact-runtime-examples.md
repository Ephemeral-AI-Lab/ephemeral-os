# CodeAct Runtime Examples

Use this reference before the first `daytona_codeact` verification or reproduction command on a benchmark lane.

## Rules

- Must execute repo commands through `shell("...")` inside `daytona_codeact`.
- Must default to repo-root-relative commands inside the shell string.
- Must not prepend guessed repo roots like `cd /testbed &&` when the sandbox cwd is already injected; only `cd` into a real repo subdirectory when the command truly needs it.
- Must not replace a simple repo command with raw Python `subprocess.run(...)` boilerplate.
- Must treat missing tools, missing modules, or unsupported flags as runtime evidence first.
- Must not start pip-install loops or ad hoc environment mutation unless repo bootstrap evidence proves the lane owns that setup.
- Never treat ambient package installation as the default next step just because the first verify command failed.
- After one existing-environment probe, must either use the working command form or switch to source-and-test reading on the owned repo surface.
- Never turn an install rejection into a `pip` -> `pip3` -> `conda` -> `uv` retry ladder.

## Few-shot examples

- Example: payload verify command is `python -m pytest dask/tests/test_cli.py -x`.
  Call `daytona_codeact` with code like `shell("python -m pytest dask/tests/test_cli.py -x", timeout=120)`.
  Do not wrap that same command in Python `subprocess.run(...)`.
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
