//! Daemon-owned plugin package publish and setup helpers.

use std::fs;
use std::io::ErrorKind;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use namespace::protocol::{Intent, RunMode, RunRequest, ToolCall, WorkspaceRoot};
use plugin::{PluginError, PluginManifest, PACKAGE_SHA256_MARKER, SETUP_SHA256_MARKER};
use sha2::{Digest, Sha256};
use workspace::NsRunnerLauncher;

use super::contract::{PluginEnsureInput, PluginNeedsUploadOutput, PluginPackageInput};
use super::route::{
    ENV_PLUGIN_DEPENDENCY_ROOT, ENV_PLUGIN_DIGEST, ENV_PLUGIN_ID, ENV_PLUGIN_PACKAGE_ROOT,
};
use super::PpcError;

const SETUP_OUTPUT_TAIL_BYTES: usize = 4096;

/// Outcome of a package ensure: whether the package contract is active, whether
/// the caller must upload, and the resolved roots / publish + setup status.
#[derive(Debug, Clone, Default)]
pub struct PackageEnsureReport {
    pub active: bool,
    pub needs_upload: bool,
    pub package_root: Option<PathBuf>,
    pub dependency_root: Option<PathBuf>,
    pub package_published: bool,
    pub setup_ran: bool,
    pub setup: Option<PluginSetupReport>,
}

/// Bounded setup command facts for trace events.
#[derive(Debug, Clone, Default)]
pub struct PluginSetupReport {
    pub plugin: String,
    pub digest: String,
    pub ran: bool,
    pub success: bool,
    pub exit_code: Option<i32>,
    pub output_tail: Option<String>,
    pub spawn_error: Option<String>,
}

/// Resolved package + dependency roots for a plugin digest.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PackageRoots {
    pub package_root: PathBuf,
    pub dependency_root: PathBuf,
}

pub(crate) fn package_roots(
    package: &PluginPackageInput,
    manifest: &PluginManifest,
) -> Result<PackageRoots, PpcError> {
    let paths = PackagePaths::new(package, manifest)?;
    Ok(PackageRoots {
        package_root: paths.package_root,
        dependency_root: paths.dependency_root,
    })
}

fn package_contract_active(input: &PluginEnsureInput) -> bool {
    input.package.staged_package_root_present
        || input.manifest.as_ref().is_some_and(|manifest| {
            manifest.get("package").is_some() || manifest.get("setup").is_some()
        })
}

pub(super) fn ensure_package(
    input: &PluginEnsureInput,
    manifest: Option<&PluginManifest>,
    launcher: &dyn NsRunnerLauncher,
) -> Result<PackageEnsureReport, PpcError> {
    let Some(manifest) = manifest else {
        return Ok(PackageEnsureReport::default());
    };
    if !package_contract_active(input) {
        return Ok(PackageEnsureReport::default());
    }

    let paths = PackagePaths::new(&input.package, manifest)?;
    let Some(staged_package_root) = staged_package_root(&input.package)? else {
        return Ok(warm_probe(manifest, &paths));
    };
    validate_staged_package_root(&staged_package_root, &paths.upload_digest_root)?;
    validate_staged_package(manifest, &staged_package_root)?;

    let package_published = publish_package(&staged_package_root, &paths)?;
    let setup = ensure_setup(manifest, &paths, launcher)?;
    let setup_ran = setup
        .as_ref()
        .is_some_and(|report| report.ran && report.success);
    cleanup_upload_root(&staged_package_root, &paths.upload_digest_root);

    Ok(PackageEnsureReport {
        active: true,
        needs_upload: false,
        package_root: Some(paths.package_root),
        dependency_root: Some(paths.dependency_root),
        package_published,
        setup_ran,
        setup,
    })
}

