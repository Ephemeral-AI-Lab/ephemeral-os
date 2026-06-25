use std::path::PathBuf;

use sandbox_config::configs::manager::DockerRuntimeConfig;
use sandbox_manager::{SandboxId, SandboxRecord, SandboxState};
use sandbox_provider_docker::daemon_launch_argv;

fn record(id: &str) -> SandboxRecord {
    SandboxRecord::new(
        SandboxId::new(id).expect("valid sandbox id"),
        PathBuf::from("/host/workspace"),
        SandboxState::Creating,
    )
}

#[test]
fn daemon_launch_argv_uses_container_paths_and_tcp_flags() {
    let config = DockerRuntimeConfig {
        container_daemon_binary_path: PathBuf::from("/eos/bin/sandbox-daemon"),
        container_daemon_config_yaml_path: PathBuf::from("/eos/config/daemon.yml"),
        container_workspace_root: PathBuf::from("/workspace"),
        daemon_port: 7000,
        ..DockerRuntimeConfig::default()
    };

    let argv = daemon_launch_argv(&config, &record("eos-abc"), "tok-123");

    assert_eq!(argv[0], "/eos/bin/sandbox-daemon");
    assert_eq!(argv[1], "serve");
    assert!(has_flag(&argv, "--config-yaml", "/eos/config/daemon.yml"));
    assert!(has_flag(&argv, "--workspace-root", "/workspace"));
    assert!(has_flag(&argv, "--tcp-host", "0.0.0.0"));
    assert!(has_flag(&argv, "--tcp-port", "7000"));
    assert!(has_flag(&argv, "--auth-token", "tok-123"));
    assert!(has_flag(&argv, "--sandbox-id", "eos-abc"));
    // The container runs the daemon as its foreground Cmd, not a host-process spawn.
    assert!(!argv.iter().any(|arg| arg == "--spawn"));
}

#[test]
fn daemon_launch_argv_honors_custom_daemon_port() {
    let config = DockerRuntimeConfig {
        daemon_port: 9123,
        ..DockerRuntimeConfig::default()
    };

    let argv = daemon_launch_argv(&config, &record("eos-xyz"), "tok");

    assert!(has_flag(&argv, "--tcp-port", "9123"));
}

#[test]
fn daemon_launch_argv_passes_dynamic_sandbox_id_not_a_literal_token() {
    let argv = daemon_launch_argv(
        &DockerRuntimeConfig::default(),
        &record("eos-dyn"),
        "secret",
    );

    assert!(has_flag(&argv, "--sandbox-id", "eos-dyn"));
    assert!(has_flag(&argv, "--auth-token", "secret"));
}

fn has_flag(argv: &[String], flag: &str, value: &str) -> bool {
    argv.windows(2)
        .any(|window| window[0] == flag && window[1] == value)
}
