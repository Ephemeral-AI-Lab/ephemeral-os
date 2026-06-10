use std::path::PathBuf;

use eos_cas::LayerChange;

use crate::commit::outcome::{CommitStatus, PublishDecision, Route};
use crate::commit::queue::{CommitTransactionPort, PreparedChangeset};
use crate::commit::{base_hashes_for_snapshot, hash_bytes};
use crate::test_fixture::{lp, Fixture, TestResult};
use crate::LayerStack;

use super::CommitTransaction;

fn transaction(fixture: &Fixture) -> CommitTransaction {
    CommitTransaction {
        root: fixture.root.clone(),
    }
}

fn publish_decision(
    path: &str,
    route: Route,
    base_hash: Option<String>,
) -> TestResult<PublishDecision> {
    Ok(PublishDecision {
        path: lp(path)?,
        route,
        base_hash,
        message: None,
    })
}

#[test]
fn base_hashes_accept_opaque_dir_over_existing_directory() -> TestResult {
    let fixture = Fixture::new("opaque_base_hash")?;
    std::fs::create_dir_all(fixture.root.join("layers/B000001-base/opaque_dir"))?;
    std::fs::write(
        fixture.root.join("layers/B000001-base/opaque_dir/old.txt"),
        "old\n",
    )?;
    let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;

    let hashes = base_hashes_for_snapshot(
        &fixture.root,
        &manifest,
        &[LayerChange::OpaqueDir {
            path: lp("opaque_dir")?,
        }],
    )?;

    assert_eq!(hashes, vec![(lp("opaque_dir")?, None)]);
    Ok(())
}

#[test]
fn gated_stale_base_aborts_without_publish() -> TestResult {
    let fixture = Fixture::new("gated_stale")?;
    let old_hash = hash_bytes(b"# README\n");
    LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
        path: lp("README.md")?,
        content: b"# theirs\n".to_vec(),
    }])?;

    let result = transaction(&fixture)
        .revalidate_and_publish(&PreparedChangeset {
            snapshot_version: Some(1),
            path_groups: vec![publish_decision("README.md", Route::Gated, Some(old_hash))?],
            changes: vec![LayerChange::Write {
                path: lp("README.md")?,
                content: b"# mine\n".to_vec(),
            }],
            atomic: true,
        })
        .map_err(|conflict| format!("unexpected publish conflict: {conflict:?}"))?;

    assert_eq!(result.published_manifest_version, None);
    assert_eq!(result.files[0].status, CommitStatus::AbortedVersion);
    assert_eq!(fixture.read_text("README.md")?, "# theirs\n");
    Ok(())
}

#[test]
fn direct_route_ignores_stale_base_and_publishes() -> TestResult {
    let fixture = Fixture::new("direct_stale")?;
    LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
        path: lp("target/out.txt")?,
        content: b"theirs\n".to_vec(),
    }])?;

    let result = transaction(&fixture)
        .revalidate_and_publish(&PreparedChangeset {
            snapshot_version: Some(1),
            path_groups: vec![publish_decision(
                "target/out.txt",
                Route::Direct,
                Some("stale".to_owned()),
            )?],
            changes: vec![LayerChange::Write {
                path: lp("target/out.txt")?,
                content: b"mine\n".to_vec(),
            }],
            atomic: true,
        })
        .map_err(|conflict| format!("unexpected publish conflict: {conflict:?}"))?;

    assert!(result.success());
    assert_eq!(result.files[0].status, CommitStatus::Committed);
    assert_eq!(fixture.read_text("target/out.txt")?, "mine\n");
    Ok(())
}

#[test]
fn gated_symlink_change_validates_and_publishes() -> TestResult {
    let fixture = Fixture::new("gated_symlink")?;
    let result = transaction(&fixture)
        .revalidate_and_publish(&PreparedChangeset {
            snapshot_version: Some(1),
            path_groups: vec![publish_decision("link.txt", Route::Gated, None)?],
            changes: vec![LayerChange::Symlink {
                path: lp("link.txt")?,
                source_path: "target.txt".to_owned(),
            }],
            atomic: true,
        })
        .map_err(|conflict| format!("unexpected publish conflict: {conflict:?}"))?;

    assert!(result.success());
    assert_eq!(result.files[0].status, CommitStatus::Committed);
    let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;
    let projected = fixture.base.join("projected");
    crate::MergedView::new(fixture.root.clone()).project(&projected, &manifest)?;
    assert_eq!(
        std::fs::read_link(projected.join("link.txt"))?,
        PathBuf::from("target.txt")
    );
    Ok(())
}

#[test]
fn atomic_mixed_validation_failure_drops_accepted_paths() -> TestResult {
    let fixture = Fixture::new("atomic_mixed")?;
    let old_hash = hash_bytes(b"# README\n");
    LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
        path: lp("README.md")?,
        content: b"# theirs\n".to_vec(),
    }])?;

    let result = transaction(&fixture)
        .revalidate_and_publish(&PreparedChangeset {
            snapshot_version: Some(1),
            path_groups: vec![
                publish_decision("README.md", Route::Gated, Some(old_hash))?,
                publish_decision("target/out.txt", Route::Direct, None)?,
            ],
            changes: vec![
                LayerChange::Write {
                    path: lp("README.md")?,
                    content: b"# mine\n".to_vec(),
                },
                LayerChange::Write {
                    path: lp("target/out.txt")?,
                    content: b"ok\n".to_vec(),
                },
            ],
            atomic: true,
        })
        .map_err(|conflict| format!("unexpected publish conflict: {conflict:?}"))?;

    assert_eq!(result.published_manifest_version, None);
    assert_eq!(result.files[0].status, CommitStatus::AbortedVersion);
    assert_eq!(result.files[1].status, CommitStatus::Dropped);
    assert_eq!(fixture.read_text("README.md")?, "# theirs\n");
    assert!(
        !LayerStack::open(fixture.root.clone())?
            .read_bytes("target/out.txt")?
            .1
    );
    Ok(())
}
