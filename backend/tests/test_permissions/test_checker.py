"""Tests for permission decisions."""

from config.settings import PermissionSettings
from permissions import PermissionChecker, PermissionMode


def test_default_mode_allows_read_only():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate("read_file", is_read_only=True)
    assert decision.allowed is True
    assert decision.requires_confirmation is False


def test_default_mode_requires_confirmation_for_mutation():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    decision = checker.evaluate("write_file", is_read_only=False)
    assert decision.allowed is False
    assert decision.requires_confirmation is True


def test_plan_mode_blocks_mutating_tools():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.PLAN))
    decision = checker.evaluate("bash", is_read_only=False)
    assert decision.allowed is False
    assert "plan mode" in decision.reason


def test_full_auto_allows_mutating_tools():
    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO))
    decision = checker.evaluate("bash", is_read_only=False)
    assert decision.allowed is True
