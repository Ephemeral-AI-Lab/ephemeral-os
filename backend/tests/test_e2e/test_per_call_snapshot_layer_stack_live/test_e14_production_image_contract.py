from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    parse_json_line,
    print_live_metric,
    python_json_command,
    run_live_command,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e14_production_image_records_runtime_contract(live_snapshot_sandbox):
    command = python_json_command(
        """
        import json
        import platform
        import shutil
        import subprocess

        commands = [
            "bash",
            "python3",
            "find",
            "du",
            "mktemp",
            "stat",
            "rm",
            "unshare",
            "git",
            "mount",
        ]
        versions = {}
        for command in commands:
            path = shutil.which(command)
            versions[command] = {"path": path}
            if path:
                proc = subprocess.run(
                    [command, "--version"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                versions[command]["version"] = proc.stdout.splitlines()[:2]
        print(json.dumps({"kernel": platform.release(), "commands": versions}, sort_keys=True))
        """
    )
    result = await run_live_command(
        live_snapshot_sandbox,
        command,
        timeout=60,
        label="e14.production_image_contract",
    )
    assert_success(result)
    payload = parse_json_line(result.stdout)
    commands = payload["commands"]
    required = ["bash", "python3", "find", "du", "mktemp", "stat", "rm"]
    missing_required = [name for name in required if not commands[name]["path"]]
    missing_experiment = [name for name in ["unshare", "mount"] if not commands[name]["path"]]
    missing_optional = [name for name in ["git"] if not commands[name]["path"]]
    print_live_metric(
        "e14.summary",
        kernel=payload["kernel"],
        missing_required=missing_required,
        missing_experiment=missing_experiment,
        missing_optional=missing_optional,
        commands=commands,
    )
    assert not missing_required
    if missing_experiment:
        pytest.xfail(
            "production image is missing experiment prerequisite command(s): "
            + ", ".join(missing_experiment)
        )
