"""Mock-tier tests for the daemon-native isolated_workspace feature.

This directory is the single home for every property the
``enter_isolated_workspace`` design protects. See PLAN.md §0 for the rationale.

Layout (tier-named subdirectories):
    pre_flight/        Tier 0 — platform-agnostic AST/import fences
    happy_path/        Tier 1 — golden enter / shell / exit scenarios (Docker)
    isolation/         Tier 2 — OCC separation, peer-publish pinning, port iso
    network/           Tier 3 — egress / IMDS / DNS / inbound rejection
    failure_modes/     Tier 4 — partial-rollback, timeouts, freezer fallback
    resource_controls/ Tier 5 — quota / TTL / RAM gate / ENOSPC backpressure
    concurrency/       Tier 6 — races and locks at N=5
    gc_and_persistence/Tier 7 — daemon restart, manager.json, GC ordering
    stress/            Tier 8 — slow-marked soak and full-stack scenarios
    performance/       Tier 9 — phase-timing assertions vs hybrid baseline
"""
