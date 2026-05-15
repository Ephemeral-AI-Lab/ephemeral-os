"""Import-order regressions for the episode package facade."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_persistence_can_import_episode_state_before_manager_facade() -> None:
    """A clean interpreter must not hit the episode manager/persistence cycle."""
    backend_dir = Path(__file__).resolve().parents[4]
    src_dir = backend_dir / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{src_dir}:{env['PYTHONPATH']}" if env.get("PYTHONPATH") else str(src_dir)
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "from task_center._core.persistence import AttemptStoreProtocol",
                    "from task_center.iteration import Iteration, IterationManagerRegistry",
                    "assert AttemptStoreProtocol",
                    "assert Iteration",
                    "assert IterationManagerRegistry",
                ]
            ),
        ],
        cwd=backend_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