#[must_use]
pub fn needs_upload_output(
    manifest: &PluginManifest,
    report: &PackageEnsureReport,
) -> PluginNeedsUploadOutput {
    PluginNeedsUploadOutput {
        success: true,
        plugin: manifest.plugin_id.clone(),
        digest: manifest.plugin_digest.clone(),
        ready: false,
        needs_upload: true,
        runtime_loaded: false,
        package_root: report.package_root.clone(),
        dependency_root: report.dependency_root.clone(),
    }
}

fn warm_probe(manifest: &PluginManifest, paths: &PackagePaths) -> PackageEnsureReport {
    let package_current = marker_matches(
        &paths.package_root.join(PACKAGE_SHA256_MARKER),
        manifest.package_marker_digest(),
    );
    let setup_current = manifest
        .setup_marker_digest()
        .is_none_or(|digest| marker_matches(&paths.package_root.join(SETUP_SHA256_MARKER), digest));
    PackageEnsureReport {
        active: true,
        needs_upload: !(package_current && setup_current),
        package_root: Some(paths.package_root.clone()),
        dependency_root: Some(paths.dependency_root.clone()),
        package_published: false,
        setup_ran: false,
        setup: None,
    }
}

#[derive(Debug)]
struct PackagePaths {
    package_root: PathBuf,
    dependency_root: PathBuf,
    upload_digest_root: PathBuf,
    setup_tmp_root: PathBuf,
}

impl PackagePaths {
    fn new(package: &PluginPackageInput, manifest: &PluginManifest) -> Result<Self, PpcError> {
        let runtime_plugins_root = root_arg(
            &package.package_runtime_root,
            "/eos/runtime/plugins/catalog",
        );
        let dependency_base = root_arg(&package.package_dependency_root, "/eos/runtime/packages");
        let upload_base = root_arg(&package.package_upload_root, "/eos/scratch/uploads/plugins");
        let setup_base = root_arg(&package.package_setup_root, "/eos/scratch/setup");
        Ok(Self {
            package_root: runtime_plugins_root
                .join(&manifest.plugin_id)
                .join(&manifest.plugin_digest),
            dependency_root: dependency_base
                .join(&manifest.plugin_id)
                .join(&manifest.plugin_digest),
            upload_digest_root: upload_base
                .join(&manifest.plugin_id)
                .join(&manifest.plugin_digest),
            setup_tmp_root: setup_base
                .join(&manifest.plugin_id)
                .join(&manifest.plugin_digest),
        })
    }
}

fn root_arg(value: &Option<PathBuf>, default: &str) -> PathBuf {
    #[cfg(feature = "test-root-override")]
    if let Some(root) = value {
        return root.clone();
    }
    let _ = value;
    PathBuf::from(default)
}

fn staged_package_root(package: &PluginPackageInput) -> Result<Option<PathBuf>, PpcError> {
    if !package.staged_package_root_present {
        return Ok(None);
    };
    let Some(path) = package
        .staged_package_root
        .as_deref()
        .map(str::trim)
        .filter(|path| !path.is_empty())
    else {
        return Err(PluginError::Ensure(
            "staged_package_root must be a non-empty string".to_owned(),
        )
        .into());
    };
    Ok(Some(PathBuf::from(path)))
}

fn validate_staged_package_root(
    staged_package_root: &Path,
    upload_digest_root: &Path,
) -> Result<(), PpcError> {
    if !staged_package_root.is_absolute() {
        return Err(PluginError::Ensure("staged_package_root must be absolute".to_owned()).into());
    }
    if !staged_package_root.starts_with(upload_digest_root) {
        return Err(PluginError::Ensure(format!(
            "staged_package_root must be under {}",
            upload_digest_root.display()
        ))
        .into());
    }
    if staged_package_root
        .components()
        .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err(PluginError::Ensure(
            "staged_package_root must not contain path traversal".to_owned(),
        )
        .into());
    }
    Ok(())
}

