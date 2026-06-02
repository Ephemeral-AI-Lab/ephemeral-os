//! Config/data/log directory and central-YAML discovery (`config/paths.py`).
//!
//! Two deliberate divergences from the Python originals, both intentional (not
//! bugs):
//! 1. **No `mkdir`.** The Python `get_config_dir`/`get_data_dir`/`get_logs_dir`
//!    create the directory as a side effect; `eos-config` performs no filesystem
//!    writes (spec-conventions §1), so these resolve a path only.
//! 2. **CWD as the repo-root proxy.** Python's central-YAML discovery walked a
//!    `__file__`-relative source tree for the repo-root `ephemeralos.yaml`; at
//!    runtime Rust has no equivalent, so the process current directory is used.
//!
//! Resolution honors the injected environment (the same map the loader uses), so
//! discovery is deterministic and not coupled to the real process environment.

use std::path::PathBuf;

use crate::env::EnvMap;

const DEFAULT_BASE_DIR: &str = ".ephemeralos";
const CENTRAL_CONFIG_FILE_NAME: &str = "ephemeralos.yaml";

fn non_empty(value: Option<&String>) -> Option<&str> {
    value.map(String::as_str).filter(|v| !v.trim().is_empty())
}

fn home_dir(env: &EnvMap) -> Option<PathBuf> {
    non_empty(env.get("HOME"))
        .or_else(|| non_empty(env.get("USERPROFILE")))
        .map(PathBuf::from)
}

/// Resolve the config directory: `EPHEMERALOS_CONFIG_DIR` else `~/.ephemeralos`.
/// Does not create the directory (see module docs).
#[must_use]
pub fn config_dir(env: &EnvMap) -> PathBuf {
    if let Some(dir) = non_empty(env.get("EPHEMERALOS_CONFIG_DIR")) {
        PathBuf::from(dir)
    } else {
        home_dir(env).unwrap_or_default().join(DEFAULT_BASE_DIR)
    }
}

/// Resolve the data directory: `EPHEMERALOS_DATA_DIR` else `<config_dir>/data`.
#[must_use]
pub fn data_dir(env: &EnvMap) -> PathBuf {
    if let Some(dir) = non_empty(env.get("EPHEMERALOS_DATA_DIR")) {
        PathBuf::from(dir)
    } else {
        config_dir(env).join("data")
    }
}

/// Resolve the logs directory: `EPHEMERALOS_LOGS_DIR` else `<config_dir>/logs`.
#[must_use]
pub fn logs_dir(env: &EnvMap) -> PathBuf {
    if let Some(dir) = non_empty(env.get("EPHEMERALOS_LOGS_DIR")) {
        PathBuf::from(dir)
    } else {
        config_dir(env).join("logs")
    }
}

/// Resolve the central YAML config file (`paths.py:78-93` order):
/// 1. `EPHEMERALOS_CONFIG_DIR`/`ephemeralos.yaml`
/// 2. the process-CWD `ephemeralos.yaml` when present (the repo-root proxy)
/// 3. `<config_dir>/ephemeralos.yaml`
#[must_use]
pub fn central_config_file_path(env: &EnvMap) -> PathBuf {
    if non_empty(env.get("EPHEMERALOS_CONFIG_DIR")).is_some() {
        return config_dir(env).join(CENTRAL_CONFIG_FILE_NAME);
    }
    if let Ok(cwd) = std::env::current_dir() {
        let repo = cwd.join(CENTRAL_CONFIG_FILE_NAME);
        if repo.exists() {
            return repo;
        }
    }
    config_dir(env).join(CENTRAL_CONFIG_FILE_NAME)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(pairs: &[(&str, &str)]) -> EnvMap {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
            .collect()
    }

    #[test]
    fn config_dir_prefers_explicit_env() {
        assert_eq!(
            config_dir(&env(&[("EPHEMERALOS_CONFIG_DIR", "/tmp/cfg")])),
            PathBuf::from("/tmp/cfg")
        );
        assert_eq!(
            config_dir(&env(&[("HOME", "/home/u")])),
            PathBuf::from("/home/u/.ephemeralos")
        );
    }

    #[test]
    fn data_and_logs_default_under_config_dir() {
        let e = env(&[("EPHEMERALOS_CONFIG_DIR", "/tmp/cfg")]);
        assert_eq!(data_dir(&e), PathBuf::from("/tmp/cfg/data"));
        assert_eq!(logs_dir(&e), PathBuf::from("/tmp/cfg/logs"));
    }

    #[test]
    fn central_yaml_under_explicit_config_dir() {
        // With an explicit config dir, discovery never consults the CWD.
        assert_eq!(
            central_config_file_path(&env(&[("EPHEMERALOS_CONFIG_DIR", "/tmp/cfg")])),
            PathBuf::from("/tmp/cfg/ephemeralos.yaml")
        );
    }
}
