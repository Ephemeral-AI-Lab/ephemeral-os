//! Environment-variable → config-tree conversion (`loader.py:_data_from_env`).
//!
//! Builds a partial [`serde_yaml::Value`] mapping from the process (or injected)
//! environment that the loader merges between the YAML and init layers. Two
//! sources feed it, in this order (so legacy wins on a shared path, matching
//! `loader.py`): nested `EOS__SECTION__FIELD` vars first, then the retained
//! legacy adapter vars.
//!
//! Documented divergence from Python (§8 item 8): serde does not coerce
//! `str -> numeric/bool`, so *every* env scalar — both `EOS__` and legacy — is
//! YAML-parsed (not just the `[`/`{` complex values Python special-cased). This
//! is what makes `EOS__SANDBOX__TIMEOUT_S=120` deserialize into `f64` and the
//! legacy `EOS_DOCKER_PRIVILEGED=true` into `bool`.

use std::collections::BTreeMap;

use serde_yaml::{Mapping, Value};

/// The injected (or process) environment the loader and path resolvers read.
pub type EnvMap = BTreeMap<String, String>;

const EOS_PREFIX: &str = "EOS__";

/// The retained legacy env adapters (§8 item 3). Each maps a legacy var to a
/// nested config path. `EOS_SWEEVO_*` and the non-Docker provider vars from the
/// Python `_LEGACY_ENV_MAP` are intentionally not ported (GC-eos-config-05/08).
const LEGACY_ENV_MAP: &[(&str, &[&str])] = &[
    ("EPHEMERALOS_DATABASE_URL", &["database", "url"]),
    (
        "EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS",
        &["sandbox", "timeout_s"],
    ),
    (
        "EPHEMERALOS_RUNTIME_CLIENT_TIMEOUT",
        &["sandbox", "runtime_client_timeout_s"],
    ),
    ("EOS_SANDBOX_PROVIDER", &["sandbox", "default_provider"]),
    (
        "EOS_DOCKER_DAEMON_TCP",
        &["sandbox", "docker", "daemon_tcp"],
    ),
    (
        "EOS_DOCKER_PRIVILEGED",
        &["sandbox", "docker", "privileged"],
    ),
    (
        "EOS_DOCKER_NO_PRIVILEGE",
        &["sandbox", "docker", "no_privilege"],
    ),
    ("MINIMAX_BASE_URL", &["providers", "minimax", "base_url"]),
    ("MINIMAX_MODEL", &["providers", "minimax", "model"]),
];

/// YAML-parse a single env scalar, falling back to the trimmed string when it is
/// not valid YAML. Trimming mirrors Python's `str.strip`/`_parse_complex_env_value`.
fn coerce_scalar(raw: &str) -> Value {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Value::String(String::new());
    }
    serde_yaml::from_str::<Value>(trimmed).unwrap_or_else(|_| Value::String(trimmed.to_owned()))
}

/// Set `value` at the nested `path`, creating intermediate mappings and
/// overwriting any non-mapping node in the way (`loader.py:_set_nested`).
fn set_nested(map: &mut Mapping, path: &[impl AsRef<str>], value: Value) {
    let Some((last, parents)) = path.split_last() else {
        return;
    };
    let mut cursor = map;
    for key in parents {
        let entry = cursor
            .entry(Value::String(key.as_ref().to_owned()))
            .or_insert_with(|| Value::Mapping(Mapping::new()));
        if !entry.is_mapping() {
            *entry = Value::Mapping(Mapping::new());
        }
        cursor = entry
            .as_mapping_mut()
            .expect("entry was just ensured to be a mapping");
    }
    cursor.insert(Value::String(last.as_ref().to_owned()), value);
}