fn validate_staged_package(
    manifest: &PluginManifest,
    staged_package_root: &Path,
) -> Result<(), PpcError> {
    if !staged_package_root.is_dir() {
        return Err(PluginError::Ensure(format!(
            "staged package root does not exist: {}",
            staged_package_root.display()
        ))
        .into());
    }
    let marker = staged_package_root.join(PACKAGE_SHA256_MARKER);
    if marker_matches(&marker, manifest.package_marker_digest()) {
        return Ok(());
    }
    let digest = canonical_tree_digest(staged_package_root)?;
    if digest == manifest.package_marker_digest() {
        Ok(())
    } else {
        Err(PluginError::Ensure(format!(
            "staged package digest mismatch: got {digest}, expected {}",
            manifest.package_marker_digest()
        ))
        .into())
    }
}

fn publish_package(staged_package_root: &Path, paths: &PackagePaths) -> Result<bool, PpcError> {
    if marker_matches(
        &paths.package_root.join(PACKAGE_SHA256_MARKER),
        staged_marker(staged_package_root)
            .as_deref()
            .unwrap_or_default(),
    ) {
        return Ok(false);
    }
    if let Some(parent) = paths.package_root.parent() {
        fs::create_dir_all(parent)?;
    }
    let prepared_root = prepare_package_publish_root(staged_package_root, &paths.package_root)?;
    replace_package_root(&prepared_root, &paths.package_root)?;
    Ok(true)
}

fn prepare_package_publish_root(
    staged_package_root: &Path,
    package_root: &Path,
) -> Result<PathBuf, PpcError> {
    let temp_root = package_sibling_temp_root(package_root, "publish")?;
    match fs::rename(staged_package_root, &temp_root) {
        Ok(()) => Ok(temp_root),
        Err(err) if err.kind() == ErrorKind::CrossesDevices => {
            copy_package_tree(staged_package_root, &temp_root).inspect_err(|_| {
                let _ = fs::remove_dir_all(&temp_root);
            })?;
            Ok(temp_root)
        }
        Err(err) => Err(err.into()),
    }
}

fn replace_package_root(prepared_root: &Path, package_root: &Path) -> Result<(), PpcError> {
    if !package_root.exists() {
        return fs::rename(prepared_root, package_root)
            .inspect_err(|_| {
                let _ = fs::remove_dir_all(prepared_root);
            })
            .map_err(Into::into);
    }

    let previous_root = package_sibling_temp_root(package_root, "previous")?;
    fs::rename(package_root, &previous_root)?;
    match fs::rename(prepared_root, package_root) {
        Ok(()) => {
            let _ = fs::remove_dir_all(previous_root);
            Ok(())
        }
        Err(publish_err) => {
            let restore_err = fs::rename(&previous_root, package_root).err();
            let _ = fs::remove_dir_all(prepared_root);
            if let Some(restore_err) = restore_err {
                return Err(PluginError::Ensure(format!(
                    "failed to publish package root {} and failed to restore previous package root: publish error: {publish_err}; restore error: {restore_err}",
                    package_root.display()
                ))
                .into());
            }
            Err(publish_err.into())
        }
    }
}

fn package_sibling_temp_root(package_root: &Path, label: &str) -> Result<PathBuf, PpcError> {
    let parent = package_root.parent().ok_or_else(|| {
        PluginError::Ensure(format!(
            "package root has no parent: {}",
            package_root.display()
        ))
    })?;
    Ok(parent.join(format!(
        ".{}.{}-{}",
        package_root
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("package"),
        label,
        uuid::Uuid::new_v4().simple()
    )))
}

fn copy_package_tree(source_root: &Path, target_root: &Path) -> Result<(), PpcError> {
    fs::create_dir_all(target_root)?;
    copy_package_dir(source_root, source_root, target_root)
}

