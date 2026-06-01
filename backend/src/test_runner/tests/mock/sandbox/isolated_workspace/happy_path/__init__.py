"""Tier 1 — golden enter/shell/exit scenarios.

Each test boots a real sweevo Docker sandbox via ``iws_sandbox`` and drives
``call_daemon_api`` through :mod:`_iws_rpc`. The tests skip on non-Linux hosts
and when ``live_e2e_heavy_enabled()`` is off.
"""
