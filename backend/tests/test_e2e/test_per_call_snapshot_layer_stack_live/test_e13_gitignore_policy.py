from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    mark_ignored,
    make_workdir,
    print_live_metric,
    read_live_file,
    run_live_command,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e13_api_gitignore_policy_routes_direct_paths(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e13")
    tracked = f"{workdir}/src/foo.py"
    ignored_bundle = f"{workdir}/dist/bundle.js"
    ignored_cache = f"{workdir}/.pytest_cache/v/cache/lastfailed"
    mark_ignored(
        live_snapshot_sandbox,
        [f"{workdir}/dist", f"{workdir}/.pytest_cache"],
    )
    result = await run_live_command(
        live_snapshot_sandbox,
        (
            f"mkdir -p {workdir}/src {workdir}/dist {workdir}/.pytest_cache/v/cache && "
            f"printf 'print(1)\\n' > {tracked} && "
            f"printf 'bundle\\n' > {ignored_bundle} && "
            f"printf 'cache\\n' > {ignored_cache}"
        ),
        timeout=60,
        label="e13.gitignore_policy",
    )
    assert_success(result)
    payload = {
        "tracked": await read_live_file(live_snapshot_sandbox, tracked, label="e13.read_tracked"),
        "ignored_bundle": await read_live_file(
            live_snapshot_sandbox,
            ignored_bundle,
            label="e13.read_bundle",
        ),
        "ignored_cache": await read_live_file(
            live_snapshot_sandbox,
            ignored_cache,
            label="e13.read_cache",
        ),
        "changed_paths": list(result.changed_paths),
    }
    print_live_metric("e13.summary", **payload)
    assert payload["tracked"] == "print(1)\n"
    assert payload["ignored_bundle"] == "bundle\n"
    assert payload["ignored_cache"] == "cache\n"
    assert set(result.changed_paths) == {tracked, ignored_bundle, ignored_cache}
