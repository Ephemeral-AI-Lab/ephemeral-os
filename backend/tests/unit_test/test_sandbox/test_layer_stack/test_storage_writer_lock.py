"""Storage-root writer serialization regressions."""

from __future__ import annotations

import threading

from sandbox.layer_stack import LayerStack


def test_transactions_for_same_storage_root_share_process_mutex(tmp_path) -> None:
    """Two live managers for one root must not enter write transactions together."""
    stack_root = tmp_path / "stack"
    first = LayerStack(stack_root)
    second = LayerStack(stack_root)

    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    first_done = threading.Event()
    second_done = threading.Event()

    def hold_first_transaction() -> None:
        try:
            with first.commit_transaction():
                first_entered.set()
                assert release_first.wait(timeout=2.0)
        finally:
            first_done.set()

    def enter_second_transaction() -> None:
        try:
            assert first_entered.wait(timeout=2.0)
            with second.commit_transaction():
                second_entered.set()
        finally:
            second_done.set()

    first_thread = threading.Thread(target=hold_first_transaction, daemon=True)
    second_thread = threading.Thread(target=enter_second_transaction, daemon=True)

    first_thread.start()
    assert first_entered.wait(timeout=2.0)
    second_thread.start()

    assert not second_entered.wait(timeout=0.05)

    release_first.set()
    assert first_done.wait(timeout=2.0)
    assert second_done.wait(timeout=2.0)
    assert second_entered.is_set()

    first_thread.join(timeout=2.0)
    second_thread.join(timeout=2.0)
