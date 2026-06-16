use std::path::PathBuf;

use crate::commit::{
    base_hashes_for_snapshot, hash_current, CommitStatus, PreparedChangeset, PublishDecision, Route,
};
use crate::model::LayerChange;
use crate::test_fixture::{lp, Fixture, TestResult};
use crate::{CommitOptions, LayerStack};

use super::{run_auto_squash, CommitTransaction};

fn transaction(fixture: &Fixture) -> CommitTransaction {
    CommitTransaction {
        root: fixture.root.clone(),
        options: CommitOptions::default(),
    }
}

fn readme_base_hash() -> TestResult<String> {
    hash_current(Some(b"# README\n"), true).ok_or_else(|| "missing readme hash".into())
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
        drop_reason: None,
        reject_publish: false,
        validation_base_hashes: None,
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
    let old_hash = readme_base_hash()?;
    LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
        path: lp("README.md")?,
        content: b"# theirs\n".to_vec(),
    }])?;

    let result = transaction(&fixture)
        .revalidate_and_publish(&PreparedChangeset {
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
    assert_eq!(
        result.files[0].observed_state.as_deref(),
        Some("content_changed")
    );
    let events = result.trace_events();
    assert_eq!(events.len(), 4);
    assert_eq!(events[2].module, "occ");
    assert_eq!(events[2].name, "commit_finished");
    assert_eq!(events[2].details["success"], false);
    assert_eq!(events[2].details["aborted_version_file_count"], 1);
    assert_eq!(events[3].module, "occ");
    assert_eq!(events[3].name, "conflict_detected");
    assert_eq!(events[3].details["path"], "README.md");
    assert_eq!(events[3].details["reason"], "aborted_version");
    assert_eq!(events[3].details["message"], "content changed");
    assert_eq!(events[3].details["observed_state"], "content_changed");
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
    let events = result.trace_events();
    let manifest = events
        .iter()
        .find(|event| event.module == "layer_stack" && event.name == "manifest_validated")
        .expect("manifest validation event");
    assert_eq!(manifest.details["manifest_version"], 2);
    assert_eq!(manifest.details["manifest_depth"], 2);
    assert_eq!(manifest.details["manifest_path_count"], 2);
    assert_eq!(manifest.details["active_lease_count"], 0);
    let published = events
        .iter()
        .find(|event| event.module == "layer_stack" && event.name == "publish_layer_finished")
        .expect("publish layer event");
    assert_eq!(published.details["success"], true);
    assert_eq!(published.details["manifest_version_before"], 2);
    assert_eq!(published.details["manifest_version_after"], 3);
    assert_eq!(published.details["published_manifest_version"], 3);
    assert_eq!(published.details["published_layer_count"], 1);
    assert_eq!(fixture.read_text("target/out.txt")?, "mine\n");
    Ok(())
}

#[test]
fn auto_squash_skip_reason_is_traced_when_stack_is_too_shallow() -> TestResult {
    let fixture = Fixture::new("auto_squash_too_shallow")?;
    let mut stack = LayerStack::open(fixture.root.clone())?;

    let trace = run_auto_squash(&mut stack, crate::AUTO_SQUASH_MAX_DEPTH);

    assert!(trace.timings.is_empty());
    assert_eq!(trace.events.len(), 1);
    assert_eq!(trace.events[0].module, "layer_stack");
    assert_eq!(trace.events[0].name, "auto_squash_skipped");
    assert_eq!(trace.events[0].details["reason"], "too_shallow");
    assert_eq!(trace.events[0].details["max_depth"], 100);
    assert_eq!(trace.events[0].details["depth_before"], 1);
    Ok(())
}

#[test]
fn auto_squash_finished_event_records_depth_and_manifest() -> TestResult {
    let fixture = Fixture::new("auto_squash_finished")?;
    let mut stack = LayerStack::open(fixture.root.clone())?;
    for index in 0..3 {
        stack.publish_layer(&[LayerChange::Write {
            path: lp(&format!("file-{index}.txt"))?,
            content: format!("{index}\n").into_bytes(),
        }])?;
    }

    let trace = run_auto_squash(&mut stack, 2);

    assert_eq!(trace.events.len(), 2);
    assert_eq!(trace.events[0].module, "layer_stack");
    assert_eq!(trace.events[0].name, "auto_squash_started");
    assert_eq!(trace.events[0].details["max_depth"], 2);
    assert_eq!(trace.events[0].details["depth_before"], 4);
    assert_eq!(trace.events[1].module, "layer_stack");
    assert_eq!(trace.events[1].name, "auto_squash_finished");
    assert_eq!(trace.events[1].details["success"], true);
    assert_eq!(trace.events[1].details["max_depth"], 2);
    assert_eq!(trace.events[1].details["depth_before"], 4);
    assert_eq!(trace.events[1].details["depth_after"], 1);
    assert_eq!(trace.events[1].details["manifest_version"], 5);
    assert!(trace
        .timings
        .contains_key("layer_stack.auto_squash.total_s"));
    Ok(())
}

#[test]
fn auto_squash_failure_finishes_with_error_reason() -> TestResult {
    let fixture = Fixture::new("auto_squash_failed")?;
    let mut stack = LayerStack::open(fixture.root.clone())?;
    for index in 0..3 {
        stack.publish_layer(&[LayerChange::Write {
            path: lp(&format!("file-{index}.txt"))?,
            content: format!("{index}\n").into_bytes(),
        }])?;
    }
    let manifest = stack.read_active_manifest()?;
    let missing_layer = fixture.root.join(&manifest.layers[1].path);
    std::fs::remove_dir_all(missing_layer)?;

    let trace = run_auto_squash(&mut stack, 2);

    assert_eq!(trace.events.len(), 2);
    assert_eq!(trace.events[0].module, "layer_stack");
    assert_eq!(trace.events[0].name, "auto_squash_started");
    assert_eq!(trace.events[0].details["max_depth"], 2);
    assert_eq!(trace.events[0].details["depth_before"], 4);
    assert_eq!(trace.events[1].module, "layer_stack");
    assert_eq!(trace.events[1].name, "auto_squash_finished");
    assert_eq!(trace.events[1].details["success"], false);
    assert_eq!(trace.events[1].details["max_depth"], 2);
    assert_eq!(trace.events[1].details["depth_before"], 4);
    assert!(trace.events[1].details["error"].is_string());
    assert!(trace
        .timings
        .contains_key("layer_stack.auto_squash.total_s"));
    Ok(())
}

#[test]
fn gated_symlink_change_validates_and_publishes() -> TestResult {
    let fixture = Fixture::new("gated_symlink")?;
    let result = transaction(&fixture)
        .revalidate_and_publish(&PreparedChangeset {
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
    let old_hash = readme_base_hash()?;
    LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
        path: lp("README.md")?,
        content: b"# theirs\n".to_vec(),
    }])?;

    let result = transaction(&fixture)
        .revalidate_and_publish(&PreparedChangeset {
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
