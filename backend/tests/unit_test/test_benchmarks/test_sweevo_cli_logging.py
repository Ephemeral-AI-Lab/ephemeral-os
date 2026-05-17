from __future__ import annotations

import logging

from benchmarks.sweevo.__main__ import _build_parser, _configure_logging


def test_configure_logging_suppresses_warning_and_below() -> None:
    previous_disable_level = logging.root.manager.disable
    try:
        logging.disable(logging.NOTSET)
        _configure_logging()
        assert not logging.getLogger("benchmarks.sweevo").isEnabledFor(logging.DEBUG)
        assert not logging.getLogger("benchmarks.sweevo").isEnabledFor(logging.INFO)
        assert not logging.getLogger("benchmarks.sweevo").isEnabledFor(logging.WARNING)
        assert logging.getLogger("benchmarks.sweevo").isEnabledFor(logging.ERROR)
    finally:
        logging.disable(previous_disable_level)


def test_sweevo_cli_exposes_scenario_and_real_agent_flags() -> None:
    parser = _build_parser()
    list_args = parser.parse_args(["--list"])
    assert list_args.list is True
    real_args = parser.parse_args(["--real-agent"])
    assert real_args.real_agent is True
    scen_args = parser.parse_args(["--scenario", "correctness_testing"])
    assert scen_args.scenario == "correctness_testing"
