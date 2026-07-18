use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use sandbox_manager::{
    CreateSandboxRequest, ManagerError, SandboxRecord, SandboxRuntime, SandboxState,
    SharedBaseMount,
};
use sandbox_provider_docker::{DockerRuntimeConfig, DockerSandboxRuntime};

#[test]
fn create_sandbox_rejects_missing_shared_base_mount() {
    let runtime = DockerSandboxRuntime::new(DockerRuntimeConfig::default());
    let error = runtime
        .create_sandbox(&CreateSandboxRequest {
            image: "ubuntu:24.04".to_owned(),
            workspace_root: PathBuf::from("/workspace"),
            shared_base: None,
        })
        .expect_err("missing shared base rejected before docker");

    assert!(matches!(error, ManagerError::RuntimeFailed { .. }));
    assert!(error.to_string().contains("shared base mount is required"));
}

#[test]
fn create_sandbox_rejects_missing_shared_base_source() {
    let runtime = DockerSandboxRuntime::new(DockerRuntimeConfig::default());
    let missing_source =
        std::env::temp_dir().join(format!("eos-missing-shared-base-{}", unique_test_suffix()));
    let error = runtime
        .create_sandbox(&CreateSandboxRequest {
            image: "ubuntu:24.04".to_owned(),
            workspace_root: PathBuf::from("/workspace"),
            shared_base: Some(SharedBaseMount {
                source: missing_source,
                target: PathBuf::from("/eos/layer-stack/base"),
                root_hash: "root-hash".to_owned(),
                readonly: true,
            }),
        })
        .expect_err("missing shared base source rejected before docker");

    assert!(matches!(error, ManagerError::RuntimeFailed { .. }));
    assert!(error.to_string().contains("shared base source"));
}

#[test]
#[ignore = "requires a local Docker Engine and the ubuntu:24.04 image"]
fn create_sandbox_accepts_image_without_git() {
    let root = std::env::temp_dir().join(format!("eos-no-git-sandbox-{}", unique_test_suffix()));
    let workspace = root.join("workspace");
    let shared_base = root.join("base");
    std::fs::create_dir_all(&workspace).expect("create workspace");
    std::fs::create_dir_all(&shared_base).expect("create shared base");

    let config = sandbox_config::configs::manager::DockerRuntimeConfig {
        daemon_config_yaml_path: PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../config/prd.yml"),
        ..sandbox_config::configs::manager::DockerRuntimeConfig::default()
    };
    let runtime = DockerSandboxRuntime::new(config);
    let result = runtime
        .create_sandbox(&CreateSandboxRequest {
            image: "ubuntu:24.04".to_owned(),
            workspace_root: workspace.clone(),
            shared_base: Some(SharedBaseMount {
                source: shared_base,
                target: PathBuf::from("/eos/layer-stack/base"),
                root_hash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                    .to_owned(),
                readonly: true,
            }),
        })
        .expect("sandbox creation must not require git in the image");

    let record = SandboxRecord::new(result.id, workspace, SandboxState::Creating);
    runtime.destroy_sandbox(&record).expect("destroy sandbox");
    std::fs::remove_dir_all(root).expect("remove fixture");
}

fn unique_test_suffix() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("time after epoch")
        .as_nanos();
    format!("{nanos}-{}", std::process::id())
}
