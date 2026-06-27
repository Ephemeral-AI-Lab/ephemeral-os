use std::io::Write;
use std::path::Path;

use serde_json::{json, Value};

use crate::session::manager::PERSISTED_HANDLES_SCHEMA_VERSION;
use crate::session::{WorkspaceManager, WorkspaceManagerError};

impl WorkspaceManager {
    fn persisted_handles_path(&self) -> std::path::PathBuf {
        self.scratch_root.join("manager.json")
    }

    pub(crate) fn persist_handles(&self) -> Result<(), WorkspaceManagerError> {
        std::fs::create_dir_all(&self.scratch_root)
            .map_err(|err| manager_setup_error("manager_root", err))?;
        let handles: Vec<Value> = self
            .handles
            .values()
            .map(|handle| {
                json!({
                    "workspace_handle_id": handle.workspace_id.0,
                    "lease_id": handle.snapshot.lease_id.0,
                    "manifest_version": handle.snapshot.manifest_version,
                    "manifest_root_hash": handle.snapshot.root_hash,
                    "network_profile": handle.network.as_str(),
                    "workspace_root": handle.workspace_root,
                    "scratch_dir": handle.dirs.run_dir.to_string_lossy(),
                    "upperdir": handle.dirs.upperdir.to_string_lossy(),
                    "workdir": handle.dirs.workdir.to_string_lossy(),
                    "layer_paths": handle.snapshot.layer_paths,
                    "holder_pid": handle.holder_pid,
                    "veth_host_name": handle.veth.as_ref().map(|veth| veth.host_name.as_str()),
                    "veth_ns_name": handle.veth.as_ref().map(|veth| veth.ns_name.as_str()),
                    "ns_ip": handle.veth.as_ref().map(|veth| veth.ns_ip.to_string()),
                    "created_at": handle.created_at,
                    "last_activity": handle.last_activity,
                })
            })
            .collect();
        let payload = json!({
            "schema_version": PERSISTED_HANDLES_SCHEMA_VERSION,
            "handles": handles,
        });
        let path = self.persisted_handles_path();
        let tmp = path.with_extension("json.tmp");
        let bytes = serde_json::to_vec_pretty(&payload)
            .map_err(|err| manager_setup_error("manager_serialize", err))?;
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&tmp)
            .map_err(|err| manager_setup_error("manager_write", err))?;
        file.write_all(&bytes)
            .and_then(|()| file.sync_all())
            .map_err(|err| manager_setup_error("manager_write", err))?;
        drop(file);
        std::fs::rename(&tmp, &path).map_err(|err| manager_setup_error("manager_rename", err))?;
        sync_directory(&self.scratch_root)
            .map_err(|err| manager_setup_error("manager_fsync", err))?;
        Ok(())
    }
}

fn manager_setup_error(step: &str, err: impl std::fmt::Display) -> WorkspaceManagerError {
    WorkspaceManagerError::SetupFailed {
        step: format!("{step}: {err}"),
    }
}

fn sync_directory(path: &Path) -> std::io::Result<()> {
    match std::fs::File::open(path).and_then(|file| file.sync_all()) {
        Ok(()) => Ok(()),
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::InvalidInput | std::io::ErrorKind::Unsupported
            ) =>
        {
            Ok(())
        }
        Err(error) => Err(error),
    }
}
