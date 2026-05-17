"""Render in-sandbox probe scripts that import the runtime bundle.

Each native probe runs as::

    cd /tmp/eos-sandbox-runtime && python3 -c "<probe source>"

The host pytest process must not import ``sandbox.layer_stack``,
``sandbox.overlay``, or ``sandbox.occ`` (the import-fence in
``conftest.py`` rejects that). Probes therefore live as Python source
strings; native_probe.render concatenates the resource sampler from
``resource_metrics.py`` with a per-probe ``body`` and substitutes
``__CFG_JSON__`` with the JSON-encoded config.

This module mirrors ``overlay_probe.py``'s shape so that test files have
one consistent rendering surface.
"""

from __future__ import annotations

import json
import shlex

from .resource_metrics import RESOURCE_PRELUDE


BUNDLE_REMOTE_DIR = "/tmp/eos-sandbox-runtime"
BUNDLE_HASH_MARKER = f"{BUNDLE_REMOTE_DIR}/.bundle-hash"
LAYER_STACK_TEST_PREFIX = f"{BUNDLE_REMOTE_DIR}/layer-stack-test-"


_PROBE_PRELUDE = r"""
import json, os, sys, time, traceback

# Native probes are launched with cwd=/tmp/eos-sandbox-runtime so that
# `import sandbox.layer_stack` etc. resolves to the runtime bundle.
sys.path.insert(0, os.getcwd())
"""


def render(body: str, *, cfg: dict | None = None) -> str:
    """Render *body* into a full probe source string.

    Concatenates ``_PROBE_PRELUDE`` (sys.path setup) + ``RESOURCE_PRELUDE``
    (resource sampler) + ``body``. If *cfg* is given, ``__CFG_JSON__`` is
    replaced with ``repr(json.dumps(cfg))`` exactly like
    ``overlay_probe._render``.
    """
    src = _PROBE_PRELUDE + RESOURCE_PRELUDE + body
    if cfg is not None:
        src = src.replace("__CFG_JSON__", repr(json.dumps(cfg)))
    return src


def shell_command(source: str) -> str:
    """Wrap *source* in the canonical ``cd <bundle> && python3 -c <src>`` shell line."""
    return "cd {bundle} && {launcher}".format(
        bundle=shlex.quote(BUNDLE_REMOTE_DIR),
        launcher=_python_launcher(source),
    )


def wrap_unshare(source: str, *, prog: str = "python3") -> str:
    """Run *source* under ``unshare -Urm`` so namespace probes get CAP_SYS_ADMIN.

    Equivalent to ``overlay_probe.wrap_unshare`` but for native probes that
    need the runtime bundle on ``sys.path``: the unshare child still cd's
    into ``/tmp/eos-sandbox-runtime`` first.
    """
    return "cd {bundle} && unshare -Urm {prog} -c {src}".format(
        bundle=shlex.quote(BUNDLE_REMOTE_DIR),
        prog=shlex.quote(prog),
        src=shlex.quote(source),
    )


def _python_launcher(source: str) -> str:
    """Choose the same Python floor as the resident daemon."""
    version_check = "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    src = shlex.quote(source)
    check = shlex.quote(version_check)
    return (
        "for py in python3.13 python3.12 python3.11 python3.10 python3; do "
        "if command -v \"$py\" >/dev/null 2>&1 "
        f"&& \"$py\" -c {check} >/dev/null 2>&1; then "
        f"exec \"$py\" -c {src}; "
        "fi; "
        "done; "
        "echo 'sandbox native probe requires Python >= 3.10' >&2; "
        "exit 127"
    )


__all__ = [
    "BUNDLE_REMOTE_DIR",
    "BUNDLE_HASH_MARKER",
    "LAYER_STACK_TEST_PREFIX",
    "render",
    "shell_command",
    "wrap_unshare",
]
