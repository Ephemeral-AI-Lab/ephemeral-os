# Completion Contract

Use this reference only when `target_paths` is a single file or a short fixed file list.

## Rules

- Must treat the handed scope itself as the deliverable.
- Must end with raw JSON like `{"summary":"This file defines compatibility helpers.","artifact":{...}}`.
- If the draft is prose or lacks `artifact`, it is unfinished.
- If the final assistant message does not literally start with `{` and include `"artifact":`, rebuild it before replying.
- `artifact` must contain `target_paths`, `files_mapped`, `entry_points`, `suggested_subdivisions`, and `gaps`.
- For single-file or short fixed file-list scouts, `suggested_subdivisions` should usually be `[]`.
- Never subdivide a single file just because it is long; only name real seams the downstream planner should schedule.
- Never claim code was created, fixed, patched, or refactored.

## Few-shot examples

- Example: JSON like `{"summary":"Mapped cli helpers"}` is incomplete because it lacks `artifact`.
- Example:
  ```json
  {
    "summary": "This file defines compatibility helpers.",
    "artifact": {
      "target_paths": ["pkg/compat.py"],
      "files_mapped": ["pkg/compat.py"],
      "entry_points": ["is_py311", "import_optional_dependency"],
      "suggested_subdivisions": [],
      "gaps": []
    }
  }
  ```
  This is complete and reusable.
