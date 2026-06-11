use std::net::Ipv4Addr;
use std::path::{Path, PathBuf};

use crate::isolated_workspace::caps::{HANDLE_PREFIX, PERSISTED_HANDLES_SCHEMA_VERSION};
use crate::isolated_workspace::error::IsolatedError;
use crate::isolated_workspace::network::VethAllocation;
use serde_json::{json, Value};

use super::IsolatedManager;

impl IsolatedManager {
    pub(super) fn reap_persisted_orphans(&mut self) -> Result<Vec<String>, IsolatedError> {
        let rows = self.read_persisted_handle_rows();
        self.handles.clear();
        self.by_caller.clear();
        for row in &rows {
            if let Some(ns_ip) = persisted_ipv4(row, "ns_ip") {
                let _ = self.network.reserve_persisted_ip(ns_ip);
            }
        }
        let orphan_lease_ids = rows
            .iter()
            .filter_map(|row| persisted_string(row, "lease_id"))
            .collect();
        for row in &rows {
            self.reap_persisted_holder(row);
            self.reap_persisted_veth(row);
            self.reap_persisted_cgroup(row);
            self.reap_persisted_scratch(row);
        }
        self.reap_named_orphans();
        self.persist_handles()?;
        Ok(orphan_lease_ids)
    }

    fn reap_persisted_holder(&self, row: &Value) {
        if let Some(holder_pid) = persisted_i32(row, "holder_pid").filter(|pid| *pid > 0) {
            let _ = self
                .runtime
                .kill_holder(holder_pid, self.caps.exit_grace_s.max(0.0));
        }
    }

    fn reap_persisted_veth(&mut self, row: &Value) {
        let Some(host_name) = persisted_string(row, "veth_host_name") else {
            return;
        };
        let Some(ns_name) = persisted_string(row, "veth_ns_name") else {
            return;
        };
        let Some(ns_ip) = persisted_ipv4(row, "ns_ip") else {
            return;
        };
        let allocation = VethAllocation {
            host_name: host_name.clone(),
            ns_name,
            ns_ip,
        };

        self.network.teardown_veth(&allocation);
        let _ = self.network.reserve_persisted_ip(ns_ip);
    }

    fn reap_persisted_cgroup(&self, row: &Value) {
        if let Some(path) = persisted_existing_path(row, "cgroup_path") {
            kill_cgroup_pids(&path);
            let _ = std::fs::remove_dir(path);
        }
    }

    fn reap_persisted_scratch(&self, row: &Value) {
        if let Some(path) = persisted_existing_path(row, "scratch_dir") {
            let _ = std::fs::remove_dir_all(path);
        }
    }

    pub(super) fn reap_named_orphans(&mut self) {
        self.reap_named_veth_orphans();
        self.reap_named_cgroup_orphans();
        self.reap_named_scratch_orphans();
    }

    fn reap_named_veth_orphans(&mut self) {
        let Ok(entries) = std::fs::read_dir("/sys/class/net") else {
            return;
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if !name.starts_with(HANDLE_PREFIX) {
                continue;
            }
            self.network.teardown_host_veth(&name);
        }
    }

    fn reap_named_cgroup_orphans(&self) {
        let Ok(entries) = std::fs::read_dir("/sys/fs/cgroup") else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if !name.starts_with(HANDLE_PREFIX) || !path.is_dir() {
                continue;
            }
            kill_cgroup_pids(&path);
            let _ = std::fs::remove_dir(&path);
        }
    }

    fn reap_named_scratch_orphans(&self) {
        let Ok(entries) = std::fs::read_dir(&self.scratch_root) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if name == "manager.json" || !path.is_dir() {
                continue;
            }
            let _ = std::fs::remove_dir_all(&path);
        }
    }

    fn persisted_handles_path(&self) -> PathBuf {
        self.scratch_root.join("manager.json")
    }

    pub(super) fn persist_handles(&self) -> Result<(), IsolatedError> {
        std::fs::create_dir_all(&self.scratch_root).map_err(|err| IsolatedError::SetupFailed {
            step: format!("manager_root: {err}"),
        })?;
        let handles: Vec<Value> = self
            .handles
            .values()
            .map(|handle| {
                json!({
                    "workspace_handle_id": handle.workspace_id.0,
                    "caller_id": handle.caller_id,
                    "lease_id": handle.lease_id,
                    "manifest_version": handle.manifest_version,
                    "manifest_root_hash": handle.manifest_root_hash,
                    "workspace_root": handle.workspace_root,
                    "scratch_dir": handle.dirs.run_dir.to_string_lossy(),
                    "upperdir": handle.dirs.upperdir.to_string_lossy(),
                    "workdir": handle.dirs.workdir.to_string_lossy(),
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

fn persisted_string(row: &Value, key: &str) -> Option<String> {
    let value = row.get(key)?.as_str()?.trim();
    if value.is_empty() {
        return None;
    }
    Some(value.to_owned())
}

fn persisted_i32(row: &Value, key: &str) -> Option<i32> {
    let value = row.get(key)?.as_i64()?;
    i32::try_from(value).ok()
}

fn persisted_ipv4(row: &Value, key: &str) -> Option<Ipv4Addr> {
    persisted_string(row, key)?.parse().ok()
}

fn persisted_path(row: &Value, key: &str) -> Option<PathBuf> {
    persisted_string(row, key).map(PathBuf::from)
}

fn persisted_existing_path(row: &Value, key: &str) -> Option<PathBuf> {
    persisted_path(row, key).filter(|path| path.exists())
}

fn kill_cgroup_pids(cgroup_path: &Path) {
    let kill_file = cgroup_path.join("cgroup.kill");
    if kill_file.exists() {
        let _ = std::fs::write(kill_file, "1\n");
    }
}
