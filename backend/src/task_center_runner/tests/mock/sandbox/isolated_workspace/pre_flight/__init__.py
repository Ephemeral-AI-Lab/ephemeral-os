"""Tier 0 — structural fences.

These tests are platform-agnostic AST/import walks. They live alongside the
Linux-only live tests so the test directory tells the whole story, but they
deliberately have zero Docker / kernel dependencies and run in <100 ms on any
host. They are the first gate before any production change to
``isolated_workspace_ops`` lands.
"""
