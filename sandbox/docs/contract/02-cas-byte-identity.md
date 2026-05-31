# 02 — CAS Byte-Identity Contract (AV-1c)

**Frozen contract for the two persisted content-addressed hashes the Rust runtime
MUST reproduce byte-for-byte.** This is the AV-1c bar from the migration plan
(`docs/plans/sandbox-rust-external-migration-PLAN.md` §4): a *byte-identity* bar
(not the looser AV-1 canonical-equal bar) for exactly two hashes:

1. `manifest_root_hash` — the manifest's root-view identity hash.
2. `layer_digest` — the per-layer change-set digest that drives head-layer dedup,
   is persisted to the `.digest` sidecar, and is read back across publishes.

Golden fixtures live at
`sandbox/crates/eos-protocol/fixtures/cas/cases.json` (18 cases). The exact
generator is reproduced at the end of this doc and at
`sandbox/docs/contract/_gen_cas_fixtures.py`.

All hashes are **SHA-256, hex (lowercase) `hexdigest()`**. Both algorithms are
**deterministic and platform-independent** (no floats, no `repr`, no locale).

Source anchors (verified against checkout at branch `main`, commit `f0c70b165`):
- `backend/src/sandbox/layer_stack/manifest.py`
- `backend/src/sandbox/layer_stack/changes.py`
- `backend/src/sandbox/layer_stack/publisher.py`

---

## 1. `manifest_root_hash` — manifest root identity

### Source (verified)

`manifest.py:134-138`:

```python
def manifest_root_hash(manifest: Manifest) -> str:
    """Return a stable identity hash for the manifest's root view."""
    payload = {"layers": [layer.to_dict() for layer in manifest.layers]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

`LayerRef.to_dict` (`manifest.py:64-65`):

```python
def to_dict(self) -> dict[str, str]:
    return {"layer_id": self.layer_id, "path": self.path}