fn copy_package_dir(
    source_root: &Path,
    source_dir: &Path,
    target_root: &Path,
) -> Result<(), PpcError> {
    for entry in fs::read_dir(source_dir)? {
        let entry = entry?;
        let source_path = entry.path();
        let metadata = entry.metadata()?;
        let relative = source_path
            .strip_prefix(source_root)
            .map_err(|err| PluginError::Ensure(err.to_string()))?;
        let target_path = target_root.join(relative);
        if metadata.is_dir() {
            fs::create_dir_all(&target_path)?;
            copy_package_dir(source_root, &source_path, target_root)?;
        } else if metadata.is_file() {
            if let Some(parent) = target_path.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::copy(&source_path, &target_path)?;
            fs::set_permissions(&target_path, metadata.permissions())?;
        } else {
            return Err(PluginError::Ensure(format!(
                "staged package path {} must be a regular file or directory",
                source_path.display()
            ))
            .into());
        }
    }
    Ok(())
}

fn ensure_setup(
    manifest: &PluginManifest,
    paths: &PackagePaths,
    launcher: &dyn NsRunnerLauncher,
) -> Result<Option<PluginSetupReport>, PpcError> {
    let Some(setup) = &manifest.setup else {
        return Ok(None);
    };
    let setup_marker = paths.package_root.join(SETUP_SHA256_MARKER);
    if marker_matches(&setup_marker, &setup.setup_marker_digest) {
        return Ok(None);
    }
    fs::create_dir_all(&paths.dependency_root)?;
    fs::create_dir_all(paths.dependency_root.join("cache"))?;
    fs::create_dir_all(paths.dependency_root.join("archives"))?;
    fs::create_dir_all(paths.setup_tmp_root.join("tmp"))?;
    let cwd = paths.package_root.join(&setup.working_dir);
    let request = setup_run_request(manifest, paths, &setup.command, &cwd, setup.timeout_ms);
    let result = launcher.run(&request).map_err(|err| {
        let spawn_error = err.to_string();
        let message = format!(
            "plugin setup command {:?} failed to start in {}: {err}",
            setup.command,
            cwd.display()
        );
        PpcError::SetupFailed {
            report: Box::new(PluginSetupReport {
                plugin: manifest.plugin_id.clone(),
                digest: manifest.plugin_digest.clone(),
                ran: false,
                success: false,
                exit_code: None,
                output_tail: None,
                spawn_error: Some(spawn_error),
            }),
            message,
        }
    })?;
    let output_tail = setup_result_tail(&result.payload);
    let success = result
        .payload
        .get("success")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(result.exit_code == 0);
    let report = PluginSetupReport {
        plugin: manifest.plugin_id.clone(),
        digest: manifest.plugin_digest.clone(),
        ran: true,
        success,
        exit_code: Some(result.exit_code),
        output_tail,
        spawn_error: None,
    };
    if !success {
        let message = format!(
            "plugin setup failed with status {:?}: {}",
            Some(result.exit_code),
            report.output_tail.as_deref().unwrap_or_default()
        );
        return Err(PpcError::SetupFailed {
            report: Box::new(report),
            message,
        });
    }
    fs::write(setup_marker, &setup.setup_marker_digest)?;
    Ok(Some(report))
}

fn setup_run_request(
    manifest: &PluginManifest,
    paths: &PackagePaths,
    command: &[String],
    cwd: &Path,
    timeout_ms: u64,
) -> RunRequest {
    RunRequest {
        mode: RunMode::FreshNs,
        tool_call: ToolCall {
            invocation_id: format!(
                "plugin-setup:{}:{}",
                manifest.plugin_id, manifest.plugin_digest
            ),
            caller_id: "plugin-setup".to_owned(),
            verb: "plugin_setup".into(),
            intent: Intent::Lifecycle,
            args: serde_json::json!({
                "command": command,
                "cwd": cwd,
                "package_root": paths.package_root,
                "dependency_root": paths.dependency_root,
                "setup_tmp_root": paths.setup_tmp_root,
                "env": {
                    ENV_PLUGIN_ID: manifest.plugin_id,
                    ENV_PLUGIN_DIGEST: manifest.plugin_digest,
                    ENV_PLUGIN_PACKAGE_ROOT: paths.package_root,
                    ENV_PLUGIN_DEPENDENCY_ROOT: paths.dependency_root,
                    "TMPDIR": paths.setup_tmp_root.join("tmp"),
                    "HOME": paths.setup_tmp_root,
                },
            }),
            background: false,
        },
        workspace_root: WorkspaceRoot(paths.package_root.clone()),
        layer_paths: Vec::new(),
        upperdir: None,
        workdir: None,
        ns_fds: None,
        cgroup_path: None,
        timeout_seconds: Some(timeout_ms as f64 / 1000.0),
    }
}

