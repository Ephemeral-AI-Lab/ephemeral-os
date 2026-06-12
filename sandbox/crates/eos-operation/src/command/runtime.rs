use std::sync::{OnceLock, RwLock};
use std::time::Instant;

use eos_command::CommandConfig;
use serde_json::Value;

use super::contract::{CommandCompletion, CommandResponse, CommandStatus};
use super::service::CommandOps;
use crate::CommandId;

pub fn command_ops() -> &'static CommandOps {
    static OPS: OnceLock<CommandOps> = OnceLock::new();
    OPS.get_or_init(|| CommandOps::new(command_config()))
}

pub fn configure_commands(config: &CommandConfig) {
    let mut guard = command_config_cell()
        .write()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    *guard = config.clone();
}

pub fn command_config() -> CommandConfig {
    command_config_cell()
        .read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone()
}

pub fn command_scratch_root() -> std::path::PathBuf {
    command_config().scratch_root
}

fn command_config_cell() -> &'static RwLock<CommandConfig> {
    static CONFIG: OnceLock<RwLock<CommandConfig>> = OnceLock::new();
    CONFIG.get_or_init(|| RwLock::new(CommandConfig::default()))
}

#[must_use]
pub fn active_commands_for_caller(caller_id: &str) -> usize {
    let caller_id = caller_id.trim();
    if caller_id.is_empty() {
        return 0;
    }
    command_ops().count_by_caller(Some(caller_id))
}

pub fn cleanup_commands_for_caller(caller_id: &str, grace_s: Option<f64>) -> usize {
    command_ops().cleanup_caller(caller_id, grace_s)
}

pub fn cancel_all_commands(grace_s: Option<f64>) -> usize {
    command_ops().cancel_all(grace_s)
}

pub fn advance_active_commands_once() {
    command_ops().advance_active_commands_once(Instant::now());
}

pub fn recover_orphaned_commands() {
    let dir = command_scratch_root();
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        if let Ok(bytes) = std::fs::read(path.join("metadata.json")) {
            if let Ok(meta) = serde_json::from_slice::<Value>(&bytes) {
                let id = meta
                    .get("command_id")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                if !id.is_empty() {
                    let caller_id = meta
                        .get("caller_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let command = meta
                        .get("command")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let result = CommandResponse {
                        status: CommandStatus::Error,
                        exit_code: Some(1),
                        stdout: String::new(),
                        stderr: "orphan_recovered: daemon restarted".to_owned(),
                        command_id: Some(CommandId::new(id.to_owned())),
                        finalized: None,
                    };
                    command_ops().push_completed(CommandCompletion {
                        command_id: id.to_owned(),
                        caller_id: caller_id.to_owned(),
                        command: command.to_owned(),
                        result,
                    });
                }
            }
        }
        let _ = std::fs::remove_dir_all(&path);
    }
}
