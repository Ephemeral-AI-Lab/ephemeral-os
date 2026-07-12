"""Session-wide sandbox registry and failure-visible teardown helpers.

Every sandbox the suite creates (via ``manager.management.helpers.create_sandbox``)
is tracked here, so a session-end finalizer can destroy any that a test leaked —
e.g. tests that create inline and fail before their own teardown. Only ids the
suite created are tracked; sandboxes owned by other clients are never touched.
"""

from collections.abc import Callable, Iterable


class CleanupAggregateError(RuntimeError):
    """Raised after every cleanup action runs and at least one failed."""

    def __init__(self, failures):
        self.failures = failures
        details = "; ".join(f"{name}: {error}" for name, error in failures)
        super().__init__(f"cleanup failed: {details}")

_tracked = set()


def track(sandbox_id):
    if sandbox_id:
        _tracked.add(sandbox_id)


def untrack(sandbox_id):
    _tracked.discard(sandbox_id)


def drain():
    """Return and clear all tracked ids."""
    ids = sorted(_tracked)
    _tracked.clear()
    return ids


def run_all(actions: Iterable[tuple[str, Callable[[], object]]]) -> None:
    failures = []
    for name, action in actions:
        try:
            action()
        except Exception as error:
            failures.append((name, error))
    if failures:
        raise CleanupAggregateError(failures)
