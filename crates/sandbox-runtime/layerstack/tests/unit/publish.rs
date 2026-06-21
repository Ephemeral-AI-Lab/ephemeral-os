use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::stack::publish::model::{
    ContentFingerprint, PublishBase, PublishBaseRevision, PublishRejectReason,
    PublishValidatedChangesRequest,
};

use super::*;

struct PublishFixture {
    base: PathBuf,
    root: PathBuf,
    workspace: PathBuf,
}

impl PublishFixture {
    fn new(label: &str) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let base = std::env::temp_dir().join(format!(
            "layerstack-publish-{label}-{}-{}",
            std::process::id(),
            NEXT_PUBLISH_TEST.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("layer-stack");
        let workspace = base.join("workspace");
        std::fs::create_dir_all(&workspace)?;
        Ok(Self {
            base,
            root,
            workspace,
        })
    }

    fn build_base(&self) -> Result<Manifest, Box<dyn std::error::Error + Send + Sync>> {
        build_workspace_base(&self.root, &self.workspace, false)?;
        let stack = LayerStack::open(self.root.clone())?;
        Ok(stack.read_active_manifest()?)
    }

    fn stack(&self) -> Result<LayerStack, LayerStackError> {
        LayerStack::open(self.root.clone())
    }
}

impl Drop for PublishFixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.base);
    }
}

static NEXT_PUBLISH_TEST: AtomicU64 = AtomicU64::new(0);

fn lp(path: &str) -> LayerPath {
    LayerPath::parse(path).expect("test layer path is valid")
}

fn request(base: Manifest, changes: Vec<LayerChange>) -> PublishValidatedChangesRequest {
    PublishValidatedChangesRequest {
        base: PublishBase {
            revision: PublishBaseRevision {
                manifest_version: base.version,
                root_hash: manifest_root_hash(&base),
                layer_count: base.layers.len(),
            },
            manifest: base,
        },
        changes,
        protected_drops: Vec::new(),
    }
}

fn read_text(
    root: &std::path::Path,
    manifest: &Manifest,
    path: &str,
) -> Result<Option<String>, Box<dyn std::error::Error + Send + Sync>> {
    let view = MergedView::new(root.to_path_buf());
    let (bytes, exists) = view.read_bytes(path, manifest)?;
    if !exists {
        return Ok(None);
    }
    let bytes = bytes.expect("merged view returned bytes for existing path");
    Ok(Some(
        String::from_utf8(bytes).expect("test content is utf8"),
    ))
}

#[test]
fn source_occ_publish_succeeds_when_active_matches_base(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("source-success")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;

    let result = fixture.stack()?.publish_validated_changes(request(
        base,
        vec![LayerChange::Write {
            path: lp("README.md"),
            content: b"command\n".to_vec(),
        }],
    ))?;

    assert!(!result.no_op);
    assert_eq!(result.route_summary.source_count, 1);
    assert_eq!(
        read_text(&fixture.root, &result.manifest, "README.md")?,
        Some("command\n".to_owned())
    );
    Ok(())
}

#[test]
fn source_occ_conflict_rejects_without_publishing_ignored_changes(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("source-conflict")?;
    std::fs::write(fixture.workspace.join(".gitignore"), "ignored.log\n")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;
    let mut stack = fixture.stack()?;
    let advanced = stack.publish_layer(&[LayerChange::Write {
        path: lp("README.md"),
        content: b"advanced\n".to_vec(),
    }])?;

    let error = stack
        .publish_validated_changes(request(
            base,
            vec![
                LayerChange::Write {
                    path: lp("README.md"),
                    content: b"command\n".to_vec(),
                },
                LayerChange::Write {
                    path: lp("ignored.log"),
                    content: b"ignored\n".to_vec(),
                },
            ],
        ))
        .expect_err("source conflict rejects publish");

    assert!(matches!(
        error,
        LayerStackError::PublishRejected(rejection)
            if rejection.reason == PublishRejectReason::SourceConflict
    ));
    let active = fixture.stack()?.read_active_manifest()?;
    assert_eq!(active, advanced);
    assert_eq!(read_text(&fixture.root, &active, "ignored.log")?, None);
    Ok(())
}

#[test]
fn ignored_only_publish_uses_command_base_gitignore(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("ignored-base")?;
    std::fs::write(fixture.workspace.join(".gitignore"), "out.log\n")?;
    let base = fixture.build_base()?;
    let mut stack = fixture.stack()?;
    stack.publish_layer(&[
        LayerChange::Write {
            path: lp(".gitignore"),
            content: Vec::new(),
        },
        LayerChange::Write {
            path: lp("out.log"),
            content: b"active\n".to_vec(),
        },
    ])?;

    let result = stack.publish_validated_changes(request(
        base,
        vec![LayerChange::Write {
            path: lp("out.log"),
            content: b"command\n".to_vec(),
        }],
    ))?;

    assert_eq!(result.route_summary.ignored_count, 1);
    assert_eq!(
        read_text(&fixture.root, &result.manifest, "out.log")?,
        Some("command\n".to_owned())
    );
    Ok(())
}

