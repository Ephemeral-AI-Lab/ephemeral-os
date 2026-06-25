use std::path::PathBuf;

#[test]
fn config_prd_manager_docker_section_deserializes_and_validates() {
    let docker = prd_docker();
    docker.validate().expect("prd manager.docker config is valid");

    assert_eq!(docker.daemon_port, 7000);
    assert_eq!(docker.container_workspace_root, PathBuf::from("/workspace"));
    assert_eq!(docker.gateway_instance_id, "eos-gateway");
    assert!(docker.privileged);
}

#[test]
fn manager_section_defaults_to_no_docker_backend() {
    // The `none` backend needs no manager section, so a default ManagerConfig
    // carries no docker backend.
    let manager = ManagerConfig::default();
    assert!(manager.docker.is_none());
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
