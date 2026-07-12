import pytest

from core import cleanup


def test_run_all_executes_every_action_and_reports_failures():
    events = []

    def fail():
        events.append("failed")
        raise RuntimeError("injected cleanup failure")

    with pytest.raises(cleanup.CleanupAggregateError) as raised:
        cleanup.run_all(
            (
                ("first", lambda: events.append("first")),
                ("failed", fail),
                ("last", lambda: events.append("last")),
            )
        )

    assert events == ["first", "failed", "last"]
    assert raised.value.failures[0][0] == "failed"
    assert "injected cleanup failure" in str(raised.value)
