"""Startup and orphan garbage collection for isolated workspaces."""

from __future__ import annotations

import contextlib
import ipaddress
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

from sandbox.isolated_workspace._types import CGROUP_ROOT, HANDLE_PREFIX, logger


class _IsolatedGcMixin:
        async def startup_gc(self) -> None:
            """Reap orphan resources after daemon restart; reconcile IP pool.

            After a fresh daemon start the in-memory ``_handles`` is empty: every
            row in persisted ``manager.json`` is by definition a zombie whose
            kernel resources (veth, cgroup, holder process, lease) outlived the
            last daemon. We:

            1. Reserve each persisted handle's IP so a concurrent ``enter`` cannot
               re-allocate one that an in-flight orphan may still be using.
            2. Release each persisted handle's lease so the OCC layer-stack can
               advance again.
            3. For each persisted handle, unfreeze the cgroup BEFORE rmdir so any
               lingering PID can be killed (R5 ordering — pinned by
               ``test_daemon_restart_gc_order_unfreeze_before_kill``).
            4. Sweep any remaining ``eos-iws-*`` veth / scratch / cgroup by
               naming convention.
            """
            persisted = self._read_manager_json()
            persisted_handles = list(persisted.get("handles", []))
            for row in persisted_handles:
                ns_ip = row.get("ns_ip")
                if ns_ip:
                    with contextlib.suppress(ValueError):
                        self._network.pool.reserve(ipaddress.IPv4Address(ns_ip))
            for row in persisted_handles:
                self._release_orphan_lease(row)
                self._reap_orphan_cgroup(row)
            # in-memory is empty on a fresh daemon — every named iws resource is
            # an orphan candidate.
            self._reap_orphans(live_set=set())

        def _reap_orphans(self, live_set: set[str]) -> None:
            # Per-orphan gc_orphan timing (PLAN §15.3): each event carries its own
            # ``total_ms`` plus ``phases_ms.{discover, reap}``. The discover cost
            # is amortized across the orphans found in that pass.
            t0 = self._clock()
            result = subprocess.run(
                ["ip", "-o", "link", "show"], capture_output=True, text=True, check=False,
            )
            veth_discover_ms = (self._clock() - t0) * 1000.0
            veth_orphans: list[str] = []
            for line in result.stdout.splitlines():
                # ``ip -o link show`` formats lines as
                #     "<idx>: <ifname>[@<peer>]: <flags> ..."
                # — the trailing colon sticks to the ifname token. The earlier
                # ``":" not in token`` filter skipped every veth (each one has
                # ``@if<n>:``), so no orphan veth was ever discovered. Strip
                # the trailing colon then drop the ``@<peer>`` suffix so the
                # remaining string is exactly what ``ip link del`` expects.
                for token in line.split():
                    cleaned = token.rstrip(":").split("@", 1)[0]
                    if cleaned.startswith(HANDLE_PREFIX):
                        short = cleaned[len(HANDLE_PREFIX):].rstrip("hn")
                        if not any(hid.startswith(short) for hid in live_set):
                            veth_orphans.append(cleaned)
                        # The ifname is always the second whitespace token on
                        # the line; no need to keep scanning flag tokens.
                        break
            veth_share_ms = (
                veth_discover_ms / len(veth_orphans) if veth_orphans else 0.0
            )
            for name in veth_orphans:
                t_reap = self._clock()
                subprocess.run(
                    ["ip", "link", "del", name],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                reap_ms = (self._clock() - t_reap) * 1000.0
                self._emit(
                    "sandbox_isolated_workspace_gc_orphan",
                    {
                        "kind": "veth",
                        "identifier": name,
                        "total_ms": veth_share_ms + reap_ms,
                        "phases_ms": {"discover": veth_share_ms, "reap": reap_ms},
                    },
                )

            scratch = self.scratch_root
            if scratch.is_dir():
                t0 = self._clock()
                scratch_children = [c for c in scratch.iterdir() if c.name != "manager.json"]
                scratch_discover_ms = (self._clock() - t0) * 1000.0
                scratch_orphans = [c for c in scratch_children if c.name not in live_set]
                scratch_share_ms = (
                    scratch_discover_ms / len(scratch_orphans) if scratch_orphans else 0.0
                )
                for child in scratch_orphans:
                    t_reap = self._clock()
                    shutil.rmtree(child, ignore_errors=True)
                    reap_ms = (self._clock() - t_reap) * 1000.0
                    self._emit(
                        "sandbox_isolated_workspace_gc_orphan",
                        {
                            "kind": "scratch",
                            "identifier": child.name,
                            "total_ms": scratch_share_ms + reap_ms,
                            "phases_ms": {"discover": scratch_share_ms, "reap": reap_ms},
                        },
                    )

            # Cgroup naming-convention sweep — anything left after the per-handle
            # release in startup_gc (e.g. created by a different daemon version
            # that crashed before persisting manager.json) gets unfrozen + rmdir'd
            # here.
            if CGROUP_ROOT.is_dir():
                t0 = self._clock()
                cgroup_children = [
                    c for c in CGROUP_ROOT.iterdir()
                    if c.is_dir() and c.name.startswith(HANDLE_PREFIX)
                ]
                cgroup_discover_ms = (self._clock() - t0) * 1000.0
                cgroup_orphans = [
                    c for c in cgroup_children
                    if c.name[len(HANDLE_PREFIX):] not in live_set
                ]
                cgroup_share_ms = (
                    cgroup_discover_ms / len(cgroup_orphans) if cgroup_orphans else 0.0
                )
                for child in cgroup_orphans:
                    t_reap = self._clock()
                    self._unfreeze_and_kill(child)
                    with contextlib.suppress(OSError):
                        child.rmdir()
                    reap_ms = (self._clock() - t_reap) * 1000.0
                    self._emit(
                        "sandbox_isolated_workspace_gc_orphan",
                        {
                            "kind": "cgroup",
                            "identifier": child.name,
                            "total_ms": cgroup_share_ms + reap_ms,
                            "phases_ms": {"discover": cgroup_share_ms, "reap": reap_ms},
                        },
                    )

        def _release_orphan_lease(self, persisted_row: dict[str, Any]) -> None:
            """Release a lease that survived the daemon process."""
            lease_id = persisted_row.get("lease_id")
            if not lease_id:
                return
            t0 = self._clock()
            released = False
            with contextlib.suppress(Exception):
                released = bool(
                    self._layer_stack.release_workspace_snapshot(
                        self._layer_stack_root, lease_id=lease_id,
                    )
                )
            reap_ms = (self._clock() - t0) * 1000.0
            self._emit(
                "sandbox_isolated_workspace_gc_orphan",
                {
                    "kind": "lease",
                    "identifier": lease_id,
                    "released": released,
                    "total_ms": reap_ms,
                    "phases_ms": {"reap": reap_ms},
                },
            )

        def _reap_orphan_cgroup(self, persisted_row: dict[str, Any]) -> None:
            """Unfreeze (R5) and remove a persisted handle's cgroup directory."""
            cg_path = persisted_row.get("cgroup_path")
            if not cg_path:
                return
            cgroup = Path(cg_path)
            if not cgroup.exists():
                return
            t0 = self._clock()
            self._unfreeze_and_kill(cgroup)
            with contextlib.suppress(OSError):
                cgroup.rmdir()
            reap_ms = (self._clock() - t0) * 1000.0
            self._emit(
                "sandbox_isolated_workspace_gc_orphan",
                {
                    "kind": "cgroup",
                    "identifier": cgroup.name,
                    "total_ms": reap_ms,
                    "phases_ms": {"reap": reap_ms},
                },
            )

        def _unfreeze_and_kill(self, cgroup: Path) -> None:
            """Unfreeze a cgroup THEN kill its remaining PIDs (R5 ordering).

            Logs both steps so the order survives test inspection
            (``test_daemon_restart_gc_order_unfreeze_before_kill``).
            """
            freeze_file = cgroup / "cgroup.freeze"
            if freeze_file.exists():
                logger.info("isolated_workspace_gc_unfreeze cgroup=%s", cgroup.name)
                with contextlib.suppress(OSError):
                    freeze_file.write_text("0\n")
            kill_file = cgroup / "cgroup.kill"
            if kill_file.exists():
                logger.info("isolated_workspace_gc_kill cgroup=%s", cgroup.name)
                with contextlib.suppress(OSError):
                    kill_file.write_text("1\n")
                return
            procs_file = cgroup / "cgroup.procs"
            if procs_file.exists():
                logger.info("isolated_workspace_gc_kill cgroup=%s", cgroup.name)
                with contextlib.suppress(OSError):
                    pids = [
                        int(line) for line in procs_file.read_text().splitlines()
                        if line.strip().isdigit()
                    ]
                    for pid in pids:
                        with contextlib.suppress(ProcessLookupError, PermissionError):
                            os.kill(pid, signal.SIGKILL)



__all__ = ["_IsolatedGcMixin"]
