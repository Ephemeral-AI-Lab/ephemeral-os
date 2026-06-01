"""In-sandbox resource sampler shipped as a Python source string.

Used by ``native_probe.render`` and (optionally) other in-sandbox probes that
need the §3.5 resource block. The sampler is pasted as a string into the
probe source so the host pytest process never imports ``sandbox.*`` modules
on its own — the import-fence in ``conftest.py`` would reject that.

The sampler exports a single helper ``sample_resource()`` that returns the
§3.5 dict with ``fd_open``, ``rss_kb``, ``rss_peak_kb``, ``threads``,
``mounts``, ``overlay_mounts``, ``inodes_used``, ``wall_ms``,
``cpu_user_ms``, and ``cpu_sys_ms``.
"""

from __future__ import annotations


# IMPORTANT: keep this string self-contained. It is concatenated with each
# probe body and `eval`-ed inside the sandbox. Imports/state must be safe to
# re-execute alongside the probe-specific prelude in ``native_probe.py``.
RESOURCE_PRELUDE = r"""
import os as _os_rm, subprocess as _sp_rm, time as _t_rm

_RM_T0 = _t_rm.perf_counter()


def _rm_status_kv():
    out = {}
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _rm_status_int(kv, key):
    raw = kv.get(key, "")
    parts = raw.split()
    if not parts:
        return 0
    try:
        return int(parts[0])
    except ValueError:
        return 0


def _rm_inodes_used(path):
    try:
        proc = _sp_rm.run(
            ["df", "-i", "--output=iused", path],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, _sp_rm.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    raw = lines[-1]
    if raw in ("-", ""):
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _rm_mount_counts():
    mounts = 0
    overlay_mounts = 0
    try:
        with open("/proc/self/mounts") as fh:
            for line in fh:
                mounts += 1
                if " overlay " in line:
                    overlay_mounts += 1
    except OSError:
        pass
    return mounts, overlay_mounts


def sample_resource(inode_path="/eos/daemon"):
    kv = _rm_status_kv()
    mounts, overlay_mounts = _rm_mount_counts()
    try:
        fd_open = len(_os_rm.listdir("/proc/self/fd"))
    except OSError:
        fd_open = 0
    times = _os_rm.times()
    return {
        "fd_open": fd_open,
        "rss_kb": _rm_status_int(kv, "VmRSS"),
        "rss_peak_kb": _rm_status_int(kv, "VmHWM"),
        "threads": _rm_status_int(kv, "Threads"),
        "mounts": mounts,
        "overlay_mounts": overlay_mounts,
        "inodes_used": _rm_inodes_used(inode_path),
        "wall_ms": (_t_rm.perf_counter() - _RM_T0) * 1000.0,
        "cpu_user_ms": times.user * 1000.0,
        "cpu_sys_ms": times.system * 1000.0,
    }
"""


__all__ = ["RESOURCE_PRELUDE"]
