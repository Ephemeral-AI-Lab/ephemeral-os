use std::net::Ipv4Addr;
use std::path::{Path, PathBuf};
use std::time::Instant;

use crate::audit::AuditSink;
use crate::caps::HANDLE_PREFIX;
use crate::error::IsolatedError;
use crate::network::VethAllocation;
use serde_json::{json, Value};

use super::{IsolatedSession, LayerStackSnapshotPort, NamespaceRuntimePort};

impl<S, R, A> IsolatedSession<S, R, A>
where
    S: LayerStackSnapshotPort,
    R: NamespaceRuntimePort,
    A: AuditSink,
{
    pub(super) fn reap_startup_orphans(&mut self) -> Result<(), IsolatedError> {
        let rows = self.read_persisted_handle_rows();
        self.handles.clear();
        self.by_caller.clear();
        for row in &rows {
            if let Some(ns_ip) = persisted_ipv4(row, "ns_ip") {
                let _ = self.network.reserve_persisted_ip(ns_ip);
            }
        }
        for row in &rows {
            self.reap_persisted_lease(row);
            self.reap_persisted_holder(row);
            self.reap_persisted_veth(row);
            self.reap_persisted_cgroup(row);
            self.reap_persisted_scratch(row);
        }
        self.reap_named_orphans();
        self.persist_handles()
    }

    fn reap_persisted_lease(&self, row: &Value) {
        let Some(lease_id) = persisted_string(row, "lease_id") else {
            return;
        };
        let timer = Instant::now();
        let result = self.layer_stack.release_lease(&lease_id);
        let mut extra = vec![("released", json!(result.as_ref().copied().unwrap_or(false)))];
        if let Err(error) = result {
            extra.push(("error", json!(error.to_string())));
        }
        self.emit_gc_orphan("lease", lease_id, timer, &extra);
    }

    fn reap_persisted_holder(&self, row: &Value) {
        let Some(holder_pid) = persisted_i32(row, "holder_pid") else {
            return;
        };
        if holder_pid <= 0 {
            return;
        }
        let timer = Instant::now();
        let result = self
            .runtime
            .kill_holder(holder_pid, self.caps.exit_grace_s.max(0.0));
        let mut extra = Vec::new();
        if let Err(error) = result {
            extra.push(("error", json!(error.to_string())));
        }
        self.emit_gc_orphan("holder", holder_pid.to_string(), timer, &extra);
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
        let timer = Instant::now();
        self.network.teardown_veth(&allocation);
        let _ = self.network.reserve_persisted_ip(ns_ip);
        self.emit_gc_orphan("veth", host_name, timer, &[]);
    }

    fn reap_persisted_cgroup(&self, row: &Value) {
        let Some(cgroup_path) = persisted_path(row, "cgroup_path") else {
            return;
        };
        if !cgroup_path.exists() {
            return;
        }
        let timer = Instant::now();
        kill_cgroup_pids(&cgroup_path);
        let remove_result = std::fs::remove_dir(&cgroup_path);
        let mut extra = Vec::new();
        if let Err(error) = remove_result {
            extra.push(("error", json!(error.to_string())));
        }
        self.emit_gc_orphan("cgroup", path_identifier(&cgroup_path), timer, &extra);
    }

    fn reap_persisted_scratch(&self, row: &Value) {
        let Some(scratch_dir) = persisted_path(row, "scratch_dir") else {
            return;
        };
        if !scratch_dir.exists() {
            return;
        }
        let timer = Instant::now();
        let remove_result = std::fs::remove_dir_all(&scratch_dir);
        let mut extra = Vec::new();
        if let Err(error) = remove_result {
            extra.push(("error", json!(error.to_string())));
        }
        self.emit_gc_orphan("scratch", path_identifier(&scratch_dir), timer, &extra);
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
            let timer = Instant::now();
            self.network.teardown_host_veth(&name);
            self.emit_gc_orphan("veth", name, timer, &[]);
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
            let timer = Instant::now();
            kill_cgroup_pids(&path);
            let remove_result = std::fs::remove_dir(&path);
            let mut extra = Vec::new();
            if let Err(error) = remove_result {
                extra.push(("error", json!(error.to_string())));
            }
            self.emit_gc_orphan("cgroup", name, timer, &extra);
        }
    }

    fn reap_named_scratch_orphans(&self) {
        let Ok(entries) = std::fs::read_dir(self.session_scratch_root()) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if name == "manager.json" || !path.is_dir() {
                continue;
            }
            let timer = Instant::now();
            let remove_result = std::fs::remove_dir_all(&path);
            let mut extra = Vec::new();
            if let Err(error) = remove_result {
                extra.push(("error", json!(error.to_string())));
            }
            self.emit_gc_orphan("scratch", name, timer, &extra);
        }
    }

    fn emit_gc_orphan(
        &self,
        kind: &str,
        identifier: String,
        timer: Instant,
        extra: &[(&str, Value)],
    ) {
        let total_ms = timer.elapsed().as_secs_f64() * 1000.0;
        let mut payload = json!({
            "kind": kind,
            "identifier": identifier,
            "total_ms": total_ms,
            "phases_ms": {"reap": total_ms},
        });
        if let Some(object) = payload.as_object_mut() {
            for (key, value) in extra {
                object.insert((*key).to_owned(), value.clone());
            }
        }
        let _ = self
            .audit
            .emit("sandbox_isolated_workspace_gc_orphan", payload);
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

fn path_identifier(path: &Path) -> String {
    path.file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
        .map_or_else(|| path.to_string_lossy().into_owned(), ToOwned::to_owned)
}

fn kill_cgroup_pids(cgroup_path: &Path) {
    let kill_file = cgroup_path.join("cgroup.kill");
    if kill_file.exists() {
        let _ = std::fs::write(kill_file, "1\n");
    }
}
