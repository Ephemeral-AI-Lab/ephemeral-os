use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::control::contract::CallerCountInput;
use crate::core::request::{
    optional_path, optional_u64, require_command_string, require_nonempty_string, ArgProblem,
    ArgsError,
};
use crate::{CallerId, CommandSessionId, InvocationId, MutationCore, WorkspaceKind};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExecCommandInput {
    pub cmd: String,
    pub caller: CallerId,
    pub layer_stack_root: Option<PathBuf>,
    pub timeout: Option<u64>,
    pub timeout_seconds: Option<u64>,
    pub yield_time_ms: Option<u64>,
    pub invocation_id: InvocationId,
}

impl ExecCommandInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            cmd: require_command_string(args, "cmd")?,
            caller: CallerId::from_wire(args),
            layer_stack_root: optional_path(args, "layer_stack_root"),
            timeout: optional_u64(args, "timeout"),
            timeout_seconds: optional_u64(args, "timeout_seconds"),
            yield_time_ms: optional_u64(args, "yield_time_ms"),
            invocation_id: InvocationId::new(
                args.get("invocation_id")
                    .and_then(Value::as_str)
                    .unwrap_or("exec_command")
                    .to_owned(),
            ),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriteStdinInput {
    pub command_session_id: CommandSessionId,
    pub chars: String,
    pub yield_time_ms: Option<u64>,
}

impl WriteStdinInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            command_session_id: CommandSessionId::new(require_command_string(
                args,
                "command_session_id",
            )?),
            chars: require_nonempty_string(args, "chars")?,
            yield_time_ms: optional_u64(args, "yield_time_ms"),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReadProgressInput {
    pub command_session_id: CommandSessionId,
    pub last_n_lines: usize,
}

impl ReadProgressInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        let command_session_id =
            CommandSessionId::new(require_command_string(args, "command_session_id")?);
        let last_n_lines = optional_u64(args, "last_n_lines").unwrap_or(50);
        Ok(Self {
            command_session_id,
            last_n_lines: last_n_lines.try_into().map_err(|_| ArgsError {
                key: "last_n_lines",
                problem: ArgProblem::Invalid("last_n_lines is too large".to_owned()),
            })?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CancelCommandInput {
    pub command_session_id: CommandSessionId,
}

impl CancelCommandInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            command_session_id: CommandSessionId::new(require_command_string(
                args,
                "command_session_id",
            )?),
        })
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CollectCompletedInput {
    pub command_session_ids: Option<Vec<CommandSessionId>>,
    pub caller: Option<CallerId>,
}

impl CollectCompletedInput {
    pub(crate) fn parse(args: &Value) -> Self {
        let command_session_ids = args
            .get("command_session_ids")
            .and_then(Value::as_array)
            .map(|ids| {
                ids.iter()
                    .filter_map(Value::as_str)
                    .map(CommandSessionId::new)
                    .collect::<Vec<_>>()
            });
        let caller = CallerId::from_wire(args);
        Self {
            command_session_ids,
            caller: (!caller.as_str().is_empty()).then_some(caller),
        }
    }
}

pub type CommandSessionCountInput = CallerCountInput;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CommandStatus {
    Running,
    Ok,
    Cancelled,
    Error,
    TimedOut,
}

