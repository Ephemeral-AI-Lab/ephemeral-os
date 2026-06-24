use std::path::PathBuf;

use anyhow::Context as _;
use serde::Deserialize;

const RUN_ROOT_ENV: &str = "EOS_E2E_RUN_ROOT";
const MANIFEST_FILE: &str = "run-manifest.json";
const SUPPORTED_SCHEMA_VERSION: u32 = 1;

/// Minimal Phase-1 run configuration, sourced entirely from the manifest under
/// `EOS_E2E_RUN_ROOT`. The full parent `RunConfig` (`max_parallel`,
/// `BuildSource`, `CleanupPolicy`, `TestSelection`, timeouts) is deferred to
/// Phase 3.
pub struct RunConfig {
    pub run_root: PathBuf,
    pub gateway_socket: PathBuf,
    pub run_id: String,
    pub image: String,
}

#[derive(Deserialize)]
struct Manifest {
    schema_version: u32,
    gateway_socket: PathBuf,
    run_id: String,
    image: String,
}

impl RunConfig {
    /// Returns `Ok(None)` when `EOS_E2E_RUN_ROOT` is unset (the skip signal);
    /// `Ok(Some(_))` when the env is set and the manifest parses; `Err` only when
    /// the env is set but the manifest is missing/invalid (a real misconfig).
    pub fn from_env() -> anyhow::Result<Option<RunConfig>> {
        let Some(run_root) = std::env::var_os(RUN_ROOT_ENV) else {
            return Ok(None);
        };
        let run_root = PathBuf::from(run_root);
        let manifest_path = run_root.join(MANIFEST_FILE);
        let bytes = std::fs::read(&manifest_path)
            .with_context(|| format!("reading run manifest at {}", manifest_path.display()))?;
        let manifest: Manifest = serde_json::from_slice(&bytes)
            .with_context(|| format!("parsing run manifest at {}", manifest_path.display()))?;
        if manifest.schema_version != SUPPORTED_SCHEMA_VERSION {
            anyhow::bail!(
                "unsupported run-manifest schema_version {} (expected {SUPPORTED_SCHEMA_VERSION})",
                manifest.schema_version
            );
        }
        Ok(Some(RunConfig {
            run_root,
            gateway_socket: manifest.gateway_socket,
            run_id: manifest.run_id,
            image: manifest.image,
        }))
    }
}
