"""Generate golden CAS fixtures by calling the REAL Python sandbox functions.

Run from backend/: cd backend && uv run python ../sandbox/docs/contract/_gen_cas_fixtures.py
Writes: sandbox/crates/eos-protocol/fixtures/cas/cases.json
Also prints diagnostic ground-truth (literal json.dumps strings, publisher anchor).

Every `expected` value comes from importing and calling the real functions:
  - manifest.manifest_root_hash
  - changes.aggregate_layer_changes + changes.update_digest
  - publisher._prepare_changes (the production digest path) + a full publish round-trip
No hand-rolled byte-stream mirror is used to produce any `expected`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from pathlib import Path

from sandbox.layer_stack.manifest import (
    LayerRef,
    Manifest,
    manifest_root_hash,
    read_manifest,
    empty_manifest,
    layer_digest_path,
)
from sandbox.layer_stack.changes import (
    LayerChange,
    PreparedLayerChange,
    WriteLayerChange,
    DeleteLayerChange,
    SymlinkLayerChange,
    OpaqueDirLayerChange,
    aggregate_layer_changes,
    update_digest,
)
from sandbox.layer_stack.publisher import LayerPublisher, _prepare_changes


# ---------------------------------------------------------------------------
# manifest_root_hash cases
# ---------------------------------------------------------------------------

def manifest_literal(layers: list[tuple[str, str]]) -> str:
    """Capture the literal json.dumps string the hash is computed over (diagnostic)."""
    payload = {"layers": [{"layer_id": lid, "path": p} for lid, p in layers]}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def mk_manifest(layers: list[tuple[str, str]]) -> Manifest:
    return Manifest(
        version=len(layers),
        layers=tuple(LayerRef(layer_id=lid, path=p) for lid, p in layers),
    )


cases: list[dict] = []


def add_manifest_case(name: str, layers: list[tuple[str, str]]) -> None:
    m = mk_manifest(layers)
    expected = manifest_root_hash(m)
    cases.append(
        {
            "name": name,
            "kind": "manifest_root_hash",
            "input": {"layers": [{"layer_id": lid, "path": p} for lid, p in layers]},
            "json_dumps_literal": manifest_literal(layers),
            "expected": expected,
        }
    )


# empty manifest
add_manifest_case("manifest_empty", [])
# single layer
add_manifest_case("manifest_single", [("L000001-aaaa0000", "layers/L000001-aaaa0000")])
# multi-layer given in a specific order
add_manifest_case(
    "manifest_multi_order_A",
    [
        ("L000002-bbbb1111", "layers/L000002-bbbb1111"),
        ("L000001-aaaa0000", "layers/L000001-aaaa0000"),
    ],
)
# SAME layers, reversed given order -> order-SENSITIVE -> different hash expected
add_manifest_case(
    "manifest_multi_order_B",
    [
        ("L000001-aaaa0000", "layers/L000001-aaaa0000"),
        ("L000002-bbbb1111", "layers/L000002-bbbb1111"),
    ],
)
# non-ASCII (BMP) layer path -> json.dumps ensure_ascii=True escapes to \uXXXX
add_manifest_case("manifest_unicode_bmp", [("Lunicodé", "layers/café")])
# non-BMP (emoji) -> surrogate-pair escaping \udXXX\udXXX
add_manifest_case("manifest_unicode_nonbmp", [("Lrocket", "layers/🚀")])


# ---------------------------------------------------------------------------
# layer_digest cases (built from REAL PreparedLayerChange via update_digest,
# routed through aggregate_layer_changes so ordering is exercised)
# ---------------------------------------------------------------------------

def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def change_to_input(change: LayerChange, write_content: bytes | None) -> dict:
    d: dict = {"kind": change.kind, "path": change.path}
    if change.source_path is not None:
        d["source_path"] = change.source_path
    if change.kind == "write":
        # write_content is what update_digest hashes (raw bytes), stored b64
        d["write_content_b64"] = b64(write_content if write_content is not None else b"")
    return d


def compute_layer_digest(
    items: list[tuple[LayerChange, bytes | None]],
) -> tuple[str, list[LayerChange]]:
    """Replicate _prepare_changes' digest path EXACTLY using the real functions.

    _prepare_changes does: digest=sha256(); for change in aggregate_layer_changes(changes):
       prepared = prepare_layer_change(...); update_digest(digest, prepared)
    Here we bypass prepare_layer_change's disk read by constructing PreparedLayerChange
    directly with the same write_content, then call the REAL update_digest + REAL
    aggregate_layer_changes. The byte stream is therefore produced by production code.
    """
    changes = [c for c, _ in items]
    content_by_id = {id(c): wc for c, wc in items}
    aggregated = aggregate_layer_changes(changes)
    digest = hashlib.sha256()
    for c in aggregated:
        wc = content_by_id[id(c)] if c.kind == "write" else None
        prepared = PreparedLayerChange(change=c, write_content=wc)
        update_digest(digest, prepared)
    return digest.hexdigest(), list(aggregated)


def add_digest_case(name: str, items: list[tuple[LayerChange, bytes | None]]) -> None:
    expected, aggregated = compute_layer_digest(items)
    # Preserve GIVEN (unsorted) input order so Rust must replicate aggregate+sort.
    content_by_id = {id(c): wc for c, wc in items}
    given = [
        change_to_input(c, content_by_id[id(c)] if c.kind == "write" else None)
        for c, _ in items
    ]
    aggregated_paths = [c.path for c in aggregated]
    cases.append(
        {
            "name": name,
            "kind": "layer_digest",
            "input": {"changes": given},
            "aggregated_order": aggregated_paths,
            "expected": expected,
        }
    )


# NOTE: LayerChange.__post_init__ requires source_path for write kind, BUT
# update_digest for a write hashes ONLY kind+path+write_content (NOT source_path).
# We pass a placeholder source_path purely to satisfy construction; it does not
# enter the digest. Verified by the publisher anchor below.
_SRC = "/placeholder/source"

# write with normal content
add_digest_case(
    "digest_write_basic",
    [(WriteLayerChange(path="src/a.txt", source_path=_SRC), b"hello world")],
)
# write with EMPTY content
add_digest_case(
    "digest_write_empty",
    [(WriteLayerChange(path="empty.txt", source_path=_SRC), b"")],
)
# write with binary / non-utf8-safe content incl NUL byte (latin-1 high bytes)
add_digest_case(
    "digest_write_binary",
    [(WriteLayerChange(path="bin.dat", source_path=_SRC), bytes([0x00, 0xFF, 0xFE, 0x80, 0x01, 0x00, 0x7F]))],
)
# write with unicode path (path.encode utf-8 -> raw bytes, NO \u escaping)
add_digest_case(
    "digest_write_unicode_path",
    [(WriteLayerChange(path="docs/café/🚀.md", source_path=_SRC), b"unicode path content")],
)
# delete
add_digest_case("digest_delete", [(DeleteLayerChange(path="gone.txt"), None)])
# symlink (source_path hashed, no content)
add_digest_case(
    "digest_symlink",
    [(SymlinkLayerChange(path="link", source_path="target/dir/file"), None)],
)
# opaque_dir
add_digest_case("digest_opaque_dir", [(OpaqueDirLayerChange(path="cleared/dir"), None)])
# same-path last-write-wins: two writes to same path -> only the LAST survives
add_digest_case(
    "digest_last_write_wins",
    [
        (WriteLayerChange(path="x.txt", source_path=_SRC), b"first"),
        (WriteLayerChange(path="x.txt", source_path=_SRC), b"second-wins"),
    ],
)
# ordering: distinct paths given UNSORTED -> aggregate emits in sorted(path) order
add_digest_case(
    "digest_ordering_unsorted_input",
    [
        (WriteLayerChange(path="zeta.txt", source_path=_SRC), b"z"),
        (WriteLayerChange(path="alpha.txt", source_path=_SRC), b"a"),
        (WriteLayerChange(path="mid.txt", source_path=_SRC), b"m"),
    ],
)
# ordering: same set, DIFFERENT input order -> SAME digest (order-insensitive)
add_digest_case(
    "digest_ordering_reversed_input",
    [
        (WriteLayerChange(path="alpha.txt", source_path=_SRC), b"a"),
        (WriteLayerChange(path="mid.txt", source_path=_SRC), b"m"),
        (WriteLayerChange(path="zeta.txt", source_path=_SRC), b"z"),
    ],
)
# mixed kinds in one change-set
add_digest_case(
    "digest_mixed_kinds",
    [
        (WriteLayerChange(path="w.txt", source_path=_SRC), b"W"),
        (DeleteLayerChange(path="d.txt"), None),
        (SymlinkLayerChange(path="s", source_path="dst"), None),
        (OpaqueDirLayerChange(path="o"), None),
    ],
)


# ---------------------------------------------------------------------------
# Cross-check: prove update_digest path == real _prepare_changes path AND a
# full LayerPublisher.publish_layer round-trip persists the same digest.
# ---------------------------------------------------------------------------

def anchor_via_publisher() -> dict:
    """Run the FULL production publisher and read back the persisted .digest sidecar."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        storage_root = tmp_path / "storage"
        storage_root.mkdir()
        source_root = tmp_path / "source"
        source_root.mkdir()
        # Real write source file on disk (publisher reads it).
        content = b"anchor content \x00\xff bytes"
        src_file = source_root / "anchor.txt"
        src_file.write_bytes(content)

        changes = [
            WriteLayerChange(path="anchor.txt", source_path=str(src_file)),
            DeleteLayerChange(path="removed.txt"),
        ]

        # 1) production _prepare_changes digest (reads source from disk)
        _, prep_digest = _prepare_changes(changes, source_root=source_root)

        # 2) our update_digest reconstruction
        recon_digest, _ = compute_layer_digest(
            [
                (WriteLayerChange(path="anchor.txt", source_path=str(src_file)), content),
                (DeleteLayerChange(path="removed.txt"), None),
            ]
        )

        # 3) full publish round-trip, read back persisted sidecar
        publisher = LayerPublisher(storage_root)
        active = read_manifest(storage_root / "manifest.json")  # empty
        new_manifest = publisher.publish_layer(
            changes, expected_manifest=active, source_root=source_root
        )
        layer_id = new_manifest.layers[0].layer_id
        persisted = layer_digest_path(storage_root, layer_id).read_text(encoding="utf-8")

        return {
            "prepare_changes_digest": prep_digest,
            "update_digest_recon": recon_digest,
            "persisted_sidecar": persisted,
            "all_equal": prep_digest == recon_digest == persisted,
        }


