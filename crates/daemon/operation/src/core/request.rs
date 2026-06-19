use std::path::PathBuf;

use serde_json::Value;

use crate::command::contract::{
    CancelCommandInput, CollectCompletedInput, ExecCommandInput, ReadProgressInput, WriteStdinInput,
};
use crate::control::contract::{
    CallerCountInput, CancelInvocationInput, HeartbeatInput, RuntimeReadyInput,
    TraceExportAckInput, TraceExportInput,
};
use crate::file::contract::{EditFileInput, ReadFileInput, WriteFileInput};
use crate::isolation::contract::{
    IsolationEnterInput, IsolationExitInput, IsolationStatusInput, IsolationTestCompactRemountInput,
};
use crate::workspace_run::contract::{RunCancelAllInput, RunEndInput};
use crate::CallerId;
use plugin_contract::{
    PluginHealthInput, PluginListInput, PyrightLspDefinitionInput, PyrightLspDiagnosticsInput,
    PyrightLspQuerySymbolsInput, PyrightLspReferencesInput,
};
use protocol::catalog::{BuiltinOp, ServedBy};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgsError {
    pub key: &'static str,
    pub problem: ArgProblem,
}

impl ArgsError {
    #[must_use]
    pub fn message(&self) -> String {
        match &self.problem {
            ArgProblem::Required => format!("{} is required", self.key),
            ArgProblem::MustBeString => format!("{} must be a string", self.key),
            ArgProblem::MustBeNonEmpty => format!("{} must be non-empty", self.key),
            ArgProblem::MustBeList => format!("{} must be a list", self.key),
            ArgProblem::Invalid(message) => message.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ArgProblem {
    Required,
    MustBeString,
    MustBeNonEmpty,
    MustBeList,
    Invalid(String),
}

#[derive(Debug)]
pub enum RequestError {
    NotDaemonServed(BuiltinOp),
    Args(ArgsError),
}

#[derive(Debug)]
pub enum OpRequest {
    RuntimeReady(RuntimeReadyInput),
    InvocationHeartbeat(HeartbeatInput),
    InvocationCancel(CancelInvocationInput),
    InflightCount(CallerCountInput),
    TraceExport(TraceExportInput),
    TraceExportAck(TraceExportAckInput),
    ReadFile(ReadFileInput),
    WriteFile(WriteFileInput),
    EditFile(EditFileInput),
    PluginList(PluginListInput),
    PluginHealth(PluginHealthInput),
    PyrightLspQuerySymbols(PyrightLspQuerySymbolsInput),
    PyrightLspDefinition(PyrightLspDefinitionInput),
    PyrightLspReferences(PyrightLspReferencesInput),
    PyrightLspDiagnostics(PyrightLspDiagnosticsInput),
    IsolatedNetworkEnter(IsolationEnterInput),
    IsolatedNetworkExit(IsolationExitInput),
    IsolatedNetworkStatus(IsolationStatusInput),
    IsolatedNetworkListOpen,
    IsolatedNetworkTestReset,
    IsolatedNetworkTestCompactRemount(IsolationTestCompactRemountInput),
    ExecCommand(ExecCommandInput),
    WriteStdin(WriteStdinInput),
    CommandReadProgress(ReadProgressInput),
    CommandCancel(CancelCommandInput),
    CommandCollectCompleted(CollectCompletedInput),
    CommandCount(CallerCountInput),
    CancelWorkspaceRunsByCaller(RunEndInput),
    CancelWorkspaceRuns(RunCancelAllInput),
}

impl OpRequest {
    pub fn parse(op: BuiltinOp, args: &Value, invocation_id: &str) -> Result<Self, RequestError> {
        if op.contract().served_by != ServedBy::Daemon {
            return Err(RequestError::NotDaemonServed(op));
        }
        Ok(match op {
            BuiltinOp::HostSandboxAcquire
            | BuiltinOp::HostSandboxRelease
            | BuiltinOp::HostSandboxStatus
            | BuiltinOp::HostSandboxList
            | BuiltinOp::HostTraceRequests
            | BuiltinOp::HostTraceShow
            | BuiltinOp::HostTraceVerify
            | BuiltinOp::HostImageProfilesList
            | BuiltinOp::HostImageList
            | BuiltinOp::HostImagePull
            | BuiltinOp::HostContainerList
            | BuiltinOp::HostContainerStart
            | BuiltinOp::HostContainerAdopt
            | BuiltinOp::HostContainerStop
            | BuiltinOp::HostContainerRemove => {
                unreachable!("host-served ops are rejected before daemon request parsing")
            }
            BuiltinOp::RuntimeReady => Self::RuntimeReady(RuntimeReadyInput::parse(args)?),
            BuiltinOp::InvocationHeartbeat => {
                Self::InvocationHeartbeat(HeartbeatInput::parse(args))
            }
            BuiltinOp::InvocationCancel => {
                Self::InvocationCancel(CancelInvocationInput::parse(args))
            }
            BuiltinOp::InflightCount => Self::InflightCount(CallerCountInput::parse(args)),
            BuiltinOp::TraceExport => Self::TraceExport(TraceExportInput::parse(args)),
            BuiltinOp::TraceExportAck => Self::TraceExportAck(TraceExportAckInput::parse(args)?),
            BuiltinOp::ReadFile => Self::ReadFile(ReadFileInput::parse(args)?),
            BuiltinOp::WriteFile => Self::WriteFile(WriteFileInput::parse(args)?),
            BuiltinOp::EditFile => Self::EditFile(EditFileInput::parse(args)?),
            BuiltinOp::PluginList => Self::PluginList(PluginListInput::parse(args)?),
            BuiltinOp::PluginHealth => Self::PluginHealth(PluginHealthInput::parse(args)?),
            BuiltinOp::PyrightLspQuerySymbols => {
                Self::PyrightLspQuerySymbols(PyrightLspQuerySymbolsInput::parse(args)?)
            }
            BuiltinOp::PyrightLspDefinition => {
                Self::PyrightLspDefinition(PyrightLspDefinitionInput::parse(args)?)
            }
            BuiltinOp::PyrightLspReferences => {
                Self::PyrightLspReferences(PyrightLspReferencesInput::parse(args)?)
            }
            BuiltinOp::PyrightLspDiagnostics => {
                Self::PyrightLspDiagnostics(PyrightLspDiagnosticsInput::parse(args)?)
            }
            BuiltinOp::IsolatedNetworkEnter => {
                Self::IsolatedNetworkEnter(IsolationEnterInput::parse(args)?)
            }
            BuiltinOp::IsolatedNetworkExit => {
                Self::IsolatedNetworkExit(IsolationExitInput::parse(args)?)
            }
            BuiltinOp::IsolatedNetworkStatus => {
                Self::IsolatedNetworkStatus(IsolationStatusInput::parse(args)?)
            }
            BuiltinOp::IsolatedNetworkListOpen => Self::IsolatedNetworkListOpen,
            BuiltinOp::IsolatedNetworkTestReset => Self::IsolatedNetworkTestReset,
            BuiltinOp::IsolatedNetworkTestCompactRemount => {
                Self::IsolatedNetworkTestCompactRemount(IsolationTestCompactRemountInput::parse(
                    args,
                )?)
            }
            BuiltinOp::ExecCommand => {
                Self::ExecCommand(ExecCommandInput::parse(args, invocation_id)?)
            }
            BuiltinOp::WriteStdin => Self::WriteStdin(WriteStdinInput::parse(args)?),
            BuiltinOp::CommandReadProgress => {
                Self::CommandReadProgress(ReadProgressInput::parse(args)?)
            }
            BuiltinOp::CommandCancel => Self::CommandCancel(CancelCommandInput::parse(args)?),
            BuiltinOp::CommandCollectCompleted => {
                Self::CommandCollectCompleted(CollectCompletedInput::parse(args))
            }
            BuiltinOp::CommandCount => Self::CommandCount(CallerCountInput::parse(args)),
            BuiltinOp::CancelWorkspaceRunsByCaller => {
                Self::CancelWorkspaceRunsByCaller(RunEndInput::parse(args)?)
            }
            BuiltinOp::CancelWorkspaceRuns => {
                Self::CancelWorkspaceRuns(RunCancelAllInput::parse(args))
            }
        })
    }
}

impl From<ArgsError> for RequestError {
    fn from(error: ArgsError) -> Self {
        Self::Args(error)
    }
}

impl From<plugin_contract::ArgsError> for RequestError {
    fn from(error: plugin_contract::ArgsError) -> Self {
        Self::Args(error.into())
    }
}

impl From<plugin_contract::ArgsError> for ArgsError {
    fn from(error: plugin_contract::ArgsError) -> Self {
        Self {
            key: error.key,
            problem: error.problem.into(),
        }
    }
}

impl From<plugin_contract::ArgProblem> for ArgProblem {
    fn from(problem: plugin_contract::ArgProblem) -> Self {
        match problem {
            plugin_contract::ArgProblem::Required => Self::Required,
            plugin_contract::ArgProblem::MustBeString => Self::MustBeString,
            plugin_contract::ArgProblem::MustBeNonEmpty => Self::MustBeNonEmpty,
            plugin_contract::ArgProblem::MustBeList => Self::MustBeList,
            plugin_contract::ArgProblem::Invalid(message) => Self::Invalid(message),
        }
    }
}

pub(crate) fn require_string(args: &Value, key: &'static str) -> Result<String, ArgsError> {
    let value = args
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned();
    if value.is_empty() {
        return Err(ArgsError {
            key,
            problem: ArgProblem::Required,
        });
    }
    Ok(value)
}

pub(crate) fn require_path(args: &Value, key: &'static str) -> Result<PathBuf, ArgsError> {
    require_string(args, key).map(PathBuf::from)
}

pub(crate) fn require_raw_string(args: &Value, key: &'static str) -> Result<String, ArgsError> {
    let Some(value) = args.get(key) else {
        return Err(ArgsError {
            key,
            problem: ArgProblem::Required,
        });
    };
    let Some(value) = value.as_str() else {
        return Err(ArgsError {
            key,
            problem: ArgProblem::MustBeString,
        });
    };
    Ok(value.to_owned())
}

pub(crate) fn require_command_string(args: &Value, key: &'static str) -> Result<String, ArgsError> {
    let value = args.get(key).and_then(Value::as_str).ok_or(ArgsError {
        key,
        problem: ArgProblem::Required,
    })?;
    if value.trim().is_empty() {
        return Err(ArgsError {
            key,
            problem: ArgProblem::MustBeNonEmpty,
        });
    }
    Ok(value.to_owned())
}

pub(crate) fn require_nonempty_string(
    args: &Value,
    key: &'static str,
) -> Result<String, ArgsError> {
    let value = args.get(key).and_then(Value::as_str).ok_or(ArgsError {
        key,
        problem: ArgProblem::Required,
    })?;
    if value.is_empty() {
        return Err(ArgsError {
            key,
            problem: ArgProblem::MustBeNonEmpty,
        });
    }
    Ok(value.to_owned())
}

pub(crate) fn require_caller_id(args: &Value) -> Result<CallerId, ArgsError> {
    require_string(args, "caller_id").map(CallerId::new)
}

pub(crate) fn optional_u64(args: &Value, key: &str) -> Option<u64> {
    args.get(key).and_then(Value::as_u64)
}

pub(crate) fn optional_bool(args: &Value, key: &str) -> Option<bool> {
    args.get(key).and_then(Value::as_bool)
}

pub(crate) fn optional_path(args: &Value, key: &str) -> Option<PathBuf> {
    args.get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
}

#[cfg(test)]
mod tests {
    use protocol::catalog::BUILTIN_OPS;
    use serde_json::json;

    use super::*;

    #[test]
    fn caller_id_default_happens_before_trim() {
        let default = CallerId::from_wire(&json!({}));
        assert_eq!(default.as_str(), "default");

        let blank = CallerId::from_wire(&json!({"caller_id": "   "}));
        assert_eq!(blank.as_str(), "");
    }

    #[test]
    fn edit_parse_checks_edits_before_path() {
        let error = OpRequest::parse(BuiltinOp::EditFile, &json!({}), "request-1")
            .expect_err("edit parse should reject missing edits before path");
        let RequestError::Args(error) = error else {
            panic!("expected args error");
        };
        assert_eq!(error.message(), "edits must be a list");
    }

    #[test]
    fn command_poll_checks_command_id_before_last_n_lines_conversion() {
        let error = OpRequest::parse(
            BuiltinOp::CommandReadProgress,
            &json!({"last_n_lines": u64::MAX}),
            "request-1",
        )
        .expect_err("command poll parse should require command id before line conversion");
        let RequestError::Args(error) = error else {
            panic!("expected args error");
        };
        assert_eq!(error.message(), "command_id is required");
    }

    #[test]
    fn host_ops_are_not_daemon_served() {
        let error = OpRequest::parse(BuiltinOp::HostSandboxAcquire, &json!({}), "request-1")
            .expect_err("host sandbox ops are not served by the daemon");
        assert!(matches!(
            error,
            RequestError::NotDaemonServed(BuiltinOp::HostSandboxAcquire)
        ));
    }

    #[test]
    fn parser_rejects_host_served_ops_from_catalog() {
        for contract in BUILTIN_OPS
            .iter()
            .filter(|contract| contract.served_by == ServedBy::Host)
        {
            let error = OpRequest::parse(contract.op, &json!({}), "request-1")
                .expect_err("host-served op must not parse as daemon request");
            assert!(
                matches!(error, RequestError::NotDaemonServed(op) if op == contract.op),
                "{} must be rejected by catalog served_by",
                contract.name
            );
        }
    }

    #[test]
    fn exec_uses_top_level_invocation_id() {
        let parsed = OpRequest::parse(
            BuiltinOp::ExecCommand,
            &json!({
                "cmd": "printf ok",
                "caller_id": "caller-1",
                "invocation_id": "args-should-not-win"
            }),
            "request-wins",
        )
        .expect("exec input should parse");
        let OpRequest::ExecCommand(input) = parsed else {
            panic!("exec op parses to exec input");
        };
        assert_eq!(input.invocation_id.as_str(), "request-wins");
    }
}
