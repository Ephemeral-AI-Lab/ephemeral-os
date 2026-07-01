"""Shared file-operation helpers for the runtime/file live e2e matrix.

Planned contents (see runtime/file/README.md):
- CLI wrappers: file_read / file_write / file_edit / file_blame, each taking an
  optional workspace_session_id and returning the parsed structured response.
- Blame assertions: assert_blame_tiling (ranges cover every line exactly once)
  and assert_blame_owners (expected owner per line/range), used by every test
  that publishes content per the standing correctness rule.
- Layerstack probes: manifest_version / layer-count snapshots around an
  operation, via `sandbox-cli observability layerstack`.
"""
