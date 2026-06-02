//! Deterministic, side-effect-free redaction/summary helpers (GC-audit-02).
//!
//! These mirror `engine/audit/stream.py`'s `_shape`/`_redacted_shape`/`_digest`/
//! `_encoded_size`/`_json_bytes` over a parsed [`JsonValue`]. The canonical byte
//! form (sorted keys, compact separators, `UTF-8` passthrough) is
//! parity-load-bearing: the same input always yields the same `*_shape`,
//! `digest`, and `encoded_size`, independent of map key-insertion order.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use eos_types::JsonValue;
use serde_json::Value;
use sha2::{Digest, Sha256};

const REDACTED: &str = "<redacted>";

/// Summarize a value's *structure* without leaking content.
///
/// Mirrors Python `_shape`: object → keys mapped to shaped values; array →
/// first 5 elements shaped; scalar → its Python type name (`"str"`, `"int"`,
/// `"float"`, `"bool"`, `"NoneType"`). Because `serde_json` has a single number
/// type, integers (`is_i64`/`is_u64`) map to `"int"` and everything else to
/// `"float"`, reproducing Python's int/float distinction.
pub(crate) fn shape(value: &JsonValue) -> JsonValue {
    match value {
        Value::Object(map) => {
            let shaped = map.iter().map(|(k, v)| (k.clone(), shape(v))).collect();
            Value::Object(shaped)
        }
        Value::Array(items) => Value::Array(items.iter().take(5).map(shape).collect()),
        Value::String(_) => Value::String("str".to_owned()),
        Value::Bool(_) => Value::String("bool".to_owned()),
        Value::Number(n) => {
            let name = if n.is_f64() { "float" } else { "int" };
            Value::String(name.to_owned())
        }
        Value::Null => Value::String("NoneType".to_owned()),
    }
}

/// Replace every leaf with `"<redacted>"`, keeping only the top-level shape.
///
/// Mirrors Python `_redacted_shape`: object → every key mapped to the redaction
/// marker (one level, not recursive); array → up to 5 markers; scalar → one
/// marker.
pub(crate) fn redacted_shape(value: &JsonValue) -> JsonValue {
    match value {
        Value::Object(map) => {
            let redacted = map
                .keys()
                .map(|k| (k.clone(), Value::String(REDACTED.to_owned())))
                .collect();
            Value::Object(redacted)
        }
        Value::Array(items) => Value::Array(
            items
                .iter()
                .take(5)
                .map(|_| Value::String(REDACTED.to_owned()))
                .collect(),
        ),
        _ => Value::String(REDACTED.to_owned()),
    }
}

/// `"sha256:<hex>"` digest of the canonical-`JSON` byte form.
#[must_use]
pub fn digest(value: &JsonValue) -> String {
    let hash = Sha256::digest(canonical_bytes(value));
    let mut out = String::with_capacity("sha256:".len() + hash.len() * 2);
    out.push_str("sha256:");
    for byte in hash {
        // Writing hex into a String is infallible.
        let _ = write!(out, "{byte:02x}");
    }
    out
}

/// Byte length of the canonical-`JSON` byte form.
#[must_use]
pub fn encoded_size(value: &JsonValue) -> usize {
    canonical_bytes(value).len()
}

/// Canonical `JSON` bytes: keys sorted recursively, compact separators, no
/// non-`ASCII` escaping (`UTF-8` passthrough). Matches Python's
/// `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
///
/// `serde_json`'s default output already uses compact separators and emits
/// `UTF-8` directly; the explicit recursive key sort guards the byte form even
/// if a transitive feature ever flips on `preserve_order`.
pub(crate) fn canonical_bytes(value: &JsonValue) -> Vec<u8> {
    serde_json::to_vec(&canonicalize(value)).expect("serde_json::Value always serializes")
}

/// Recursively rebuild `value` with object keys in sorted order. Inserting in
/// sorted order keeps the result sorted under both the `BTreeMap` and the
/// `preserve_order` `IndexMap` backings of `serde_json::Map`.
fn canonicalize(value: &JsonValue) -> JsonValue {
    match value {
        Value::Object(map) => {
            let sorted: BTreeMap<&str, JsonValue> = map
                .iter()
                .map(|(k, v)| (k.as_str(), canonicalize(v)))
                .collect();
            let mut out = serde_json::Map::with_capacity(sorted.len());
            for (k, v) in sorted {
                out.insert(k.to_owned(), v);
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(canonicalize).collect()),
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use proptest::prelude::*;
    use serde_json::json;

    // AC-audit-03: shape/redacted_shape match fixtures — dict keys preserved with
    // type-name/marker values; lists truncate to 5.
    #[test]
    fn shape_and_redacted_match_fixtures() {
        let value = json!({
            "path": "README.md",
            "limit": 5,
            "ratio": 1.5,
            "flag": true,
            "missing": null,
        });
        assert_eq!(
            shape(&value),
            json!({
                "path": "str",
                "limit": "int",
                "ratio": "float",
                "flag": "bool",
                "missing": "NoneType",
            })
        );
        assert_eq!(
            redacted_shape(&value),
            json!({
                "path": "<redacted>",
                "limit": "<redacted>",
                "ratio": "<redacted>",
                "flag": "<redacted>",
                "missing": "<redacted>",
            })
        );

        // Lists truncate to the first 5 elements.
        let list = json!([1, 2, 3, 4, 5, 6, 7]);
        assert_eq!(shape(&list), json!(["int", "int", "int", "int", "int"]));
        assert_eq!(
            redacted_shape(&list),
            json!([
                "<redacted>",
                "<redacted>",
                "<redacted>",
                "<redacted>",
                "<redacted>"
            ])
        );

        // redacted_shape is one level deep: a nested object collapses to one
        // marker under its key.
        assert_eq!(
            redacted_shape(&json!({"outer": {"inner": 1}})),
            json!({"outer": "<redacted>"})
        );
    }

    // AC-audit-04: digest is the canonical sorted-key hash and is stable across
    // key-insertion order; encoded_size is the canonical byte length.
    proptest! {
        #[test]
        fn digest_is_canonical_and_deterministic(
            map in proptest::collection::btree_map("[a-z]{1,8}", any::<i64>(), 0..8)
        ) {
            let forward: serde_json::Map<String, Value> =
                map.iter().map(|(k, v)| (k.clone(), json!(v))).collect();
            let reversed: serde_json::Map<String, Value> =
                map.iter().rev().map(|(k, v)| (k.clone(), json!(v))).collect();
            let forward = Value::Object(forward);
            let reversed = Value::Object(reversed);

            prop_assert_eq!(digest(&forward), digest(&reversed));
            prop_assert!(digest(&forward).starts_with("sha256:"));
            prop_assert_eq!(encoded_size(&forward), canonical_bytes(&forward).len());
        }
    }

    // Pin the canonical byte form and a known digest against a hand-computed
    // fixture so a serializer change can never silently move the hash.
    #[test]
    fn canonical_bytes_are_sorted_and_compact() {
        let value = json!({"path": "README.md", "limit": 5});
        assert_eq!(
            canonical_bytes(&value),
            br#"{"limit":5,"path":"README.md"}"#
        );
        assert_eq!(
            digest(&value),
            "sha256:eae28db122d085e97660cdaca234aa8fe92b68793608afd3c5391d10ab70a5d3"
        );
        assert_eq!(encoded_size(&value), 30);
    }
}