/// Build the env-derived partial config tree.
pub(crate) fn data_from_env(env: &EnvMap) -> Value {
    let mut map = Mapping::new();

    // 1. Nested EOS__SECTION__FIELD vars (lowercase each segment).
    for (name, raw) in env {
        if let Some(rest) = name.strip_prefix(EOS_PREFIX) {
            let path: Vec<String> = rest
                .split("__")
                .filter(|seg| !seg.is_empty())
                .map(str::to_ascii_lowercase)
                .collect();
            if !path.is_empty() {
                set_nested(&mut map, &path, coerce_scalar(raw));
            }
        }
    }

    // 2. Legacy adapters — applied after EOS__ so they win on a shared path.
    //    Blank values are skipped; EOS_SANDBOX_PROVIDER additionally lowercases.
    for (var, path) in LEGACY_ENV_MAP {
        let Some(raw) = env.get(*var) else { continue };
        if raw.trim().is_empty() {
            continue;
        }
        let transformed = if *var == "EOS_SANDBOX_PROVIDER" {
            raw.trim().to_ascii_lowercase()
        } else {
            raw.trim().to_owned()
        };
        set_nested(&mut map, path, coerce_scalar(&transformed));
    }

    // 3. Default-snapshot special case. The Python fan-out to the non-Docker
    //    provider section is not ported (GC-eos-config-08).
    if let Some(raw) = env.get("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT") {
        if !raw.trim().is_empty() {
            set_nested(
                &mut map,
                &["sandbox", "docker", "default_snapshot"],
                coerce_scalar(raw),
            );
        }
    }

    Value::Mapping(map)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    fn env(pairs: &[(&str, &str)]) -> EnvMap {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
            .collect()
    }

    fn at<'a>(tree: &'a Value, path: &[&str]) -> Option<&'a Value> {
        let mut cur = tree;
        for key in path {
            cur = cur.as_mapping()?.get(Value::String((*key).to_owned()))?;
        }
        Some(cur)
    }

    // AC-eos-config-03: EOS__ nested env sets the path; scalars are YAML-coerced;
    // complex `[...]` values are parsed.
    #[test]
    fn test_eos_nested_env_sets_path() {
        let tree = data_from_env(&env(&[
            ("EOS__SANDBOX__TIMEOUT_S", "120"),
            ("EOS__PROVIDERS__RETRY__STATUS_CODES", "[429,503]"),
        ]));
        assert_eq!(
            at(&tree, &["sandbox", "timeout_s"]),
            Some(&Value::Number(120.into()))
        );
        let codes = at(&tree, &["providers", "retry", "status_codes"]).unwrap();
        assert_eq!(
            codes.as_sequence().unwrap().len(),
            2,
            "complex [429,503] value should YAML-parse to a 2-element sequence"
        );
    }

    // AC-eos-config-04: the default-snapshot var sets docker.default_snapshot.
    #[test]
    fn test_default_snapshot_fans_out() {
        let tree = data_from_env(&env(&[("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT", "foo")]));
        assert_eq!(
            at(&tree, &["sandbox", "docker", "default_snapshot"]),
            Some(&Value::String("foo".to_owned()))
        );
    }

    // AC-eos-config-05: legacy adapters map to their paths; provider lowercases;
    // legacy bools YAML-coerce; blank values are ignored.
    #[test]
    fn test_legacy_env_adapters() {
        let tree = data_from_env(&env(&[
            ("EPHEMERALOS_DATABASE_URL", "  sqlite:///./x.db  "),
            ("EOS_SANDBOX_PROVIDER", "DOCKER"),
            ("EOS_DOCKER_PRIVILEGED", "true"),
            ("MINIMAX_MODEL", ""), // blank → skipped
        ]));
        assert_eq!(
            at(&tree, &["database", "url"]),
            Some(&Value::String("sqlite:///./x.db".to_owned()))
        );
        assert_eq!(
            at(&tree, &["sandbox", "default_provider"]),
            Some(&Value::String("docker".to_owned()))
        );
        assert_eq!(
            at(&tree, &["sandbox", "docker", "privileged"]),
            Some(&Value::Bool(true)),
            "legacy EOS_DOCKER_PRIVILEGED=true should YAML-coerce to a bool"
        );
        assert!(
            at(&tree, &["providers", "minimax", "model"]).is_none(),
            "blank legacy value should be skipped"
        );
    }
}