fn setup_result_tail(payload: &serde_json::Value) -> Option<String> {
    let stdout = payload
        .get("stdout_tail")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default();
    let stderr = payload
        .get("stderr_tail")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default();
    if stdout.is_empty() && stderr.is_empty() {
        return None;
    }
    setup_output_tail(stdout.as_bytes(), stderr.as_bytes())
}

fn setup_output_tail(stdout: &[u8], stderr: &[u8]) -> Option<String> {
    if stdout.is_empty() && stderr.is_empty() {
        return None;
    }
    let mut combined = Vec::with_capacity(stdout.len().saturating_add(stderr.len()));
    combined.extend_from_slice(stdout);
    combined.extend_from_slice(stderr);
    if combined.len() > SETUP_OUTPUT_TAIL_BYTES {
        combined = combined[combined.len() - SETUP_OUTPUT_TAIL_BYTES..].to_vec();
    }
    Some(String::from_utf8_lossy(&combined).into_owned())
}

fn marker_matches(marker: &Path, expected: &str) -> bool {
    fs::read_to_string(marker).is_ok_and(|value| value.trim() == expected)
}

fn staged_marker(staged_package_root: &Path) -> Option<String> {
    fs::read_to_string(staged_package_root.join(PACKAGE_SHA256_MARKER))
        .ok()
        .map(|value| value.trim().to_owned())
}

fn cleanup_upload_root(staged_package_root: &Path, upload_digest_root: &Path) {
    if let Some(upload_id_root) = staged_package_root.parent() {
        if upload_id_root.starts_with(upload_digest_root) {
            let _ = fs::remove_dir_all(upload_id_root);
        }
    }
}

fn canonical_tree_digest(root: &Path) -> Result<String, PpcError> {
    let mut files = Vec::new();
    collect_files(root, root, &mut files)?;
    files.sort_by(|a, b| a.0.cmp(&b.0));
    let mut hasher = Sha256::new();
    for (relative, path) in files {
        if relative == PACKAGE_SHA256_MARKER || relative == SETUP_SHA256_MARKER {
            continue;
        }
        let metadata = fs::metadata(&path)?;
        hasher.update(relative.as_bytes());
        hasher.update([0]);
        hasher.update((metadata.permissions().mode() & 0o777).to_be_bytes());
        hasher.update(fs::read(path)?);
        hasher.update([0]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn collect_files(
    root: &Path,
    dir: &Path,
    files: &mut Vec<(String, PathBuf)>,
) -> Result<(), PpcError> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        let metadata = entry.metadata()?;
        let relative = path
            .strip_prefix(root)
            .map_err(|err| PluginError::Ensure(err.to_string()))?
            .to_string_lossy()
            .replace('\\', "/");
        if relative
            .split('/')
            .any(|component| component == ".." || component.is_empty())
        {
            return Err(
                PluginError::Ensure(format!("invalid staged package path {relative}")).into(),
            );
        }
        if metadata.is_symlink() {
            return Err(PluginError::Ensure(format!(
                "staged package path {relative} must not be a symlink"
            ))
            .into());
        }
        if metadata.is_dir() {
            collect_files(root, &path, files)?;
        } else if metadata.is_file() {
            files.push((relative, path));
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "../../tests/plugin/package.rs"]
mod tests;
