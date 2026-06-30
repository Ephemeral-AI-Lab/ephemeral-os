# Experiment Prompt

Use `README.md` in this directory as reference material for the problem statement
and one existing proposal. Do not treat it as the only acceptable design. First
reason from the layerstack constraints, then produce a small set of candidate
proposals, benchmark them, and choose the best path for three-way merge,
line-level auditability, and storage efficiency.

At minimum, consider these designs, then add or remove candidates if the code and
measurements justify it:

1. Current full-file publish with source-path OCC.
2. Proposed three-way text merge that still publishes full-file concrete layers.
3. Full-file concrete layers plus provenance sidecars keyed by layer/path/digest.
4. Patch-backed storage with a materializer that creates overlay-compatible
   concrete cache layers.
5. A hybrid policy that stores full files by default and uses patch-backed
   storage only when measured thresholds justify it.

Optimize for the actual layerstack constraints:

- overlayfs receives `layer_paths` and mounts concrete lowerdirs
- `MergedView::read_entry` and projection resolve concrete files, symlinks,
  directories, and whiteouts
- publish must stay atomic: no partial changesets, no visible sidecar without
  the matching layer, no manifest update before all staged data is valid
- provenance must identify whether each final text line came from `original`,
  `workspace_session:<id>`, `mixed`, or `unknown`

Define benchmark cases that cover:

- small text files with tiny edits
- large text files with one-line edits
- large text files with scattered non-overlapping edits
- repeated edits to the same large file across many sessions
- many small files edited in one publish
- mixed source and ignored changes
- overlapping text edits that must reject
- binary, invalid UTF-8, minified, and generated files
- deep manifests with many layers
- hot cache, cold cache, and active leases that keep materialized caches alive

For each case, record:

- committed layer bytes
- provenance metadata bytes
- materialized cache bytes
- total bytes while active leases exist
- total bytes after cache cleanup
- publish latency
- snapshot acquisition/remount latency
- read/projection latency
- provenance query latency
- conflict/rejection correctness
- attribution correctness for line provenance

State hypotheses before proposing implementation:

- Full-file concrete publish should be fastest for hot workspace startup because
  overlayfs can mount committed layer dirs directly.
- Patch-backed storage should save cold historical disk only when files are
  large, edits are small, and materialized caches can be deleted or reused.
- Patch-backed storage may increase active disk use because the system stores
  both patches and materialized concrete cache layers.
- Provenance sidecars should add small write/read overhead for text files and no
  overhead for binary files skipped as `unknown`.
- A hybrid threshold may beat pure patch storage if it avoids materializing small
  or high-churn files.

Deliverables:

- candidate proposals with explicit tradeoffs before benchmarking
- benchmark matrix with inputs, expected output, and metrics
- implementation sketch for a minimal benchmark harness
- pass/fail criteria for choosing full-file, patch-backed, or hybrid storage
- risks and invalidation criteria for each hypothesis
- recommendation format that names the winning design and the evidence required
  to revisit it