```

### Algorithm (Rust MUST reproduce exactly)

1. Build a JSON object with a **single key** `"layers"`, whose value is a JSON
   **array** of objects, **one per layer in `manifest.layers` IN GIVEN ORDER**.
2. Each layer object has EXACTLY two string keys: `"layer_id"` and `"path"`
   (string→string; `LayerRef.to_dict` emits nothing else).
   - **`version` and `schema_version` are NOT in the hashed payload.** Only the
     `{"layers": [...]}` envelope is hashed (`manifest.py:136`). The on-disk
     manifest (`Manifest.to_dict`, `manifest.py:91-96`) DOES contain
     `schema_version` + `version` + `layers`, but `manifest_root_hash` does NOT
     hash that form. See §4 for the schema-version binding.
3. Serialize with the EXACT equivalent of
   `json.dumps(payload, sort_keys=True, separators=(",", ":"))`:
   - **Key separator `:`**, **item separator `,`** — NO spaces anywhere
     (compact form).
   - **`sort_keys=True`**: dict keys sorted (so the two-key layer object always
     emits `layer_id` before `path`; UTF-8/code-point key order). The top-level
     object has only one key so sorting is a no-op there.
   - **`ensure_ascii=True` (THE DEFAULT — `manifest.py:137` does NOT pass
     `ensure_ascii=False`).** This is the single most dangerous divergence point:
     **all non-ASCII characters in `layer_id` / `path` are escaped to `\uXXXX`
     BEFORE the `.encode("utf-8")`.** Non-BMP code points (e.g. emoji) are escaped
     as a **UTF-16 surrogate pair** `\udXXX\udXXX`. The Rust serializer MUST
     produce ASCII-escaped JSON for this hash (NOT raw UTF-8). This is the
     OPPOSITE of `layer_digest`, which hashes raw UTF-8 path bytes (§2).
4. `.encode("utf-8")` the resulting (now pure-ASCII) string.
5. `sha256(...).hexdigest()`.

### Literal serialized strings (ground truth, from generator)

| case | literal `json.dumps` output | sha256 hexdigest |
| --- | --- | --- |
| empty | `{"layers":[]}` | `c824a9aa7d2e3471306648c6d4baa1abbcb97ff0276181ab4722ca27127cdba0` |
| single | `{"layers":[{"layer_id":"L000001-aaaa0000","path":"layers/L000001-aaaa0000"}]}` | `3e1670346e0e5e76eb86b590cbde637731fe206735e054eb844f9dd783b51edb` |
| order A | `{"layers":[{"layer_id":"L000002-bbbb1111","path":"layers/L000002-bbbb1111"},{"layer_id":"L000001-aaaa0000","path":"layers/L000001-aaaa0000"}]}` | `b5bfdf4ff16d2105cbaa27ab8f55dff2a4b8d4d42367d8e57807a4c3d57fc25a` |
| order B (reversed) | `{"layers":[{"layer_id":"L000001-aaaa0000","path":"layers/L000001-aaaa0000"},{"layer_id":"L000002-bbbb1111","path":"layers/L000002-bbbb1111"}]}` | `a5c82146e1bb3c38ff74b6902c9c535419061f32d95d33043c5186059c076d57` |
| unicode BMP (`café`) | `{"layers":[{"layer_id":"Lunicodé","path":"layers/café"}]}` | `b3d7d6503a80f84e18530180448b8b5efdf20c8e7ef10ec50546128b078e0136` |
| unicode non-BMP (`🚀`) | `{"layers":[{"layer_id":"Lrocket","path":"layers/🚀"}]}` | `91a70772ce3bfe23c287766edc1c0d935b70c0b522e1f11f4cf4fe22b9c9df70` |

Note the `é` (é) and surrogate pair `🚀` (🚀) in the literals —
this is the `ensure_ascii=True` escaping in action.

### Ordering: `manifest_root_hash` is ORDER-SENSITIVE on the given layer list

The payload is `[layer.to_dict() for layer in manifest.layers]` — a list
comprehension over the layers tuple **in given order**. There is **no list
sort** (`sort_keys` only sorts dict *keys*, not array *elements*). So the SAME
two layers in two different orders produce DIFFERENT root hashes (verified:
`manifest_multi_order_A` `b5bfdf4f...` != `manifest_multi_order_B` `a5c82146...`).

Rust MUST hash layers in the order they appear in the manifest, with no
reordering. (In the real publisher, the newest layer is prepended:
`publisher.py:112-118` builds `(new_layer, *active.layers)`, so order is
newest-first — but `manifest_root_hash` just hashes whatever order it is given.)

---

## 2. `layer_digest` — per-layer change-set digest

### Source (verified)

The digest is finalized in `_prepare_changes` (`publisher.py:144-158`):

```python
def _prepare_changes(changes, *, source_root=None):
    digest = hashlib.sha256()
    resolved_source_root = (Path(source_root).resolve(strict=True) if source_root is not None else None)
    prepared = []
    for change in aggregate_layer_changes(changes):
        prepared_change = prepare_layer_change(change, source_root=resolved_source_root)
        update_digest(digest, prepared_change)
        prepared.append(prepared_change)
    return tuple(prepared), digest.hexdigest()
```

- **Hash algorithm: `hashlib.sha256()`, `.hexdigest()`** (`publisher.py:149,158`).
  CONFIRMED sha256 — same as manifest.
- The digest is computed over `aggregate_layer_changes(changes)` (the
  collapsed/sorted set), one `update_digest` call per change, in that order.

`update_digest` (`changes.py:145-157`):

```python
def update_digest(digest: DigestSink, prepared: PreparedLayerChange) -> None:
    c = prepared.change
    digest.update(c.kind.encode("utf-8"))
    digest.update(b"\0")
    digest.update(c.path.encode("utf-8"))
    digest.update(b"\0")
    if c.kind == "write":
        assert prepared.write_content is not None
        digest.update(prepared.write_content)
    elif c.kind == "symlink":
        assert c.source_path is not None
        digest.update(c.source_path.encode("utf-8"))
    digest.update(b"\0")