anchor = anchor_via_publisher()


# Add the anchor case as a fixture too (built via update_digest, content has NUL+high byte)
anchor_content = b"anchor content \x00\xff bytes"
add_digest_case(
    "digest_publisher_anchor",
    [
        (WriteLayerChange(path="anchor.txt", source_path="/tmp/anchor.txt"), anchor_content),
        (DeleteLayerChange(path="removed.txt"), None),
    ],
)
# Note: source_path in input is informational; update_digest does NOT hash source_path
# for write kind (only kind+path+write_content). Verify the anchor fixture expected
# equals the persisted sidecar:
assert cases[-1]["expected"] == anchor["persisted_sidecar"], (
    cases[-1]["expected"],
    anchor["persisted_sidecar"],
)


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

out_path = Path(
    "/Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox/crates/eos-protocol/fixtures/cas/cases.json"
)
out_path.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Diagnostics for the markdown
print("=== ANCHOR (publisher round-trip vs update_digest vs _prepare_changes) ===")
print(json.dumps(anchor, indent=2))
print()
print("=== MANIFEST json.dumps literals + digests ===")
for c in cases:
    if c["kind"] == "manifest_root_hash":
        print(f"{c['name']}: literal={c['json_dumps_literal']!r}")
        print(f"    contains_backslash_u={'\\u' in c['json_dumps_literal']}")
        print(f"    expected={c['expected']}")
print()
print("=== LAYER_DIGEST cases ===")
for c in cases:
    if c["kind"] == "layer_digest":
        print(f"{c['name']}: aggregated_order={c['aggregated_order']} expected={c['expected']}")
print()
print(f"order_sensitive_check: A != B = "
      f"{[c['expected'] for c in cases if c['name']=='manifest_multi_order_A'][0] != [c['expected'] for c in cases if c['name']=='manifest_multi_order_B'][0]}")
print(f"digest_order_insensitive_check: unsorted == reversed = "
      f"{[c['expected'] for c in cases if c['name']=='digest_ordering_unsorted_input'][0] == [c['expected'] for c in cases if c['name']=='digest_ordering_reversed_input'][0]}")
print(f"\nwrote {len(cases)} cases to {out_path}")
