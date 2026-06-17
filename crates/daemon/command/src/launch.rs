use std::path::{Path, PathBuf};

use linux_namespace_subprocess::protocol::{
    Fd, NsFds, RunMode, RunRequest, RunnerVerb, ToolCall, WorkspaceRoot,
};
use serde_json::{json, Value};

use crate::CommandError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CommandLaunchNamespaceFds {
    pub user: Option<i32>,
    pub mnt: Option<i32>,
    pub pid: Option<i32>,
    pub net: Option<i32>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommandExecRunRequest {
    pub command_id: String,
    pub caller_id: String,
    pub command: String,
    pub cwd: Option<PathBuf>,
    pub timeout_seconds: Option<f64>,
    pub workspace_root: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
    pub namespace_fds: Option<CommandLaunchNamespaceFds>,
    pub cgroup_path: Option<PathBuf>,
}

pub fn build_exec_run_request(request: CommandExecRunRequest) -> Result<Value, CommandError> {
    let mode = if request.namespace_fds.is_some() {
        RunMode::SetNs
    } else {
        RunMode::FreshNs
    };
    let cwd = request
        .cwd
        .as_deref()
        .unwrap_or_else(|| Path::new("."))
        .to_string_lossy()
        .into_owned();
    let run_request = RunRequest {
        mode,
        tool_call: ToolCall {
            invocation_id: request.command_id,
            caller_id: request.caller_id,
            verb: RunnerVerb::ExecCommand,
            args: json!({
                "command": request.command,
                "cwd": cwd,
            }),
            background: false,
        },
        workspace_root: WorkspaceRoot(request.workspace_root),
        layer_paths: request.layer_paths,
        upperdir: Some(request.upperdir),
        workdir: Some(request.workdir),
        ns_fds: request.namespace_fds.map(Into::into),
        cgroup_path: request.cgroup_path,
        timeout_seconds: request.timeout_seconds,
    };
    serde_json::to_value(run_request).map_err(|error| {
        CommandError::InvalidRequest(format!("serialize command runner request: {error}"))
    })
}

impl From<CommandLaunchNamespaceFds> for NsFds {
    fn from(fds: CommandLaunchNamespaceFds) -> Self {
        let fd = |raw: Option<i32>| raw.map(Fd);
        Self {
            user: fd(fds.user),
            mnt: fd(fds.mnt),
            pid: fd(fds.pid),
            net: fd(fds.net),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_exec_run_request_uses_fresh_namespace_without_namespace_fds() {
        let request = build_exec_run_request(CommandExecRunRequest {
            command_id: "cmd_1".to_owned(),
            caller_id: "caller-1".to_owned(),
            command: "printf ok".to_owned(),
            cwd: None,
            timeout_seconds: Some(1.0),
            workspace_root: "/workspace".into(),
            layer_paths: vec!["/lower".into()],
            upperdir: "/upper".into(),
            workdir: "/work".into(),
            namespace_fds: None,
            cgroup_path: None,
        })
        .expect("request serializes");

        assert_eq!(request["mode"], "fresh_ns");
        assert_eq!(request["tool_call"]["caller_id"], "caller-1");
        assert_eq!(request["tool_call"]["args"]["command"], "printf ok");
        assert_eq!(request["tool_call"]["args"]["cwd"], ".");
        assert!(request["tool_call"]["args"].get("remountable").is_none());
    }

    #[test]
    fn build_exec_run_request_uses_setns_with_namespace_fds() {
        let request = build_exec_run_request(CommandExecRunRequest {
            command_id: "cmd_1".to_owned(),
            caller_id: "caller-1".to_owned(),
            command: "pwd".to_owned(),
            cwd: Some("/workspace/src".into()),
            timeout_seconds: None,
            workspace_root: "/workspace".into(),
            layer_paths: vec!["/lower".into()],
            upperdir: "/upper".into(),
            workdir: "/work".into(),
            namespace_fds: Some(CommandLaunchNamespaceFds {
                user: Some(10),
                mnt: Some(11),
                pid: Some(12),
                net: None,
            }),
            cgroup_path: Some("/sys/fs/cgroup/eos".into()),
        })
        .expect("request serializes");

        assert_eq!(request["mode"], "set_ns");
        assert_eq!(request["tool_call"]["args"]["cwd"], "/workspace/src");
        assert_eq!(request["ns_fds"]["mnt"], 11);
        assert_eq!(request["cgroup_path"], "/sys/fs/cgroup/eos");
    }
}
