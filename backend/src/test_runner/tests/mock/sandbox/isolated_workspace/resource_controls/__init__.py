"""Tier 5 — resource controls (PLAN §5 / §19 / NEXT-AGENT-GUIDE phase 7).

Per-agent quota, global cap, host-RAM gate, TTL evict, and ENOSPC backpressure.
Every test in this directory is live-CI gated (requires sweevo Docker +
``runner.live_e2e.heavy_enabled``).
"""