#[test]
fn nested_gitignore_anchored_patterns_do_not_double_strip(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("nested-gitignore")?;
    std::fs::create_dir_all(fixture.workspace.join("pkg/pkg"))?;
    std::fs::write(fixture.workspace.join("pkg/.gitignore"), "/pkg.log\n")?;
    let base = fixture.build_base()?;

    let result = fixture.stack()?.publish_validated_changes(request(
        base,
        vec![
            LayerChange::Write {
                path: lp("pkg/pkg.log"),
                content: b"ignored\n".to_vec(),
            },
            LayerChange::Write {
                path: lp("pkg/pkg/pkg.log"),
                content: b"source\n".to_vec(),
            },
        ],
    ))?;

    assert_eq!(result.route_summary.ignored_count, 1);
    assert_eq!(result.route_summary.source_count, 1);
    Ok(())
}

#[test]
fn git_mutation_and_protected_paths_reject() -> Result<(), Box<dyn std::error::Error + Send + Sync>>
{
    let fixture = PublishFixture::new("forbidden")?;
    std::fs::write(fixture.workspace.join("README.md"), "base\n")?;
    let base = fixture.build_base()?;

    for (path, reason) in [
        ("pkg/.git/config", PublishRejectReason::GitMutationForbidden),
        ("layers", PublishRejectReason::ProtectedPath),
        ("staging", PublishRejectReason::ProtectedPath),
        (".layer-metadata", PublishRejectReason::ProtectedPath),
        (
            "pkg/.layer-metadata/file",
            PublishRejectReason::ProtectedPath,
        ),
    ] {
        let error = fixture
            .stack()?
            .publish_validated_changes(request(
                base.clone(),
                vec![LayerChange::Write {
                    path: lp(path),
                    content: b"x".to_vec(),
                }],
            ))
            .expect_err("forbidden path rejects publish");
        assert!(
            matches!(error, LayerStackError::PublishRejected(ref rejection) if rejection.reason == reason),
            "unexpected error for {path}: {error:?}"
        );
    }
    Ok(())
}

#[test]
fn invalid_gitignore_does_not_panic_and_contributes_no_rules(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("invalid-gitignore")?;
    std::fs::write(fixture.workspace.join(".gitignore"), "[\n")?;
    let base = fixture.build_base()?;

    let result = fixture.stack()?.publish_validated_changes(request(
        base,
        vec![LayerChange::Write {
            path: lp("file.log"),
            content: b"source\n".to_vec(),
        }],
    ))?;

    assert_eq!(result.route_summary.source_count, 1);
    Ok(())
}

#[test]
fn symlink_fingerprints_report_source_conflicts(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("symlink-conflict")?;
    std::os::unix::fs::symlink("base-target", fixture.workspace.join("link"))?;
    let base = fixture.build_base()?;
    let mut stack = fixture.stack()?;
    stack.publish_layer(&[LayerChange::Symlink {
        path: lp("link"),
        source_path: "active-target".to_owned(),
    }])?;

    let error = stack
        .publish_validated_changes(request(
            base,
            vec![LayerChange::Symlink {
                path: lp("link"),
                source_path: "command-target".to_owned(),
            }],
        ))
        .expect_err("symlink target mismatch conflicts");

    match error {
        LayerStackError::PublishRejected(rejection)
            if rejection.reason == PublishRejectReason::SourceConflict =>
        {
            let conflict = rejection
                .source_conflict
                .expect("source conflict is included");
            assert!(matches!(
                conflict.expected,
                ContentFingerprint::Symlink { ref target } if target == "base-target"
            ));
            assert!(matches!(
                conflict.actual,
                ContentFingerprint::Symlink { ref target } if target == "active-target"
            ));
        }
        other => panic!("unexpected error: {other:?}"),
    }
    Ok(())
}

#[test]
fn opaque_dir_over_mixed_source_and_ignored_descendants_rejects(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = PublishFixture::new("opaque-mixed")?;
    std::fs::write(fixture.workspace.join(".gitignore"), "target/ignored.log\n")?;
    std::fs::create_dir_all(fixture.workspace.join("target"))?;
    std::fs::write(fixture.workspace.join("target/source.txt"), "source\n")?;
    std::fs::write(fixture.workspace.join("target/ignored.log"), "ignored\n")?;
    let base = fixture.build_base()?;

    let error = fixture
        .stack()?
        .publish_validated_changes(request(
            base,
            vec![LayerChange::OpaqueDir { path: lp("target") }],
        ))
        .expect_err("opaque dir mixed routes reject");

    assert!(matches!(
        error,
        LayerStackError::PublishRejected(rejection)
            if rejection.reason == PublishRejectReason::OpaqueDirMixedRoutes
    ));
    Ok(())
}
