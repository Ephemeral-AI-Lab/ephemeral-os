"""Vulture whitelist for the sandbox-reframe (W4a) dead-code audit.

Names listed here are intentionally kept even when vulture flags them as
unused. Each entry should have a short comment explaining why.

Usage:
    .venv/bin/vulture backend/src/sandbox backend/scripts/vulture_whitelist.py \
        --min-confidence 80
"""

# RUNTIME_SCRIPT_DIR is the constant naming the in-sandbox script path.
# Used by host/runtime_bundle.py at deploy time; vulture sometimes misses
# the indirect reference because it's threaded through a tar arcname.
RUNTIME_SCRIPT_DIR = None  # noqa: F841  (whitelist anchor)

# Audit event-name constants live in sandbox.audit.events; they are
# downstream consumer identifiers (log parsers, dashboards). Even if a
# given constant has no internal call site at audit time, it must not be
# deleted. Sample anchor below; the audit file is large, so we anchor on
# the canonical "overlay executed" event verified in W2 grep gates.
OVERLAY_EXECUTED = "sandbox.overlay.executed"  # noqa: F841

# Plugin extensibility surface (per RFC §13 AC#14):
register_op = None  # noqa: F841  (kept for plugin pipeline registration)
ProviderAdapter = None  # noqa: F841  (Protocol — provider extensibility)
DaytonaProviderAdapter = None  # noqa: F841  (Protocol impl)

# Filename constants (in-sandbox files, not module paths) preserved
# unchanged across runtime->daemon rename:
RUNTIME_SOCK = "runtime.sock"  # noqa: F841
RUNTIME_PID = "runtime.pid"  # noqa: F841
RUNTIME_LOG = "runtime.log"  # noqa: F841
RUNTIME_ENV = "runtime.env"  # noqa: F841

# Runtime-bundle identifiers (payload concept, not module path):
runtime_bundle_bytes = None  # noqa: F841
ensure_runtime_uploaded = None  # noqa: F841
RuntimeBundle = None  # noqa: F841