impl CommandStatus {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Ok => "ok",
            Self::Cancelled => "cancelled",
            Self::Error => "error",
            Self::TimedOut => "timed_out",
        }
    }

    #[must_use]
    pub fn from_wire_str(raw: &str) -> Option<Self> {
        match raw {
            "running" => Some(Self::Running),
            "ok" => Some(Self::Ok),
            "cancelled" => Some(Self::Cancelled),
            "error" => Some(Self::Error),
            "timed_out" => Some(Self::TimedOut),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandMetadata {
    #[serde(flatten)]
    pub core: MutationCore,
    pub workspace: WorkspaceKind,
    #[serde(flatten)]
    pub extras: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandResponse {
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    pub command_session_id: Option<CommandSessionId>,
    pub settled: Option<CommandMetadata>,
}

impl CommandResponse {
    #[must_use]
    pub fn running(command_session_id: String, stdout: String) -> Self {
        Self {
            status: CommandStatus::Running,
            exit_code: None,
            stdout,
            stderr: String::new(),
            command_session_id: Some(CommandSessionId::new(command_session_id)),
            settled: None,
        }
    }

    #[must_use]
    pub fn cancelled(stdout: String) -> Self {
        Self {
            status: CommandStatus::Cancelled,
            exit_code: None,
            stdout,
            stderr: String::new(),
            command_session_id: None,
            settled: None,
        }
    }

    #[must_use]
    pub fn error(stderr: impl Into<String>) -> Self {
        Self {
            status: CommandStatus::Error,
            exit_code: None,
            stdout: String::new(),
            stderr: stderr.into(),
            command_session_id: None,
            settled: None,
        }
    }

    #[must_use]
    pub fn with_last_lines(mut self, last_n_lines: usize) -> Self {
        self.stdout = eos_command_session::tail_lines(&self.stdout, last_n_lines);
        self
    }

    #[must_use]
    pub fn to_wire_value(&self) -> Value {
        let mut response = json!({
            "status": self.status.as_str(),
            "exit_code": self.exit_code,
            "output": {
                "stdout": self.stdout,
                "stderr": self.stderr,
            },
        });
        if let Some(command_session_id) = self.command_session_id.as_ref() {
            response["command_session_id"] = json!(command_session_id.as_str());
        }
        let Some(settled) = self.settled.as_ref() else {
            return response;
        };
        let Value::Object(core) =
            serde_json::to_value(&settled.core).expect("serialize command mutation core")
        else {
            unreachable!("MutationCore serializes to a JSON object");
        };
        let object = response
            .as_object_mut()
            .expect("command response starts as a JSON object");
        for (key, value) in core {
            object.insert(key, value);
        }
        object.insert("workspace".to_owned(), json!(settled.workspace.as_str()));
        for (key, value) in &settled.extras {
            object.insert(key.clone(), value.clone());
        }
        response
    }
}

pub(crate) fn u64_to_f64_saturating(value: u64) -> f64 {
    const U32_FACTOR: f64 = 4_294_967_296.0;
    let high = u32::try_from(value >> 32).unwrap_or(u32::MAX);
    let low = u32::try_from(value & u64::from(u32::MAX)).unwrap_or(u32::MAX);
    f64::from(high).mul_add(U32_FACTOR, f64::from(low))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandSessionCompletion {
    pub command_session_id: String,
    pub caller_id: String,
    pub command: String,
    pub result: CommandResponse,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectCompletedResponse {
    pub success: bool,
    pub completions: Vec<CommandSessionCompletion>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ChangedPathKind, MutationSource, WorkspaceConflict};

    #[test]
    fn settled_response_splices_typed_metadata_extras() {
        let mut changed_path_kinds = crate::ChangedPathKinds::default();
        changed_path_kinds.insert("src/main.rs".to_owned(), ChangedPathKind::Write);
        let mut extras = Map::new();
        extras.insert(
            "isolated_workspace".to_owned(),
            json!({"caller_id": "caller", "published": false}),
        );
        extras.insert("warnings".to_owned(), json!([]));

        let response = CommandResponse {
            status: CommandStatus::Ok,
            exit_code: Some(0),
            stdout: "done\n".to_owned(),
            stderr: String::new(),
            command_session_id: Some(CommandSessionId::new("cmd_1")),
            settled: Some(CommandMetadata {
                core: MutationCore {
                    success: true,
                    changed_paths: vec!["src/main.rs".to_owned()],
                    changed_path_kinds,
                    mutation_source: Some(MutationSource::IsolatedWorkspace),
                    conflict: None,
                    conflict_reason: None,
                    timings: crate::WorkspaceTimings::default(),
                },
                workspace: WorkspaceKind::Isolated,
                extras,
            }),
        }
        .to_wire_value();

        assert_eq!(response["status"], "ok");
        assert_eq!(response["command_session_id"], "cmd_1");
        assert_eq!(response["workspace"], "isolated");
        assert_eq!(response["success"], true);
        assert_eq!(response["changed_paths"], json!(["src/main.rs"]));
        assert_eq!(
            response["changed_path_kinds"],
            json!({"src/main.rs": "write"})
        );
        assert_eq!(response["mutation_source"], "isolated_workspace");
        assert_eq!(response["isolated_workspace"]["caller_id"], "caller");
        assert_eq!(response["warnings"], json!([]));
    }

    #[test]
    fn discarded_response_renders_empty_mutation_source() {
        let response = CommandResponse {
            status: CommandStatus::Cancelled,
            exit_code: Some(130),
            stdout: String::new(),
            stderr: String::new(),
            command_session_id: None,
            settled: Some(CommandMetadata {
                core: MutationCore {
                    success: false,
                    mutation_source: None,
                    ..MutationCore::default()
                },
                workspace: WorkspaceKind::Ephemeral,
                extras: Map::new(),
            }),
        }
        .to_wire_value();

        assert_eq!(response["status"], "cancelled");
        assert_eq!(response["workspace"], "ephemeral");
        assert_eq!(response["mutation_source"], "");
    }

    #[test]
    fn conflict_settlement_matches_contract_fixture() {
        let mut changed_path_kinds = crate::ChangedPathKinds::default();
        changed_path_kinds.insert("src/main.rs".to_owned(), ChangedPathKind::Write);
        let mut timings = crate::WorkspaceTimings::default();
        timings.insert("command_exec.total_s".to_owned(), json!(1.25));

        let response = CommandResponse {
            status: CommandStatus::Ok,
            exit_code: Some(0),
            stdout: String::new(),
            stderr: String::new(),
            command_session_id: Some(CommandSessionId::new("cmd_conflict")),
            settled: Some(CommandMetadata {
                core: MutationCore {
                    success: false,
                    changed_paths: vec!["src/main.rs".to_owned()],
                    changed_path_kinds,
                    mutation_source: Some(MutationSource::OverlayCapture),
                    conflict: Some(WorkspaceConflict::path(
                        "aborted_overlap",
                        "src/main.rs",
                        "conflict on src/main.rs",
                    )),
                    conflict_reason: Some("conflict on src/main.rs".to_owned()),
                    timings,
                },
                workspace: WorkspaceKind::Ephemeral,
                extras: Map::new(),
            }),
        }
        .to_wire_value();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../../../contract/fixtures/command_settle_conflict_response.json"
        ))
        .expect("valid command settle conflict fixture");

        assert_eq!(response, fixture);
    }
}
