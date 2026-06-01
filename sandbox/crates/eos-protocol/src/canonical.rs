//! AV-1 canonical form for response-envelope comparison.
//!
//! Invariant: response envelopes carry non-deterministic fields (the whole
//! `timings` subtree, plus `daemon_pid` and `uptime_s`). [`canonicalize`] drops
//! those, recursively sorts object keys, and normalizes float representation so
//! two structurally-equal responses compare equal regardless of measured values.
//! Requests, error envelopes, and the CAS hashes are byte/structurally exact and
//! must NOT be routed through this.
//! `// PORT docs/contract/01-wire-protocol.md §2.2`

use serde_json::{Map, Value};

/// Object keys dropped wholesale before comparison (in addition to the entire
/// `timings` subtree, which is dropped wherever it appears).
const DROP_KEYS: &[&str] = &["timings", "daemon_pid", "uptime_s"];

/// Return a canonical copy of `value`: recursively drop the non-deterministic
/// allowlist, sort object keys, and quantize floats to 1e-9 to absorb
/// representation jitter.
pub fn canonicalize(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut out: Map<String, Value> = Map::new();
            // BTreeMap-style key sort is achieved by inserting in sorted order;
            // with `preserve_order`, insertion order is the emission order.
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            for key in keys {
                if DROP_KEYS.contains(&key.as_str()) {
                    continue;
                }
                if let Some(v) = map.get(key) {
                    out.insert(key.clone(), canonicalize(v));
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(canonicalize).collect()),
        Value::Number(n) => {
            if let Some(f) = n.as_f64() {
                if n.as_i64().is_none() && n.as_u64().is_none() {
                    // Quantize to 1e-9; reuse string round-trip for stability.
                    let q = (f * 1e9).round() / 1e9;
                    return serde_json::Number::from_f64(q).map_or(Value::Null, Value::Number);
                }
            }
            value.clone()
        }
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn drops_timings_pid_uptime_and_sorts_keys() {
        let a = serde_json::json!({
            "b": 2, "a": 1,
            "timings": {"x": 0.1},
            "daemon_pid": 1234,
            "uptime_s": 3.5,
            "nested": {"timings": {"y": 9.0}, "k": "v"}
        });
        let b = serde_json::json!({
            "a": 1, "b": 2,
            "timings": {"x": 999.9},
            "daemon_pid": 4321,
            "uptime_s": 88.0,
            "nested": {"k": "v", "timings": {"y": 0.0}}
        });
        assert_eq!(canonicalize(&a), canonicalize(&b));
    }

    #[test]
    fn integers_preserved() {
        let v = serde_json::json!({"n": 0});
        assert_eq!(canonicalize(&v), serde_json::json!({"n": 0}));
    }
}
