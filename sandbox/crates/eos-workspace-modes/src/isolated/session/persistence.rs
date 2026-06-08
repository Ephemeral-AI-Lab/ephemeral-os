use std::path::PathBuf;

use crate::isolated::audit::AuditSink;
use crate::isolated::caps::PERSISTED_HANDLES_SCHEMA_VERSION;
use crate::isolated::error::IsolatedError;
use serde_json::{json, Value};

use super::{IsolatedSession, LayerStackSnapshotPort, NamespaceRuntimePort};

impl<S, R, A> IsolatedSession<S, R, A>
where
    S: LayerStackSnapshotPort,
    R: NamespaceRuntimePort,
    A: AuditSink,
{
    pub(super) fn session_scratch_root(&self) -> PathBuf {
        self.scratch_root.clone()
    }

    fn persisted_handles_path(&self) -> PathBuf {
        self.session_scratch_root().join("manager.json")
    }

    pub(super) fn persist_handles(&self) -> Result<(), IsolatedError> {
        let root = self.session_scratch_root();
        std::fs::create_dir_all(&root).map_err(|err| IsolatedError::SetupFailed {
            step: format!("manager_root: {err}"),
        })?;
        let handles: Vec<Value> = self
            .handles
            .values()
            .map(|handle| {
                json!({
                    "workspace_handle_id": handle.workspace_handle_id.0,
                    "caller_id": handle.caller_id.0,
                    "lease_id": handle.lease_id,
                    "manifest_version": handle.manifest_version,
                    "manifest_root_hash": handle.manifest_root_hash,
                    "workspace_root": handle.workspace_root,
                    "scratch_dir": handle.scratch_dir.to_string_lossy(),
                    "upperdir": handle.upperdir.to_string_lossy(),
                    "workdir": handle.workdir.to_string_lossy(),
                    "layer_paths": handle.layer_paths,
                    "holder_pid": handle.holder_pid,
                    "veth_host_name": handle.veth.as_ref().map(|veth| veth.host_name.as_str()),
                    "veth_ns_name": handle.veth.as_ref().map(|veth| veth.ns_name.as_str()),
                    "ns_ip": handle.veth.as_ref().map(|veth| veth.ns_ip.to_string()),
                    "cgroup_path": handle
                        .cgroup_path
                        .as_ref()
                        .map(|path| path.to_string_lossy().into_owned()),
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
        std::fs::write(
            &tmp,
            serde_json::to_vec_pretty(&payload).map_err(|err| IsolatedError::SetupFailed {
                step: format!("manager_serialize: {err}"),
            })?,
        )
        .map_err(|err| IsolatedError::SetupFailed {
            step: format!("manager_write: {err}"),
        })?;
        std::fs::rename(tmp, path).map_err(|err| IsolatedError::SetupFailed {
            step: format!("manager_rename: {err}"),
        })?;
        Ok(())
    }

    pub(super) fn read_persisted_handle_rows(&self) -> Vec<Value> {
        let Ok(raw) = std::fs::read(self.persisted_handles_path()) else {
            return Vec::new();
        };
        let Ok(payload) = serde_json::from_slice::<Value>(&raw) else {
            return Vec::new();
        };
        if payload.get("schema_version").and_then(Value::as_u64)
            != Some(u64::from(PERSISTED_HANDLES_SCHEMA_VERSION))
        {
            return Vec::new();
        }
        payload
            .get("handles")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
    }
}
