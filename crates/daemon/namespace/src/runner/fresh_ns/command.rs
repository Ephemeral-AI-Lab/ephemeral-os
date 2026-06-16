//! Argv, cwd, and environment construction for fresh-ns command execution.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Component, Path, PathBuf};

use crate::protocol::RunRequest;
use crate::runner::RunnerError;

pub(super) fn plugin_service_argv(request: &RunRequest) -> Result<Vec<String>, RunnerError> {
    argv_command(request, "plugin_service")
}

pub(super) fn plugin_setup_argv(request: &RunRequest) -> Result<Vec<String>, RunnerError> {
    argv_command(request, "plugin_setup")
}

fn argv_command(request: &RunRequest, label: &str) -> Result<Vec<String>, RunnerError> {
    let Some(command) = request.tool_call.args.get("command") else {
        return Err(RunnerError::InvalidRequest(format!(
            "{label} requires command argv"
        )));
    };
    let parts = command.as_array().ok_or_else(|| {
        RunnerError::InvalidRequest(format!("{label} command must be an argv list"))
    })?;
    if parts.is_empty() {
        return Err(RunnerError::InvalidRequest(format!(
            "{label} command argv must not be empty"
        )));
    }
    let argv: Result<Vec<String>, RunnerError> = parts
        .iter()
        .map(|part| {
            part.as_str().map_or_else(
                || {
                    Err(RunnerError::InvalidRequest(format!(
                        "{label} command argv entries must be strings"
                    )))
                },
                |value| Ok(value.to_owned()),
            )
        })
        .collect();
    let argv = argv?;
    if argv[0].trim().is_empty() {
        return Err(RunnerError::InvalidRequest(format!(
            "{label} command argv[0] must not be empty"
        )));
    }
    Ok(argv)
}

pub(super) fn shell_argv(request: &RunRequest) -> Result<Vec<String>, RunnerError> {
    let shell_args = &request.tool_call.args;
    let Some(command) = shell_args.get("command") else {
        return Err(RunnerError::InvalidRequest(
            "shell args require command".to_owned(),
        ));
    };
    if let Some(value) = command.as_str() {
        let command = value.trim();
        if command.is_empty() {
            return Err(RunnerError::InvalidRequest(
                "shell command string must not be empty".to_owned(),
            ));
        }
        return Ok(vec![
            "/bin/bash".to_owned(),
            "--noprofile".to_owned(),
            "--norc".to_owned(),
            "-c".to_owned(),
            value.to_owned(),
        ]);
    }
    Err(RunnerError::InvalidRequest(
        "exec_command requires a shell-format command string".to_owned(),
    ))
}

pub(super) fn shell_cwd(request: &RunRequest) -> Result<PathBuf, RunnerError> {
    let raw = request
        .tool_call
        .args
        .get("cwd")
        .and_then(serde_json::Value::as_str)
        .unwrap_or(".");
    let allow_external_cwd = request
        .tool_call
        .args
        .get("remountable")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);
    let workspace_root = normalize_lexical(&request.workspace_root.0);
    let candidate = PathBuf::from(raw);
    let resolved = if candidate.is_absolute() {
        let candidate = normalize_lexical(&candidate);
        if candidate.starts_with(&workspace_root) {
            let rel = candidate.strip_prefix(&workspace_root).map_err(|_| {
                RunnerError::InvalidRequest(format!(
                    "cwd escapes workspace replacement root: {raw}"
                ))
            })?;
            workspace_root.join(rel)
        } else if allow_external_cwd {
            candidate
        } else {
            return Err(RunnerError::InvalidRequest(format!(
                "cwd escapes workspace replacement root: {raw}"
            )));
        }
    } else {
        normalize_lexical(&workspace_root.join(candidate))
    };
    if !allow_external_cwd && !resolved.starts_with(&workspace_root) {
        return Err(RunnerError::InvalidRequest(format!(
            "cwd escapes workspace replacement root: {raw}"
        )));
    }
    fs::create_dir_all(&resolved).map_err(RunnerError::Child)?;
    Ok(resolved)
}

pub(super) fn plugin_setup_cwd(request: &RunRequest) -> Result<PathBuf, RunnerError> {
    let package_root = setup_path_arg(request, "package_root")?;
    let raw = request
        .tool_call
        .args
        .get("cwd")
        .and_then(serde_json::Value::as_str)
        .unwrap_or(".");
    let candidate = PathBuf::from(raw);
    let resolved = if candidate.is_absolute() {
        normalize_lexical(&candidate)
    } else {
        normalize_lexical(&package_root.join(candidate))
    };
    if !resolved.starts_with(&package_root) {
        return Err(RunnerError::InvalidRequest(format!(
            "plugin_setup cwd escapes package root: {raw}"
        )));
    }
    Ok(resolved)
}

pub(super) fn setup_path_arg(request: &RunRequest, key: &str) -> Result<PathBuf, RunnerError> {
    request
        .tool_call
        .args
        .get(key)
        .and_then(serde_json::Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| RunnerError::InvalidRequest(format!("plugin_setup requires {key}")))
}

fn normalize_lexical(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other.as_os_str()),
        }
    }
    normalized
}

pub(super) fn command_environment(args: &serde_json::Value) -> BTreeMap<String, String> {
    const HOST_KEYS: &[&str] = &["PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TZ"];
    const RESTRICTED: &[&str] = &[
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "PATH",
        "PYTHONPATH",
        "BASH_ENV",
        "ENV",
    ];

    const DEFAULT_PATH: &str = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";

    let mut env = BTreeMap::new();
    for key in HOST_KEYS {
        if let Ok(value) = std::env::var(key) {
            env.insert((*key).to_owned(), value);
        }
    }
    let suffix = env
        .get("PATH")
        .filter(|path| !path.is_empty())
        .cloned()
        .unwrap_or_else(|| DEFAULT_PATH.to_owned());
    env.insert(
        "PATH".to_owned(),
        format!("/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:{suffix}"),
    );
    if let Some(extra) = args.get("env").and_then(serde_json::Value::as_object) {
        for (key, value) in extra {
            if !RESTRICTED.contains(&key.as_str()) {
                env.insert(
                    key.to_owned(),
                    value
                        .as_str()
                        .map_or_else(|| value.to_string(), str::to_owned),
                );
            }
        }
    }
    env.insert("GIT_OPTIONAL_LOCKS".to_owned(), "0".to_owned());
    env
}

pub(super) fn setup_environment(args: &serde_json::Value) -> BTreeMap<String, String> {
    args.get("env")
        .and_then(serde_json::Value::as_object)
        .map(|env| {
            env.iter()
                .filter_map(|(key, value)| {
                    value.as_str().map(|value| (key.clone(), value.to_owned()))
                })
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
#[path = "../../../tests/unit/runner/fresh_ns/command.rs"]
mod tests;
