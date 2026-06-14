use std::fs;
use std::io::Read;
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use config::configs::daemon::PyrightLspConfig;
#[cfg(unix)]
use nix::sys::signal::{kill, Signal};
#[cfg(unix)]
use nix::unistd::Pid;

use crate::PluginRuntimeError;

const PYRIGHT_PYPI_VERSION: &str = "1.1.410";

pub(super) fn resolve_pyright_command(
    config: &PyrightLspConfig,
) -> Result<Vec<String>, PluginRuntimeError> {
    if config.node_path.exists() && config.pyright_langserver_path.exists() {
        return Ok(vec![
            config.node_path.to_string_lossy().into_owned(),
            config
                .pyright_langserver_path
                .to_string_lossy()
                .into_owned(),
            "--stdio".to_owned(),
        ]);
    }
    if let Some(path) = find_executable("pyright-langserver") {
        return Ok(vec![
            path.to_string_lossy().into_owned(),
            "--stdio".to_owned(),
        ]);
    }
    for candidate in [
        "/usr/local/bin/pyright-langserver",
        "/usr/bin/pyright-langserver",
        "/opt/miniconda3/bin/pyright-langserver",
        "/opt/miniconda3/envs/testbed/bin/pyright-langserver",
    ] {
        let path = PathBuf::from(candidate);
        if path.exists() {
            return Ok(vec![candidate.to_owned(), "--stdio".to_owned()]);
        }
    }
    if let Some(command) = provision_python_pyright_command(config)? {
        return Ok(command);
    }
    Err(PluginRuntimeError::PyrightLsp(format!(
        "pyright_lsp could not find configured node/langserver ({}, {}) or pyright-langserver in PATH",
        config.node_path.display(),
        config.pyright_langserver_path.display()
    )))
}

fn provision_python_pyright_command(
    config: &PyrightLspConfig,
) -> Result<Option<Vec<String>>, PluginRuntimeError> {
    let Some(python) = find_python() else {
        return Ok(None);
    };
    let root = pyright_python_root(config)?;
    let marker = root.join("pyright").join("langserver.py");
    if !marker.exists() {
        fs::create_dir_all(&root).map_err(|err| {
            PluginRuntimeError::PyrightLsp(format!(
                "create pyright_lsp Python asset root {}: {err}",
                root.display()
            ))
        })?;
        let mut command = Command::new(&python);
        command
            .args([
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--target",
            ])
            .arg(&root)
            .arg(format!("pyright=={PYRIGHT_PYPI_VERSION}"))
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        command.process_group(0);
        let mut child = command.spawn().map_err(|err| {
            PluginRuntimeError::PyrightLsp(format!(
                "spawn pyright_lsp Python asset install with {}: {err}",
                python.display()
            ))
        })?;
        let output = wait_for_pip_install(&mut child, config.refresh_timeout_ms)?;
        if !output.status.success() {
            return Err(PluginRuntimeError::PyrightLsp(format!(
                "install pyright_lsp Python assets failed: {}",
                bounded_command_output(&output.stderr, &output.stdout)
            )));
        }
    }
    Ok(Some(vec![
        "/bin/sh".to_owned(),
        "-lc".to_owned(),
        format!(
            "PYTHONPATH={} exec {} -m pyright.langserver --stdio",
            shell_quote(&root),
            shell_quote(&python)
        ),
    ]))
}

fn wait_for_pip_install(child: &mut Child, timeout_ms: u64) -> Result<Output, PluginRuntimeError> {
    let status = wait_for_child_status(child, timeout_ms)?;
    let stdout = read_child_pipe(child.stdout.take())?;
    let stderr = read_child_pipe(child.stderr.take())?;
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

fn wait_for_child_status(
    child: &mut Child,
    timeout_ms: u64,
) -> Result<ExitStatus, PluginRuntimeError> {
    let deadline = Instant::now() + Duration::from_millis(timeout_ms.max(1));
    loop {
        if let Some(status) = child.try_wait().map_err(|err| {
            PluginRuntimeError::PyrightLsp(format!(
                "wait for pyright_lsp Python asset install: {err}"
            ))
        })? {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            terminate_child_tree(child);
            let _ = child.wait();
            return Err(PluginRuntimeError::PyrightLsp(format!(
                "install pyright_lsp Python assets timed out after {timeout_ms}ms"
            )));
        }
        thread::sleep(Duration::from_millis(10));
    }
}

fn terminate_child_tree(child: &mut Child) {
    #[cfg(unix)]
    {
        if let Ok(pid) = i32::try_from(child.id()) {
            let _ = kill(Pid::from_raw(-pid), Signal::SIGKILL);
            let _ = kill(Pid::from_raw(pid), Signal::SIGKILL);
            return;
        }
    }
    let _ = child.kill();
}

fn read_child_pipe<R: Read>(pipe: Option<R>) -> Result<Vec<u8>, PluginRuntimeError> {
    let Some(mut pipe) = pipe else {
        return Ok(Vec::new());
    };
    let mut bytes = Vec::new();
    pipe.read_to_end(&mut bytes).map_err(|err| {
        PluginRuntimeError::PyrightLsp(format!(
            "read pyright_lsp Python asset install output: {err}"
        ))
    })?;
    Ok(bytes)
}

fn pyright_python_root(config: &PyrightLspConfig) -> Result<PathBuf, PluginRuntimeError> {
    let parent = config.workspace_root.parent().ok_or_else(|| {
        PluginRuntimeError::PyrightLsp(format!(
            "pyright_lsp workspace_root has no parent: {}",
            config.workspace_root.display()
        ))
    })?;
    Ok(parent.join("python"))
}

fn find_python() -> Option<PathBuf> {
    find_executable("python3")
        .or_else(|| find_executable("python"))
        .or_else(|| {
            [
                "/opt/miniconda3/bin/python",
                "/usr/local/bin/python3",
                "/usr/bin/python3",
            ]
            .into_iter()
            .map(PathBuf::from)
            .find(|candidate| candidate.exists())
        })
}

fn shell_quote(path: &Path) -> String {
    let value = path.to_string_lossy();
    format!("'{}'", value.replace('\'', "'\\''"))
}

fn bounded_command_output(stderr: &[u8], stdout: &[u8]) -> String {
    let mut text = String::new();
    if !stderr.is_empty() {
        text.push_str("stderr=");
        text.push_str(&String::from_utf8_lossy(stderr));
    }
    if !stdout.is_empty() {
        if !text.is_empty() {
            text.push_str("; ");
        }
        text.push_str("stdout=");
        text.push_str(&String::from_utf8_lossy(stdout));
    }
    const LIMIT: usize = 4096;
    if text.len() > LIMIT {
        text.truncate(LIMIT);
        text.push_str("...");
    }
    text
}

fn find_executable(name: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|path| {
        std::env::split_paths(&path)
            .map(|dir| dir.join(name))
            .find(|candidate| candidate.exists())
    })
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn wait_for_child_status_times_out_and_reaps_process_group(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let mut command = Command::new("sh");
        command
            .arg("-c")
            .arg("sleep 60")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0);
        let mut child = command.spawn()?;

        let error =
            wait_for_child_status(&mut child, 1).expect_err("sleeping child should time out");

        assert!(error.to_string().contains("timed out"));
        assert!(
            child.try_wait()?.is_some(),
            "timed out child should be reaped"
        );
        Ok(())
    }
}
