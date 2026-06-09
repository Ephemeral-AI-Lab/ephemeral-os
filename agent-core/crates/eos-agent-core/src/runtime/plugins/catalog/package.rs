//! Catalog-owned plugin package descriptors.

use eos_sandbox_port::{
    Intent, PluginDependencyScope, PluginManifestDescriptor, PluginOperationDescriptor,
    PluginPackageContract, PluginPackageDescriptor, PluginPackageFile, PluginPackageTree,
    PluginRefreshStrategy, PluginServiceDescriptor, PluginServiceMode, PluginSetupDescriptor,
};
use sha2::{Digest, Sha256};

use super::tool_specs::plugin_tool_specs;

const LSP_PLUGIN_ID: &str = "lsp";
const LSP_PLUGIN_VERSION: &str = "0.1.0";
const LSP_SERVICE_ID: &str = "pyright";
const LSP_SERVICE_PROFILE_PREFIX: &str = "builtin-lsp-pyright-service";
const LSP_SETUP_COMMAND: &[&str] = &["./setup.sh"];
const LSP_SERVICE_COMMAND: &[&str] = &["./ppc_service.py"];
const LSP_SERVICE_WORKING_DIR: &str = "runtime";
const PLUGIN_OP_TIMEOUT_MS: u64 = 150_000;
const LSP_SETUP_TIMEOUT_MS: u64 = 60_000;

const LSP_PLUGIN_METADATA: &[u8] = include_bytes!("lsp/plugin.md");
const LSP_SANDBOX_PLUGIN_JSON: &[u8] = include_bytes!("lsp/sandbox-plugin.json");
const LSP_SETUP_SCRIPT: &[u8] = include_bytes!("lsp/setup.sh");
const LSP_PPC_SERVICE: &[u8] = include_bytes!("lsp/runtime/ppc_service.py");

/// Return the neutral package descriptor for one built-in catalog plugin.
#[must_use]
pub fn plugin_package_descriptor(plugin_id: &str) -> Option<PluginPackageDescriptor> {
    match plugin_id {
        LSP_PLUGIN_ID => Some(lsp_plugin_package_descriptor()),
        _ => None,
    }
}

/// Return the neutral package descriptor for the built-in LSP plugin.
#[must_use]
pub fn lsp_plugin_package_descriptor() -> PluginPackageDescriptor {
    let package_tree = lsp_package_tree();
    let plugin_digest = canonical_tree_digest(&package_tree.files);
    let service_profile_digest = format!("{LSP_SERVICE_PROFILE_PREFIX}-{plugin_digest}");
    let setup_marker_digest = format!("setup-{plugin_digest}");
    PluginPackageDescriptor {
        manifest: PluginManifestDescriptor {
            plugin_id: LSP_PLUGIN_ID.to_owned(),
            plugin_version: LSP_PLUGIN_VERSION.to_owned(),
            plugin_digest,
            package: PluginPackageContract {
                runtime_dir: LSP_SERVICE_WORKING_DIR.to_owned(),
                dependency_scope: PluginDependencyScope::PackageDigest,
            },
            setup: Some(PluginSetupDescriptor {
                command: LSP_SETUP_COMMAND
                    .iter()
                    .map(|arg| (*arg).to_owned())
                    .collect(),
                working_dir: ".".to_owned(),
                setup_marker_digest,
                timeout_ms: LSP_SETUP_TIMEOUT_MS,
            }),
            services: vec![PluginServiceDescriptor {
                service_id: LSP_SERVICE_ID.to_owned(),
                service_profile_digest,
                service_mode: PluginServiceMode::WorkspaceSnapshotRefresh,
                refresh_strategy: PluginRefreshStrategy::RemountWorkspaceAndNotify,
                command: LSP_SERVICE_COMMAND
                    .iter()
                    .map(|arg| (*arg).to_owned())
                    .collect(),
                working_dir: Some(LSP_SERVICE_WORKING_DIR.to_owned()),
                ppc_protocol_version: 1,
            }],
            operations: lsp_operations(),
        },
        package_tree,
    }
}

fn lsp_package_tree() -> PluginPackageTree {
    PluginPackageTree {
        files: vec![
            package_file("plugin.md", LSP_PLUGIN_METADATA, 0o644),
            package_file("sandbox-plugin.json", LSP_SANDBOX_PLUGIN_JSON, 0o644),
            package_file("setup.sh", LSP_SETUP_SCRIPT, 0o755),
            package_file("runtime/ppc_service.py", LSP_PPC_SERVICE, 0o755),
        ],
    }
}

fn package_file(path: &str, contents: &[u8], mode: u32) -> PluginPackageFile {
    PluginPackageFile {
        path: path.to_owned(),
        contents: contents.to_vec(),
        mode,
    }
}

fn lsp_operations() -> Vec<PluginOperationDescriptor> {
    plugin_tool_specs()
        .into_iter()
        .filter_map(|spec| {
            let op_name = spec.name.as_str().strip_prefix("lsp.")?;
            Some(PluginOperationDescriptor {
                op_name: op_name.to_owned(),
                intent: spec.intent,
                auto_workspace_overlay: spec.intent != Intent::WriteAllowed,
                service_id: Some(LSP_SERVICE_ID.to_owned()),
                timeout_ms: Some(PLUGIN_OP_TIMEOUT_MS),
            })
        })
        .collect()
}

