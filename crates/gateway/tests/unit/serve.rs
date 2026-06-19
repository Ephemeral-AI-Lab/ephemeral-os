use std::path::PathBuf;

use super::ServeArgs;

fn parse(args: &[&str]) -> ServeArgs {
    ServeArgs::parse(args.iter().map(|arg| (*arg).to_owned())).expect("serve args parse")
}

#[test]
fn default_remote_config_lives_under_remote_daemon_dir() {
    let args = parse(&[
        "--image",
        "sandbox:latest",
        "--remote-daemon-dir",
        "/eos/custom/daemon",
    ]);

    assert_eq!(
        args.host.remote_config_path,
        PathBuf::from("/eos/custom/daemon/config.yml")
    );
}

#[test]
fn explicit_remote_config_overrides_default() {
    let args = parse(&[
        "--image",
        "sandbox:latest",
        "--remote-daemon-dir",
        "/eos/custom/daemon",
        "--remote-config",
        "/eos/config/prd.yml",
    ]);

    assert_eq!(
        args.host.remote_config_path,
        PathBuf::from("/eos/config/prd.yml")
    );
}

#[test]
fn parse_keeps_local_and_remote_config_paths_distinct() {
    let parsed = parse(&[
        "--image",
        "sandbox:dev",
        "--config-yaml",
        "/tmp/source.yml",
        "--remote-config",
        "/eos/runtime/config/prd.yml",
        "--listen",
        "/tmp/sandbox.sock",
    ]);

    assert_eq!(
        parsed.host.config_yaml_path,
        PathBuf::from("/tmp/source.yml")
    );
    assert_eq!(
        parsed.host.remote_config_path,
        PathBuf::from("/eos/runtime/config/prd.yml")
    );
}

#[test]
fn docker_privileged_can_be_disabled_and_reenabled() {
    let disabled = parse(&["--image", "sandbox:dev", "--no-docker-privileged"]);
    assert!(!disabled.host.docker_privileged);

    let reenabled = parse(&[
        "--image",
        "sandbox:dev",
        "--no-docker-privileged",
        "--docker-privileged",
    ]);
    assert!(reenabled.host.docker_privileged);
}

#[test]
fn defaults_use_private_runtime_dir_for_sockets_and_state() {
    let parsed = parse(&["--image", "sandbox:dev"]);

    assert_eq!(
        parsed.listen.file_name().and_then(|name| name.to_str()),
        Some("gateway.sock")
    );
    assert_eq!(
        parsed
            .host
            .state_dir
            .file_name()
            .and_then(|name| name.to_str()),
        Some("state")
    );
    assert_eq!(parsed.listen.parent(), parsed.host.state_dir.parent());
    assert_ne!(
        parsed.listen.parent(),
        Some(std::path::Path::new("/tmp")),
        "default operator socket must not be a direct /tmp sibling"
    );
}