```

`aggregate_layer_changes` (`changes.py:160-165`):

```python
def aggregate_layer_changes(changes):
    final_by_path: dict[str, LayerChange] = {}
    for change in changes:
        final_by_path[change.path] = change
    return tuple(final_by_path[path] for path in sorted(final_by_path))
```

### Algorithm (Rust MUST reproduce exactly)

**Step A — aggregate (last-write-wins + sort):**
1. Walk the input changes IN ORDER, keeping a map `path -> change`. Each change
   for a given `path` OVERWRITES the previous (**last-write-wins**, INCLUDING
   across different kinds — a later delete to a path that had a write wins, etc.;
   the map is keyed by `path` only).
2. Emit the surviving changes sorted by `path`. Python uses `sorted(final_by_path)`
   = ascending by Unicode **code point**. Rust `str` `Ord` is UTF-8 **byte order**,
   which agrees with code-point order for all valid strings (UTF-8 preserves
   code-point ordering) — so a plain `Vec<...>.sort_by_key(|c| c.path.clone())`
   (or sort on `&str`) is correct. Do NOT use any locale/collation-aware sort.

   > `path` here is the post-`normalize_layer_path` form (`changes.py:27-40`,
   > applied in `LayerChange.__post_init__`). Path normalization is a SEPARATE
   > contract surface; these fixtures store already-normalized paths. The digest
   > hashes whatever the normalized `path` string is.

**Step B — digest each surviving change, in the sorted order from Step A**, by
appending these bytes to one running SHA-256:

```
kind_bytes        = kind.encode("utf-8")        # "write"|"delete"|"symlink"|"opaque_dir"
                    + b"\0"
path_bytes        = path.encode("utf-8")         # RAW UTF-8, NO \u escaping
                    + b"\0"
payload:
    if kind == "write":     write_content        # raw bytes, verbatim, may be empty / contain \0 / non-UTF8
    elif kind == "symlink": source_path.encode("utf-8")   # RAW UTF-8
    else (delete|opaque_dir): (nothing)
                    + b"\0"                        # ALWAYS one trailing NUL, even when payload is empty
