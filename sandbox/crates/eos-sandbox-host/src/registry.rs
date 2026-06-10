//! Sandbox registry: the in-memory fleet map, its docker-label rebuild, and
//! the host-private token store.
//!
//! A host restart MUST NOT orphan running sandboxes: every record is
//! reconstructible from the container labels (SPEC §5) plus a token file in
//! the host-private state dir keyed by sandbox id.

use std::collections::HashMap;
use std::fs;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, PoisonError};

use anyhow::{Context, Result};
use serde_json::Value;

use crate::docker::{container_labels, running_container_ids};

/// Container label carrying the sandbox id (also the registry rebuild filter).
pub const SANDBOX_ID_LABEL: &str = "eos.sandbox_id";
/// Container label carrying the in-container daemon TCP port.
pub const TCP_PORT_LABEL: &str = "eos.tcp_port";
/// Container label carrying the provisioning identity.
pub const CREATED_BY_LABEL: &str = "eos.created_by";

/// One live sandbox: container handle, auth token, and endpoint cache.
#[derive(Debug)]
pub struct SandboxRecord {
    /// Public sandbox identity (`sb-…`).
    pub sandbox_id: String,
    /// Docker container name (equals the sandbox id at provision time).
    pub container: String,
    /// Daemon TCP auth token.
    pub token: String,
    /// In-container daemon TCP port (the docker-published side varies).
    pub tcp_port: u16,
    /// Provisioning identity from the container label.
    pub created_by: String,
    endpoint: Mutex<Option<SocketAddr>>,
}

impl SandboxRecord {
    pub(crate) fn new(
        sandbox_id: String,
        container: String,
        token: String,
        tcp_port: u16,
        created_by: String,
        endpoint: Option<SocketAddr>,
    ) -> Self {
        Self {
            sandbox_id,
            container,
            token,
            tcp_port,
            created_by,
            endpoint: Mutex::new(endpoint),
        }
    }

    /// The cached loopback endpoint, if previously resolved.
    #[must_use]
    pub fn cached_endpoint(&self) -> Option<SocketAddr> {
        *self.endpoint.lock().unwrap_or_else(PoisonError::into_inner)
    }

    pub(crate) fn cache_endpoint(&self, addr: SocketAddr) {
        *self.endpoint.lock().unwrap_or_else(PoisonError::into_inner) = Some(addr);
    }

    pub(crate) fn invalidate_endpoint(&self) {
        *self.endpoint.lock().unwrap_or_else(PoisonError::into_inner) = None;
    }
}

/// In-memory map `sandbox_id → record`, rebuilt from docker labels on open.
pub struct SandboxRegistry {
    state_dir: PathBuf,
    records: Mutex<HashMap<String, Arc<SandboxRecord>>>,
}

