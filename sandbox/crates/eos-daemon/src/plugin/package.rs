//! Daemon-owned plugin package publish and setup helpers.

use std::fs;
use std::io::ErrorKind;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use eos_plugin::{PluginError, PluginManifest, PACKAGE_SHA256_MARKER, SETUP_SHA256_MARKER};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::error::DaemonError;

#[derive(Debug, Clone, Default)]
pub(super) struct PackageEnsureReport {
    pub(super) active: bool,
    pub(super) needs_upload: bool,
    pub(super) package_root: Option<PathBuf>,
    pub(super) dependency_root: Option<PathBuf>,
    pub(super) package_published: bool,
    pub(super) setup_ran: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct PackageRoots {
    pub(super) package_root: PathBuf,
    pub(super) dependency_root: PathBuf,
}

pub(super) fn package_roots(
    args: &Value,
    manifest: &PluginManifest,
) -> Result<PackageRoots, DaemonError> {
    let paths = PackagePaths::new(args, manifest)?;
    Ok(PackageRoots {
        package_root: paths.package_root,
        dependency_root: paths.dependency_root,
    })
}

pub(super) fn package_contract_active(args: &Value) -> bool {
    args.get("staged_package_root").is_some()
        || args.get("manifest").is_some_and(|manifest| {
            manifest.get("package").is_some() || manifest.get("setup").is_some()
        })
}

pub(super) fn ensure_package(
    args: &Value,
    manifest: Option<&PluginManifest>,
) -> Result<PackageEnsureReport, DaemonError> {
    let Some(manifest) = manifest else {
        return Ok(PackageEnsureReport::default());
    };
    if !package_contract_active(args) {
        return Ok(PackageEnsureReport::default());
    }

    let paths = PackagePaths::new(args, manifest)?;
    let staged_package_root = staged_package_root(args)?;
    if staged_package_root.is_none() {
        return Ok(warm_probe(manifest, &paths));
    }

    let staged_package_root = staged_package_root.expect("checked is_some");
    validate_staged_package_root(&staged_package_root, &paths.upload_digest_root)?;
    validate_staged_package(manifest, &staged_package_root)?;

    let package_published = publish_package(&staged_package_root, &paths)?;
    let setup_ran = ensure_setup(manifest, &paths)?;
    cleanup_upload_root(&staged_package_root, &paths.upload_digest_root);

    Ok(PackageEnsureReport {
        active: true,
        needs_upload: false,
        package_root: Some(paths.package_root),
        dependency_root: Some(paths.dependency_root),
        package_published,
        setup_ran,
    })
}

pub(super) fn needs_upload_response(
    manifest: &PluginManifest,
    report: &PackageEnsureReport,
) -> Value {
    json!({
        "success": true,
        "plugin": manifest.plugin_id,
        "digest": manifest.plugin_digest,
        "ready": false,
        "needs_upload": true,
        "runtime_loaded": false,
        "package_root": report.package_root,
        "dependency_root": report.dependency_root,
    })
}

fn warm_probe(manifest: &PluginManifest, paths: &PackagePaths) -> PackageEnsureReport {
    let package_current = marker_matches(
        &paths.package_root.join(PACKAGE_SHA256_MARKER),
        manifest.package_marker_digest(),
    );
    let setup_current = manifest.setup_marker_digest().map_or(true, |digest| {
        marker_matches(&paths.package_root.join(SETUP_SHA256_MARKER), digest)
    });
    PackageEnsureReport {
        active: true,
        needs_upload: !(package_current && setup_current),
        package_root: Some(paths.package_root.clone()),
        dependency_root: Some(paths.dependency_root.clone()),
        package_published: false,
        setup_ran: false,
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
    fn new(args: &Value, manifest: &PluginManifest) -> Result<Self, DaemonError> {
        let runtime_plugins_root =
            root_arg(args, "package_runtime_root", "/eos/runtime/plugins/catalog");
        let dependency_base = root_arg(args, "package_dependency_root", "/eos/runtime/packages");
        let upload_base = root_arg(args, "package_upload_root", "/eos/scratch/uploads/plugins");
        let setup_base = root_arg(args, "package_setup_root", "/eos/scratch/setup");
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

fn root_arg(args: &Value, key: &str, default: &str) -> PathBuf {
    #[cfg(test)]
    if let Some(root) = args
        .get(key)
        .and_then(Value::as_str)
        .filter(|root| !root.is_empty())
    {
        return PathBuf::from(root);
    }
    let _ = args;
    let _ = key;
    PathBuf::from(default)
}

fn staged_package_root(args: &Value) -> Result<Option<PathBuf>, DaemonError> {
    let Some(value) = args.get("staged_package_root") else {
        return Ok(None);
    };
    let Some(path) = value
        .as_str()
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
) -> Result<(), DaemonError> {
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
) -> Result<(), DaemonError> {
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

fn publish_package(staged_package_root: &Path, paths: &PackagePaths) -> Result<bool, DaemonError> {
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
    if paths.package_root.exists() {
        fs::remove_dir_all(&paths.package_root)?;
    }
    match fs::rename(staged_package_root, &paths.package_root) {
        Ok(()) => {}
        Err(err) if err.kind() == ErrorKind::CrossesDevices => {
            publish_package_across_filesystems(staged_package_root, &paths.package_root)?;
        }
        Err(err) => return Err(err.into()),
    }
    Ok(true)
}

fn publish_package_across_filesystems(
    staged_package_root: &Path,
    package_root: &Path,
) -> Result<(), DaemonError> {
    let parent = package_root.parent().ok_or_else(|| {
        PluginError::Ensure(format!(
            "package root has no parent: {}",
            package_root.display()
        ))
    })?;
    let temp_root = parent.join(format!(
        ".{}.publish-{}",
        package_root
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or("package"),
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&temp_root);
    copy_package_tree(staged_package_root, &temp_root)?;
    fs::rename(&temp_root, package_root).inspect_err(|_| {
        let _ = fs::remove_dir_all(&temp_root);
    })?;
    Ok(())
}

fn copy_package_tree(source_root: &Path, target_root: &Path) -> Result<(), DaemonError> {
    fs::create_dir_all(target_root)?;
    copy_package_dir(source_root, source_root, target_root)
}

fn copy_package_dir(
    source_root: &Path,
    source_dir: &Path,
    target_root: &Path,
) -> Result<(), DaemonError> {
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

fn ensure_setup(manifest: &PluginManifest, paths: &PackagePaths) -> Result<bool, DaemonError> {
    let Some(setup) = &manifest.setup else {
        return Ok(false);
    };
    let setup_marker = paths.package_root.join(SETUP_SHA256_MARKER);
    if marker_matches(&setup_marker, &setup.setup_marker_digest) {
        return Ok(false);
    }
    fs::create_dir_all(&paths.dependency_root)?;
    fs::create_dir_all(paths.dependency_root.join("cache"))?;
    fs::create_dir_all(paths.dependency_root.join("archives"))?;
    fs::create_dir_all(paths.setup_tmp_root.join("tmp"))?;
    let cwd = paths.package_root.join(&setup.working_dir);
    reject_forbidden_setup_roots(&setup.command, &cwd)?;
    let output = Command::new(&setup.command[0])
        .args(&setup.command[1..])
        .current_dir(&cwd)
        .env_clear()
        .env("EOS_PLUGIN_ID", &manifest.plugin_id)
        .env("EOS_PLUGIN_DIGEST", &manifest.plugin_digest)
        .env("EOS_PLUGIN_PACKAGE_ROOT", &paths.package_root)
        .env("EOS_PLUGIN_DEPENDENCY_ROOT", &paths.dependency_root)
        .env("TMPDIR", paths.setup_tmp_root.join("tmp"))
        .env("HOME", &paths.setup_tmp_root)
        .stdin(Stdio::null())
        .output()?;
    if !output.status.success() {
        return Err(PluginError::Ensure(format!(
            "plugin setup failed with status {:?}: {}{}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        ))
        .into());
    }
    fs::write(setup_marker, &setup.setup_marker_digest)?;
    Ok(true)
}

fn reject_forbidden_setup_roots(command: &[String], cwd: &Path) -> Result<(), DaemonError> {
    let joined = command.join("\0");
    reject_forbidden_text("plugin setup command", &joined)?;
    for arg in command {
        if let Some(script) = setup_script_path(arg, cwd) {
            if script.is_file() {
                let script_text = fs::read_to_string(&script)?;
                reject_forbidden_text(
                    &format!("plugin setup script {}", script.display()),
                    &script_text,
                )?;
            }
        }
    }
    Ok(())
}

fn setup_script_path(arg: &str, cwd: &Path) -> Option<PathBuf> {
    let path = Path::new(arg);
    if path.is_absolute() {
        path.starts_with(cwd).then(|| path.to_path_buf())
    } else if arg.contains('/') {
        Some(cwd.join(path))
    } else {
        None
    }
}

fn reject_forbidden_text(context: &str, text: &str) -> Result<(), DaemonError> {
    for forbidden in ["/root", "/var"] {
        if text.contains(forbidden) {
            return Err(PluginError::Ensure(format!(
                "{context} references forbidden managed root {forbidden}"
            ))
            .into());
        }
    }
    Ok(())
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

fn canonical_tree_digest(root: &Path) -> Result<String, DaemonError> {
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
) -> Result<(), DaemonError> {
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
mod tests {
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::*;

    type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn copy_package_tree_preserves_nested_files_and_modes() -> TestResult {
        let root = unique_temp_dir("copy-package-tree");
        let staged = root.join("staged");
        let target = root.join("target");
        fs::create_dir_all(staged.join("runtime"))?;
        fs::write(staged.join("sandbox-plugin.json"), "{}")?;
        fs::write(staged.join("runtime/server.py"), "#!/usr/bin/env python3\n")?;
        fs::set_permissions(
            staged.join("runtime/server.py"),
            fs::Permissions::from_mode(0o755),
        )?;

        copy_package_tree(&staged, &target)?;

        assert_eq!(
            fs::read_to_string(target.join("sandbox-plugin.json"))?,
            "{}"
        );
        assert_eq!(
            fs::metadata(target.join("runtime/server.py"))?
                .permissions()
                .mode()
                & 0o777,
            0o755
        );
        let _ = fs::remove_dir_all(&root);
        Ok(())
    }

    fn unique_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |duration| duration.as_nanos());
        std::env::temp_dir().join(format!("{label}-{}-{nanos}", std::process::id()))
    }
}
