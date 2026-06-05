//! eos-config — typed, validated, immutable runtime configuration.
//!
//! This crate loads [`CentralConfig`] from files only — the committed
//! `agent-core/config/prd.yml` baseline merged with a gitignored
//! `agent-core/config/local.yml` override (objects recurse, scalars/arrays
//! replace) — parses raw strings into validated config types at the boundary,
//! and fails fast on unsupported settings (network database urls). There is no
//! environment-variable or CLI config selection: config is chosen by file. It is
//! a leaf of the dependency DAG — it has no internal upstream edge (not even
//! `eos-types`) — and is consumed read-only by every crate that needs tunables.
//!
//! It deliberately does **not** resolve the active model (that is `eos-db`),
//! hold secrets (those live only in the gitignored override), open connections,
//! spawn tasks, or perform any I/O beyond reading the config files.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod attempt;
mod config;
mod database;
mod error;
mod loader;
mod markdown;
mod providers;
mod validation;

pub use attempt::AttemptConfig;
pub use config::CentralConfig;
pub use database::{DatabaseConfig, DatabaseUrl, DEFAULT_SQLITE_DATABASE_URL};
pub use error::ConfigError;
pub use loader::{load, load_with_override};
pub use markdown::parse_markdown_frontmatter;
pub use providers::{ProvidersConfig, RetryConfig};

#[cfg(test)]
mod schema_parity {
    //! AC-eos-config-10: the `CentralConfig` JSON Schema is checked against the
    //! recorded Pydantic-derived schema (`tests/fixtures/...`) for the surviving
    //! sections, then a normalized snapshot guards Rust-side drift.
    //!
    //! Scope note (loud, per review): this is a **field-name** cross-check, not a
    //! full type-level Pydantic comparator. The Rust schema intentionally drops
    //! sections/fields (`runner`/`engine`, `pool_pre_ping`/`max_overflow`) and
    //! adds Rust-only ones (`attempt`, the sqlite controls); a
    //! type-level parity comparator over the surviving subset is deferred to the
    //! Phase-7 cutover parity harness (matching the Phase-0 corpus deferrals).
    #![allow(clippy::unwrap_used)]

    use std::collections::BTreeSet;

    use schemars::schema_for;
    use serde_json::Value;

    use super::CentralConfig;

    const PYTHON_SCHEMA: &str = include_str!("../tests/fixtures/central_config_python_schema.json");

    /// The definitions map, under either the Pydantic (`$defs`) or schemars
    /// (`definitions`) key.
    fn defs(schema: &Value) -> &serde_json::Map<String, Value> {
        schema
            .get("$defs")
            .or_else(|| schema.get("definitions"))
            .and_then(Value::as_object)
            .expect("schema has a definitions map")
    }

    fn keys(object: Option<&Value>) -> BTreeSet<String> {
        object
            .and_then(|v| v.get("properties"))
            .and_then(Value::as_object)
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default()
    }

    fn section_fields(schema: &Value, section: &str) -> BTreeSet<String> {
        keys(defs(schema).get(section))
    }

    fn top_fields(schema: &Value) -> BTreeSet<String> {
        keys(Some(schema))
    }

    fn expect(py: &BTreeSet<String>, dropped: &[&str], added: &[&str]) -> BTreeSet<String> {
        let mut out: BTreeSet<String> = py
            .iter()
            .filter(|f| !dropped.contains(&f.as_str()))
            .cloned()
            .collect();
        out.extend(added.iter().map(|s| (*s).to_owned()));
        out
    }

    // The deliberate per-section deltas (impl-eos-config.md §3/§6).
    #[test]
    fn test_central_config_field_names_match_python() {
        let py: Value = serde_json::from_str(PYTHON_SCHEMA).unwrap();
        let rust: Value = serde_json::to_value(schema_for!(CentralConfig)).unwrap();

        // Top level: drop runner/engine + the whole sandbox section (sandbox
        // config is owned by the ephemeral-os sandbox module, not agent-core),
        // add the Rust-only attempt section.
        assert_eq!(
            top_fields(&rust),
            expect(&top_fields(&py), &["runner", "engine", "sandbox"], &["attempt"]),
            "CentralConfig top-level field names diverged from Python beyond the documented deltas",
        );

        let cases: &[(&str, &[&str], &[&str])] = &[
            (
                "DatabaseConfig",
                &["pool_pre_ping", "max_overflow", "echo"],
                &["busy_timeout_ms", "wal", "foreign_keys"],
            ),
            ("ProvidersConfig", &["minimax"], &[]),
            ("RetryConfig", &[], &[]),
        ];
        for (section, dropped, added) in cases {
            assert_eq!(
                section_fields(&rust, section),
                expect(&section_fields(&py, section), dropped, added),
                "{section} field names diverged from Python beyond the documented deltas",
            );
        }
    }

    /// Strip integer `format`/`minimum`/`maximum` so Rust `u*` types compare as
    /// Python's unbounded `integer` (AC-10 normalization step (a)).
    fn normalize(value: &mut Value) {
        match value {
            Value::Object(map) => {
                if map.get("type").and_then(Value::as_str) == Some("integer") {
                    map.remove("format");
                    map.remove("minimum");
                    map.remove("maximum");
                }
                // Drop doc-comment text so the snapshot guards schema *shape*
                // only; a field's prose drifting must not trip it (Python field
                // parity is covered by the cross-check test above).
                map.remove("description");
                for child in map.values_mut() {
                    normalize(child);
                }
            }
            Value::Array(items) => items.iter_mut().for_each(normalize),
            _ => {}
        }
    }

    // The recorded, normalized Rust schema — a drift guard (AC-10 snapshot).
    #[test]
    fn test_central_config_json_schema_snapshot() {
        let mut rust: Value = serde_json::to_value(schema_for!(CentralConfig)).unwrap();
        normalize(&mut rust);
        insta::with_settings!({ snapshot_path => "../tests/schema_parity/snapshots" }, {
            insta::assert_json_snapshot!("central_config_schema", rust);
        });
    }
}
