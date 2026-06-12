//! Daemon↔namespace-runner wire DTOs.

use std::os::unix::io::RawFd;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde::{Deserializer, Serializer};
use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Intent {
    ReadOnly,
    WriteAllowed,
    Lifecycle,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunMode {
    FreshNs,
    SetNs,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[repr(transparent)]
pub struct Fd(pub RawFd);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceRoot(pub PathBuf);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct NsFds {
    pub user: Option<Fd>,
    pub mnt: Option<Fd>,
    pub pid: Option<Fd>,
    pub net: Option<Fd>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RunnerVerb {
    ExecCommand,
    PluginService,
    Unknown(String),
}

impl From<&str> for RunnerVerb {
    fn from(value: &str) -> Self {
        match value {
            "exec_command" => Self::ExecCommand,
            "plugin_service" => Self::PluginService,
            other => Self::Unknown(other.to_owned()),
        }
    }
}

impl Serialize for RunnerVerb {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(match self {
            Self::ExecCommand => "exec_command",
            Self::PluginService => "plugin_service",
            Self::Unknown(verb) => verb,
        })
    }
}

impl<'de> Deserialize<'de> for RunnerVerb {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        String::deserialize(deserializer).map(|value| Self::from(value.as_str()))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ToolCall {
    pub invocation_id: String,
    pub caller_id: String,
    pub verb: RunnerVerb,
    pub intent: Intent,
    pub args: Value,
    #[serde(default)]
    pub background: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunRequest {
    pub mode: RunMode,
    pub tool_call: ToolCall,
    pub workspace_root: WorkspaceRoot,
    #[serde(default)]
    pub layer_paths: Vec<PathBuf>,
    #[serde(default)]
    pub upperdir: Option<PathBuf>,
    #[serde(default)]
    pub workdir: Option<PathBuf>,
    #[serde(default)]
    pub ns_fds: Option<NsFds>,
    #[serde(default)]
    pub cgroup_path: Option<PathBuf>,
    #[serde(default)]
    pub timeout_seconds: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunResult {
    pub exit_code: i32,
    pub payload: Value,
}

#[cfg(test)]
#[path = "../../tests/unit/protocol/intent.rs"]
mod intent_tests;

#[cfg(test)]
#[path = "../../tests/unit/protocol/runner.rs"]
mod runner_tests;
