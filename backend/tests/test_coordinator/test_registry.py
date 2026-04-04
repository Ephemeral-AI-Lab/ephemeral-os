"""Tests for the minimal team registry."""

import pytest

from ephemeralos.coordinator.coordinator_mode import TeamRegistry


def test_create_add_and_delete_team():
    registry = TeamRegistry()
    team = registry.create_team("alpha", "demo")
    registry.add_agent("alpha", "a123")
    registry.send_message("alpha", "hello")

    assert team.name == "alpha"
    assert team.agents == ["a123"]
    assert team.messages == ["hello"]

    registry.delete_team("alpha")
    assert registry.list_teams() == []


def test_duplicate_team_raises():
    registry = TeamRegistry()
    registry.create_team("alpha")
    with pytest.raises(ValueError):
        registry.create_team("alpha")