```

So the per-change byte frame is:
`kind ‖ \0 ‖ path ‖ \0 ‖ <payload-or-nothing> ‖ \0`.

Critical details:
- **`write_content` is hashed RAW and verbatim** for write kind. It may be empty
  (zero bytes between the two trailing NULs), contain `\0` bytes, or be arbitrary
  non-UTF-8 binary. NO length prefix, NO escaping, NO NUL-termination of the
  content itself — the framing NUL comes after it unconditionally.
- **`source_path` is hashed (raw UTF-8) ONLY for symlink kind.** For `write`,
  `source_path` is the on-disk staging source and is **NOT part of the digest**
  (the file's *content* is hashed via `write_content`, not its source path). For
  `delete`/`opaque_dir`, `source_path` is always `None` and nothing is appended.
- **`path` uses RAW UTF-8** (`path.encode("utf-8")`) — **NO `\uXXXX` escaping.**
  This is the OPPOSITE of `manifest_root_hash`. A unicode path like `docs/café/🚀.md`
  contributes its raw UTF-8 bytes here, but its `\u`-escaped form in the manifest.
- **There is ALWAYS a trailing `\0`** after the payload position, even for
  delete/opaque_dir where the payload is empty (so a delete frame is
  `kind ‖ \0 ‖ path ‖ \0 ‖ \0`).
- **Framing assumption: paths are NUL-free.** The `\0` delimiters are
  collision-safe only because `path` (and `kind`, which is a fixed enum)
  contain no NUL. This holds by construction — filesystem paths cannot contain
  NUL and `LayerRef`/`normalize_layer_path` reject NUL explicitly
  (`manifest.py:56-57`, and `changes.py` normalization). `write_content` MAY
  contain NUL (it is binary), but it is the LAST field before the trailing
  delimiter so no ambiguity arises. Rust need not handle NUL-in-path (it is
  unreachable), but must not add length prefixes that would change the bytes.

### Per-frame verification (hand-rolled mirror == real function)

These independent hand-rolled byte streams were confirmed to equal the real
`update_digest` outputs (single-change cases):

| kind | byte stream | sha256 |
| --- | --- | --- |
| write | `b"write\0" + b"src/a.txt\0" + b"hello world" + b"\0"` | `b5ac9bc8f553fca336579381e2efe24dce947768aba9ccec3e64c577fc25bb5e` |
| symlink | `b"symlink\0" + b"link\0" + b"target/dir/file" + b"\0"` | `8808574099329e761b44eece8a7b716a67aa4ca07ddd054d580642b6f3db9984` |
| delete | `b"delete\0" + b"gone.txt\0" + b"\0"` | `3c4d600a083cd67d94b6bfc19c99bba02e55bcd5186af2d095029b88b837d018` |

### Ordering: `layer_digest` is INPUT-ORDER-INSENSITIVE (aggregate sorts)

Because Step A sorts surviving changes by path, the SAME set of changes given in
two different input orders yields the SAME digest (verified:
`digest_ordering_unsorted_input` == `digest_ordering_reversed_input` ==
`e734164d...`). This is the OPPOSITE of `manifest_root_hash` (order-sensitive).
The fixtures store the UNSORTED given order in `input.changes` and the resulting
sorted order in `aggregated_order`, forcing the Rust port to replicate the
aggregate+sort, not just hash the input order.

Last-write-wins is also fixture-locked (`digest_last_write_wins`: two writes to
`x.txt` → only `second-wins` content survives → `195bf60a...`).

---

## 3. How `layer_digest` is used (dedup + persistence)

(Context for AV-7 / AV-1c — the Rust runtime must persist and read back this
digest identically.)

- **Head-layer dedup decision** (`publisher.py:71-80`): after computing
  `layer_digest`, the publisher compares it to `_head_layer_digest(...)`. If
  equal, the publish is a no-op (returns the active manifest unchanged) — NO new
  layer. A byte-divergent Rust digest would silently publish a duplicate layer
  (the AV-7 silent-divergence risk).
- **Head digest read** (`publisher.py:171-179`): `_head_layer_digest` reads the
  newest layer's sidecar via `layer_digest_path(storage_root, active.layers[0].layer_id)`
  with `.read_text(encoding="utf-8")`; returns `None` on `OSError` or empty layers.
- **Persistence** (`publisher.py:106`): `write_layer_digest_atomic(storage_root, layer_id, layer_digest)`
  writes the hex string (UTF-8) to `<storage_root>/.layer-metadata/<layer_id>.digest`
  (`manifest.py:29-43`), fsync'd. The sidecar content is exactly the 64-char hex
  `hexdigest` string (no newline, no trailing whitespace — `digest.encode("utf-8")`,
  `manifest.py:42`).
- **EXDEV constraint** (`publisher.py:104`): `os.replace(staging_dir, layer_dir)`
  raises `EXDEV` if staging and layers dirs are on different filesystems — the
  storage root + staging must be one filesystem (CP-1b item (ii)). This is an
  operational constraint, not part of the hash.

### Anchor verification (full publisher round-trip)

The fixture `digest_publisher_anchor` was produced by running the FULL
production `LayerPublisher.publish_layer` in a tmpdir with real on-disk source
files, then reading back the persisted `.digest` sidecar. All three independently
computed values were EQUAL:

```
_prepare_changes(changes, source_root=...)  -> 1d84a47b1812ed06e09840099db88ae72b8077a89d719e1c02922f8396cac340
update_digest reconstruction                -> 1d84a47b1812ed06e09840099db88ae72b8077a89d719e1c02922f8396cac340
persisted .digest sidecar (read back)       -> 1d84a47b1812ed06e09840099db88ae72b8077a89d719e1c02922f8396cac340
all_equal = True
```

This closes the loop: the documented `update_digest` byte stream IS the
production digest, and it is what gets persisted and read back. (The anchor's
write content `b"anchor content \x00\xff bytes"` includes a NUL and a high byte,
proving raw binary passthrough through the real disk read path.)

---

## 4. Schema-version coordinated-bump binding

- **`MANIFEST_SCHEMA_VERSION = 1`** (`manifest.py:22`). The current and only
  supported on-disk manifest schema.
- **Root hash is intentionally schema-version-INVARIANT.** `manifest_root_hash`
  hashes ONLY `{"layers": [...]}` (no `version`, no `schema_version`,
  `manifest.py:136`). So a schema bump that changes the on-disk envelope does NOT
  change the root identity hash for the same set of layers. Rust MUST keep the
  hashed payload to `{"layers": [...]}` only.
- **The version gate lives in two places — Rust MUST reject identically:**
  - `Manifest.__post_init__` (`manifest.py:78-82`): raises `ManifestConflictError`
    if `schema_version != MANIFEST_SCHEMA_VERSION` (1) at construction.
  - `Manifest.from_dict` (`manifest.py:107-114`): reads `schema_version`
    (defaulting to 1 if absent), raises `ManifestConflictError` if
    `schema_version > MANIFEST_SCHEMA_VERSION` ("newer than this runtime
    supports") OR `schema_version != MANIFEST_SCHEMA_VERSION` ("unsupported").
    Also requires both `version` and `layers` keys present (`manifest.py:103-106`),
    treating a torn write that lost `layers` as corruption, not an empty manifest.
- **On-disk manifest is parsed, NOT hashed** (`write_manifest_atomic`,
  `manifest.py:155-157`: `json.dumps(manifest.to_dict(), indent=2, sort_keys=True)`).
  Because it is re-parsed on read (`read_manifest`, `manifest.py:145-152`), the
  on-disk byte form is held only to the **AV-1 canonical-equal** bar, NOT
  byte-identity. Rust may write a byte-different but JSON-equal manifest file.
  Only `manifest_root_hash` and `layer_digest` are byte-identity (AV-1c).
- **Coordinated-bump rule:** bumping `MANIFEST_SCHEMA_VERSION` (or any change to
  the `manifest_root_hash` / `update_digest` byte stream) is a CAS-format break.
  It requires a coordinated Python+Rust release and a fixture regeneration
  (`cases.json`), per the plan's `_eos_daemon_protocol_version` /
  `CONTRACT.md` coordinated-bump procedure (§1, §2 of the plan). The root-hash
  payload shape (`{"layers":...}`) and the digest byte frame are the frozen
  surface; changing either invalidates every golden hash below.

---

## 5. Golden fixtures

File: `sandbox/crates/eos-protocol/fixtures/cas/cases.json` — JSON array of 18
cases. Each case:

```json
{
  "name": "<unique>",
  "kind": "manifest_root_hash" | "layer_digest",
  "input": { ... },
  "expected": "<64-char lowercase sha256 hex>"
}
```

- `manifest_root_hash` cases: `input.layers = [{"layer_id", "path"}, ...]` in
  GIVEN order; also carry `json_dumps_literal` (the exact serialized string, for
  debugging the `ensure_ascii` escaping).
- `layer_digest` cases: `input.changes = [{kind, path, [source_path],
  [write_content_b64]}, ...]` in GIVEN (unsorted) order; also carry
  `aggregated_order` (the sorted post-aggregate path order Rust must produce).
  **`write_content_b64` is base64 of the raw `write_content` bytes** (JSON cannot
  hold raw bytes); a Rust test base64-decodes it to reconstruct the exact bytes,
  including empty / NUL / high-byte content. For write cases `source_path` is
  present (required by `LayerChange` construction) but is NOT hashed and may be
  ignored by the Rust digest. For symlink cases `source_path` IS hashed.

### Computed golden digests (all 18)

**manifest_root_hash:**
| name | expected |
| --- | --- |
| `manifest_empty` | `c824a9aa7d2e3471306648c6d4baa1abbcb97ff0276181ab4722ca27127cdba0` |
| `manifest_single` | `3e1670346e0e5e76eb86b590cbde637731fe206735e054eb844f9dd783b51edb` |
| `manifest_multi_order_A` | `b5bfdf4ff16d2105cbaa27ab8f55dff2a4b8d4d42367d8e57807a4c3d57fc25a` |
| `manifest_multi_order_B` | `a5c82146e1bb3c38ff74b6902c9c535419061f32d95d33043c5186059c076d57` |
| `manifest_unicode_bmp` | `b3d7d6503a80f84e18530180448b8b5efdf20c8e7ef10ec50546128b078e0136` |
| `manifest_unicode_nonbmp` | `91a70772ce3bfe23c287766edc1c0d935b70c0b522e1f11f4cf4fe22b9c9df70` |

**layer_digest:**
| name | expected |
| --- | --- |
| `digest_write_basic` | `b5ac9bc8f553fca336579381e2efe24dce947768aba9ccec3e64c577fc25bb5e` |
| `digest_write_empty` | `2ce7db542fdce312d61e56ad25037c04ef409f115e8d1d83ead6ab5f8840d22a` |
| `digest_write_binary` | `cde2c8c0a5e9f2d866066f5adb5a1c1d1d9ea23b61915bbab78fef4b7454b806` |
| `digest_write_unicode_path` | `ab2402a752d60dfba92ad5fae5abde676466d79b142a756ec811de21a07ea1a8` |
| `digest_delete` | `3c4d600a083cd67d94b6bfc19c99bba02e55bcd5186af2d095029b88b837d018` |
| `digest_symlink` | `8808574099329e761b44eece8a7b716a67aa4ca07ddd054d580642b6f3db9984` |
| `digest_opaque_dir` | `957377f13cfe6e48935c19335047fa7ff5e01094db8ae7a813e1da61b85789e9` |
| `digest_last_write_wins` | `195bf60a0171f02bce8b3b463dd65c2fa3db939a1a23017d937827be8fd88238` |
| `digest_ordering_unsorted_input` | `e734164d4a7b2699a5f72b9236c73e453dc40d0d65bdff99cecf643c6409ee8c` |
| `digest_ordering_reversed_input` | `e734164d4a7b2699a5f72b9236c73e453dc40d0d65bdff99cecf643c6409ee8c` |
| `digest_mixed_kinds` | `25a4f0feaaff9e65b9028d875a68bf798448f018a45ad82edf3e8ae4a90d7df1` |
| `digest_publisher_anchor` | `1d84a47b1812ed06e09840099db88ae72b8077a89d719e1c02922f8396cac340` |

---

## 6. Reproducible generation snippet

Run from `backend/`:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend
uv run python ../sandbox/docs/contract/_gen_cas_fixtures.py
```

