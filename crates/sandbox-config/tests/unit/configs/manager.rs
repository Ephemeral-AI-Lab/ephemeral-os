use std::path::PathBuf;

#[test]
fn config_prd_manager_docker_section_deserializes_and_validates() {
    let docker = prd_docker();
    docker.validate().expect("prd manager.docker config is valid");

    assert_eq!(docker.daemon_port, 7000);
    assert_eq!(docker.readiness_timeout_ms, 60_000);
    assert_eq!(docker.container_workspace_root, PathBuf::from("/workspace"));
    assert_eq!(docker.gateway_instance_id, "eos-gateway");
    // Phase 3: prd runs the de-privileged container boundary.
    assert!(!docker.privileged);
}

#[test]
fn config_prd_manager_docker_injects_proxy_container_env() {
    let docker = prd_docker();

    assert_eq!(
        docker.container_env.get("HTTP_PROXY").map(String::as_str),
        Some("http://http.docker.internal:3128")
    );
    assert_eq!(
        docker.container_env.get("HTTPS_PROXY").map(String::as_str),
        Some("http://http.docker.internal:3128")
    );
    assert_eq!(
        docker.container_env.get("NO_PROXY").map(String::as_str),
        Some("localhost,127.0.0.1,::1")
    );
}

#[test]
fn container_env_defaults_to_empty() {
    assert!(DockerRuntimeConfig::default().container_env.is_empty());
}

#[test]
fn validate_rejects_blank_container_env_name() {
    let mut docker = prd_docker();
    docker
        .container_env
        .insert(String::new(), "value".to_owned());
    assert_invalid(&docker, "manager.docker.container_env");
}

#[test]
fn validate_rejects_container_env_name_with_equals() {
    let mut docker = prd_docker();
    docker
        .container_env
        .insert("HTTP=PROXY".to_owned(), "value".to_owned());
    assert_invalid(&docker, "manager.docker.container_env");
}

#[test]
fn manager_section_defaults_to_no_docker_backend() {
    // The `none` backend needs no manager section, so a default ManagerConfig
    // carries no docker backend.
    let manager = ManagerConfig::default();
    assert!(manager.docker.is_none());
}

#[test]
fn manager_registry_path_defaults_to_none_and_deserializes_when_set() {
    // The prd baseline sets no registry path, so the registry stays in-memory
    // unless a deployment opts in.
    let baseline: ManagerConfig = crate::load_baseline()
        .expect("prd config loads")
        .section("manager")
        .expect("manager section deserializes");
    assert!(baseline.registry_path.is_none());

    let doc = crate::ConfigDocument::parse(
        std::path::Path::new("<test>"),
        "manager:\n  registry_path: /var/lib/eos/sandboxes.json\n",
    )
    .expect("document parses");
    let manager: ManagerConfig = doc
        .section("manager")
        .expect("manager section deserializes");
    assert_eq!(
        manager.registry_path,
        Some(PathBuf::from("/var/lib/eos/sandboxes.json"))
    );
}

#[test]
fn validate_rejects_blank_gateway_instance_id() {
    let mut docker = prd_docker();
    docker.gateway_instance_id = String::new();
    assert_invalid(&docker, "manager.docker.gateway_instance_id");
}

#[test]
fn validate_rejects_relative_container_workspace_root() {
    let mut docker = prd_docker();
    docker.container_workspace_root = PathBuf::from("relative/workspace");
    assert_invalid(&docker, "manager.docker.container_workspace_root");
}

#[test]
fn validate_rejects_empty_daemon_binary_path() {
    let mut docker = prd_docker();
    docker.daemon_binary_path = PathBuf::new();
    assert_invalid(&docker, "manager.docker.daemon_binary_path");
}

fn prd_docker() -> DockerRuntimeConfig {
    let manager: ManagerConfig = crate::load_baseline()
        .expect("prd config loads")
        .section("manager")
        .expect("manager section deserializes");
    manager.docker.expect("manager.docker section present")
}

fn assert_invalid(config: &DockerRuntimeConfig, field: &str) {
    let err = config.validate().expect_err("config should be invalid");
    assert!(err.to_string().contains(field), "{err}");
}