fn canonical_tree_digest(files: &[PluginPackageFile]) -> String {
    let mut files = files.iter().collect::<Vec<_>>();
    files.sort_by(|a, b| a.path.cmp(&b.path));
    let mut hasher = Sha256::new();
    for file in files {
        hasher.update(file.path.as_bytes());
        hasher.update([0]);
        hasher.update((file.mode & 0o777).to_be_bytes());
        hasher.update(&file.contents);
        hasher.update([0]);
    }
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn lsp_descriptor_is_neutral_daemon_package_contract() {
        let descriptor = lsp_plugin_package_descriptor();
        assert_eq!(descriptor.manifest.plugin_id, "lsp");
        assert_eq!(
            descriptor.manifest.plugin_digest,
            canonical_tree_digest(&descriptor.package_tree.files)
        );
        assert_eq!(
            descriptor
                .manifest
                .setup
                .as_ref()
                .map(|setup| setup.command.clone()),
            Some(vec!["./setup.sh".to_owned()])
        );
        assert_eq!(
            descriptor
                .manifest
                .setup
                .as_ref()
                .map(|setup| setup.working_dir.as_str()),
            Some(".")
        );
        assert!(descriptor
            .manifest
            .setup
            .as_ref()
            .is_some_and(|setup| setup.setup_marker_digest.starts_with("setup-")));
        assert_eq!(
            package_file_named(&descriptor, "runtime/ppc_service.py").mode,
            0o755
        );
        assert_eq!(
            descriptor.manifest.services[0].command,
            vec!["./ppc_service.py"]
        );
        assert_eq!(
            descriptor.manifest.services[0].working_dir.as_deref(),
            Some("runtime")
        );
        assert!(descriptor
            .manifest
            .services
            .iter()
            .flat_map(|service| service.command.iter())
            .all(|part| !part.starts_with("/eos/")));
    }

    #[test]
    fn lsp_package_tree_contains_normal_package_material() {
        let descriptor = lsp_plugin_package_descriptor();
        assert_eq!(
            package_paths(&descriptor),
            vec![
                "plugin.md",
                "runtime/ppc_service.py",
                "sandbox-plugin.json",
                "setup.sh"
            ]
        );
        assert!(std::str::from_utf8(
            &package_file_named(&descriptor, "runtime/ppc_service.py").contents
        )
        .expect("runtime is utf8")
        .contains("pyright_command"));
        assert!(
            std::str::from_utf8(&package_file_named(&descriptor, "setup.sh").contents)
                .expect("setup is utf8")
                .contains("node22")
        );
    }

    #[test]
    fn lsp_package_source_omits_retired_global_runtime_fragments() {
        let descriptor = lsp_plugin_package_descriptor();
        let forbidden = [
            concat!("/eos", "/env"),
            concat!("NODE", "_HOME"),
            concat!("pyright-langserver", " --stdio"),
            concat!("plugin-packages", "/lsp"),
        ];
        for file in &descriptor.package_tree.files {
            let Ok(text) = std::str::from_utf8(&file.contents) else {
                continue;
            };
            for fragment in forbidden {
                assert!(
                    !text.contains(fragment),
                    "{} leaked retired runtime fragment",
                    file.path
                );
            }
        }
    }

    #[test]
    fn lsp_operations_follow_catalog_tool_specs() {
        let descriptor = lsp_plugin_package_descriptor();
        let operations = &descriptor.manifest.operations;
        let specs = plugin_tool_specs();
        assert_eq!(operations.len(), specs.len());
        assert!(operations.iter().any(|operation| {
            operation.op_name == "rename"
                && operation.intent == Intent::WriteAllowed
                && !operation.auto_workspace_overlay
                && operation.service_id.as_deref() == Some("pyright")
        }));
        assert!(operations.iter().any(|operation| {
            operation.op_name == "hover"
                && operation.intent == Intent::ReadOnly
                && operation.auto_workspace_overlay
        }));
    }

    #[test]
    fn lsp_manifest_serializes_to_daemon_shape() {
        let descriptor = lsp_plugin_package_descriptor();
        let value = serde_json::to_value(&descriptor.manifest).expect("manifest serializes");
        assert_eq!(value["plugin_id"], json!("lsp"));
        assert_eq!(
            value["package"],
            json!({"runtime_dir": "runtime", "dependency_scope": "package_digest"})
        );
        assert_eq!(
            value["services"][0]["service_mode"],
            json!("workspace_snapshot_refresh")
        );
        assert_eq!(
            value["services"][0]["refresh_strategy"],
            json!("remount_workspace_and_notify")
        );
        assert_eq!(value["setup"]["command"], json!(["./setup.sh"]));
        assert_eq!(value["setup"]["working_dir"], json!("."));
    }

    fn package_file_named<'a>(
        descriptor: &'a PluginPackageDescriptor,
        path: &str,
    ) -> &'a PluginPackageFile {
        descriptor
            .package_tree
            .files
            .iter()
            .find(|file| file.path == path)
            .unwrap_or_else(|| panic!("{path} package file missing"))
    }

    fn package_paths(descriptor: &PluginPackageDescriptor) -> Vec<&str> {
        let mut paths = descriptor
            .package_tree
            .files
            .iter()
            .map(|file| file.path.as_str())
            .collect::<Vec<_>>();
        paths.sort_unstable();
        paths
    }
}