impl SandboxRegistry {
    /// Open the registry over `state_dir` (created `0700` when missing).
    ///
    /// # Errors
    /// Returns an error if the state dir cannot be created.
    pub fn open(state_dir: PathBuf) -> Result<Self> {
        fs::create_dir_all(&state_dir)
            .with_context(|| format!("create host state dir {}", state_dir.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let perms = fs::Permissions::from_mode(0o700);
            fs::set_permissions(&state_dir, perms)
                .with_context(|| format!("chmod 700 {}", state_dir.display()))?;
        }
        Ok(Self {
            state_dir,
            records: Mutex::new(HashMap::new()),
        })
    }

    /// Rebuild records from running containers labeled with
    /// [`SANDBOX_ID_LABEL`], recovering tokens from the state dir. Containers
    /// whose token file is missing are skipped (unreachable without auth).
    /// Returns how many sandboxes were adopted.
    pub fn rebuild_from_docker(&self) -> usize {
        let ids = running_container_ids(&[SANDBOX_ID_LABEL.to_owned()]);
        let Ok(label_maps) = container_labels(&ids) else {
            return 0;
        };
        let mut adopted = 0;
        for labels in label_maps {
            let label = |key: &str| labels.get(key).and_then(Value::as_str);
            let Some(sandbox_id) = label(SANDBOX_ID_LABEL) else {
                continue;
            };
            let Ok(token) = self.load_token(sandbox_id) else {
                continue;
            };
            let Some(tcp_port) = label(TCP_PORT_LABEL).and_then(|port| port.parse::<u16>().ok())
            else {
                continue;
            };
            let created_by = label(CREATED_BY_LABEL).unwrap_or("unknown").to_owned();
            // Container NAME is the docker handle the engine commands use; the
            // provision flow names containers after their sandbox id.
            let record = SandboxRecord::new(
                sandbox_id.to_owned(),
                sandbox_id.to_owned(),
                token,
                tcp_port,
                created_by,
                None,
            );
            self.lock().insert(sandbox_id.to_owned(), Arc::new(record));
            adopted += 1;
        }
        adopted
    }

    /// Insert a freshly provisioned record and persist its token.
    ///
    /// # Errors
    /// Returns an error if the token file cannot be written.
    pub fn insert(&self, record: SandboxRecord) -> Result<Arc<SandboxRecord>> {
        self.persist_token(&record.sandbox_id, &record.token)?;
        let record = Arc::new(record);
        self.lock()
            .insert(record.sandbox_id.clone(), Arc::clone(&record));
        Ok(record)
    }

    /// Look up one sandbox.
    #[must_use]
    pub fn get(&self, sandbox_id: &str) -> Option<Arc<SandboxRecord>> {
        self.lock().get(sandbox_id).cloned()
    }

    /// Drop one sandbox record and forget its token. Returns the removed
    /// record when it existed.
    pub fn remove(&self, sandbox_id: &str) -> Option<Arc<SandboxRecord>> {
        let removed = self.lock().remove(sandbox_id);
        if removed.is_some() {
            let _ = fs::remove_file(self.token_path(sandbox_id));
        }
        removed
    }

    /// Snapshot all records, ordered by sandbox id.
    #[must_use]
    pub fn list(&self) -> Vec<Arc<SandboxRecord>> {
        let mut records: Vec<_> = self.lock().values().cloned().collect();
        records.sort_by(|a, b| a.sandbox_id.cmp(&b.sandbox_id));
        records
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, Arc<SandboxRecord>>> {
        self.records.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn token_path(&self, sandbox_id: &str) -> PathBuf {
        self.state_dir.join(format!("{sandbox_id}.token"))
    }

    fn persist_token(&self, sandbox_id: &str, token: &str) -> Result<()> {
        let path = self.token_path(sandbox_id);
        fs::write(&path, token).with_context(|| format!("write token {}", path.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600))
                .with_context(|| format!("chmod 600 {}", path.display()))?;
        }
        Ok(())
    }

    fn load_token(&self, sandbox_id: &str) -> Result<String> {
        let path = self.token_path(sandbox_id);
        let token =
            fs::read_to_string(&path).with_context(|| format!("read token {}", path.display()))?;
        Ok(token.trim().to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn registry_round_trips_records_and_tokens() -> Result<()> {
        let dir = std::env::temp_dir().join(format!("eos-host-registry-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let registry = SandboxRegistry::open(dir.clone())?;
        let record = SandboxRecord::new(
            "sb-1".into(),
            "sb-1".into(),
            "tok".into(),
            37_657,
            "test".into(),
            None,
        );
        let record = registry.insert(record)?;
        assert_eq!(registry.load_token("sb-1")?, "tok");
        assert!(registry.get("sb-1").is_some());
        assert_eq!(registry.list().len(), 1);

        record.cache_endpoint("127.0.0.1:9999".parse().expect("addr"));
        assert!(record.cached_endpoint().is_some());
        record.invalidate_endpoint();
        assert!(record.cached_endpoint().is_none());

        assert!(registry.remove("sb-1").is_some());
        assert!(registry.get("sb-1").is_none());
        assert!(registry.load_token("sb-1").is_err());
        let _ = fs::remove_dir_all(dir);
        Ok(())
    }
}
