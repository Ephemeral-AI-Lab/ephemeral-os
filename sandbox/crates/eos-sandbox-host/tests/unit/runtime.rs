use super::{container_copy_target, docker_exec_args, parse_published_addr, validate_remote_name};

#[test]
fn copy_target_uses_requested_remote_name() {
    assert_eq!(
        container_copy_target("box", "/eos/runtime/daemon/", "eosd"),
        "box:/eos/runtime/daemon/eosd"
    );
    assert!(validate_remote_name("eosd").is_ok());
    assert!(validate_remote_name("../eosd").is_err());
}

#[test]
fn docker_exec_args_runs_from_root_after_leading_flags() {
    assert_eq!(
        docker_exec_args("box", &["mkdir", "-p", "/testbed"]),
        vec!["exec", "-w", "/", "box", "mkdir", "-p", "/testbed"]
    );
    assert_eq!(
        docker_exec_args("box", &["-d", "/eos/runtime/daemon/eosd", "daemon"]),
        vec![
            "exec",
            "-d",
            "-w",
            "/",
            "box",
            "/eos/runtime/daemon/eosd",
            "daemon"
        ]
    );
}

#[test]
fn published_addr_parses_loopback_port() {
    assert_eq!(
        parse_published_addr("0.0.0.0:54321"),
        Some("127.0.0.1:54321".parse().expect("addr"))
    );
    assert_eq!(parse_published_addr("garbage"), None);
}
