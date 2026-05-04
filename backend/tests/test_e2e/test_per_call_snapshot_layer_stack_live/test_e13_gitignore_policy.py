from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    make_workdir,
    parse_json_line,
    print_live_metric,
    python_json_command,
    require_commands,
    run_live_command,
    xfail_production_binding_missing,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e13_live_gitignore_oracle_when_git_is_present(live_snapshot_sandbox):
    await require_commands(live_snapshot_sandbox, "git", "python3")
    workdir = await make_workdir(live_snapshot_sandbox, "e13")
    command = (
        f"cd {workdir} && "
        "git init -q && "
        "git config user.email e13@test.invalid && "
        "git config user.name e13 && "
        "mkdir -p src dist .pytest_cache/v/cache && "
        "printf 'dist/\\n.pytest_cache/\\n' > .gitignore && "
        "printf 'print(1)\\n' > src/foo.py && "
        "git add .gitignore src/foo.py && git commit -q -m seed"
    )
    init = await run_live_command(live_snapshot_sandbox, command, timeout=60, label="e13.init")
    assert_success(init)
    classify = await run_live_command(
        live_snapshot_sandbox,
        python_json_command(
            f"""
            import json
            import subprocess

            paths = ["src/foo.py", "dist/bundle.js", ".pytest_cache/v/cache/lastfailed"]
            results = {{}}
            for path in paths:
                proc = subprocess.run(
                    ["git", "check-ignore", "-q", path],
                    cwd={workdir!r},
                    check=False,
                )
                results[path] = "ignored" if proc.returncode == 0 else "tracked_or_unmatched"
            print(json.dumps(results, sort_keys=True))
            """
        ),
        timeout=60,
        label="e13.classify",
    )
    assert_success(classify)
    payload = parse_json_line(classify.stdout)
    print_live_metric("e13.summary", **payload)
    assert payload["src/foo.py"] == "tracked_or_unmatched"
    assert payload["dist/bundle.js"] == "ignored"
    assert payload[".pytest_cache/v/cache/lastfailed"] == "ignored"


async def test_e13_production_gitignore_policy_contract_required():
    xfail_production_binding_missing("E13 gitignore-aware policy classification")