The full generator is at `sandbox/docs/contract/_gen_cas_fixtures.py`. It imports
and calls the REAL functions (`manifest_root_hash`, `aggregate_layer_changes`,
`update_digest`, `_prepare_changes`, `LayerPublisher.publish_layer`) — no
hand-rolled byte-stream mirror produces any `expected` value. The publisher
anchor asserts `_prepare_changes == update_digest reconstruction == persisted
sidecar` at runtime, so the script fails loudly if the documented algorithm ever
drifts from production.

Minimal standalone re-derivation of either hash (for a Rust cross-check):

```python
import hashlib, json, base64

def manifest_root_hash(layers):  # layers: list[(layer_id, path)]
    payload = {"layers": [{"layer_id": lid, "path": p} for lid, p in layers]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def layer_digest(changes):  # changes: list[dict] in GIVEN order
    final = {}
    for c in changes:
        final[c["path"]] = c
    d = hashlib.sha256()
    for path in sorted(final):
        c = final[path]
        d.update(c["kind"].encode("utf-8")); d.update(b"\0")
        d.update(path.encode("utf-8")); d.update(b"\0")
        if c["kind"] == "write":
            d.update(base64.b64decode(c["write_content_b64"]))
        elif c["kind"] == "symlink":
            d.update(c["source_path"].encode("utf-8"))
        d.update(b"\0")
    return d.hexdigest()
```
