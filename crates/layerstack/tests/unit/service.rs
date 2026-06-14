use std::path::Path;

use crate::commit::CommitWriter;
use crate::model::LayerChange;
use crate::test_fixture::{lp, unique_suffix, Fixture, TestResult};
use crate::{hash_current, reset_process_state_for_tests, service, CommitOptions, LayerStack};

use super::{normalize_root_key, snapshot_manifest, RootService, ServiceCache, SERVICE_CACHE_MAX};

fn root_service(root: &Path) -> TestResult<RootService> {
    Ok(std::sync::Arc::new(CommitWriter::with_options(
        root.to_path_buf(),
        CommitOptions::default(),
    )?))
}

#[test]
fn snapshot_manifest_converts_absolute_layer_paths_to_relative() -> TestResult {
    let root = std::path::PathBuf::from("/stack");
    let manifest = snapshot_manifest(&root, 7, &[root.join("layers/a"), root.join("layers/b")])?;

    assert_eq!(manifest.version, 7);
    assert_eq!(manifest.layers[0].path, "layers/a");
    assert_eq!(manifest.layers[1].path, "layers/b");
    Ok(())
}

#[test]
fn snapshot_manifest_rejects_absolute_layer_paths_outside_root() {
    let error = snapshot_manifest(
        &std::path::PathBuf::from("/stack"),
        7,
        &[std::path::PathBuf::from("/other/layers/a")],
    )
    .expect_err("outside-root path should fail");

    assert!(
        error.to_string().contains("outside /stack"),
        "unexpected error: {error}"
    );
}

#[test]
fn commit_direct_trace_events_include_worker_handoff_and_batch_facts() -> TestResult {
    let fixture = Fixture::new("worker_handoff_trace")?;
    let base_hash =
        hash_current(Some(b"# README\n"), true).ok_or_else(|| "missing base hash".to_owned())?;

    let result = service::commit_direct(
        &fixture.root,
        Some(1),
        &[LayerChange::Write {
            path: lp("README.md")?,
            content: b"# updated\n".to_vec(),
        }],
        &[(lp("README.md")?, Some(base_hash))],
    )?;

    assert!(result.success());
    let events = result.trace_events();
    let handoff = events
        .iter()
        .find(|event| event.module == "occ" && event.name == "worker_handoff")
        .expect("worker handoff event");
    assert_eq!(handoff.details["path_count"], 1);
    assert_eq!(handoff.details["publishable_change_count"], 1);
    assert_eq!(handoff.details["atomic"], true);
    assert_eq!(handoff.details["gated_path_count"], 1);
    assert_eq!(handoff.details["direct_path_count"], 0);
    assert_eq!(handoff.details["drop_path_count"], 0);

    let batch = events
        .iter()
        .find(|event| event.module == "occ" && event.name == "worker_batch_finished")
        .expect("worker batch event");
    assert_eq!(batch.details["batch_item_count"], 1);
    assert_eq!(batch.details["combined_path_count"], 1);
    assert_eq!(batch.details["combined_change_count"], 1);
    assert_eq!(batch.details["atomic"], true);
    assert_eq!(batch.details["cas_retry_count"], 0);
    Ok(())
}

#[test]
fn process_state_reset_clears_service_cache_and_lease_registry() -> TestResult {
    reset_process_state_for_tests();
    let fixture = Fixture::new("process_state_reset")?;
    let _snapshot = service::acquire_snapshot(&fixture.root, "reset-test")?;
    {
        let stack = LayerStack::open(fixture.root.clone())?;
        assert_eq!(stack.active_lease_count(), 1, "lease registry has snapshot");
    }

    let base_hash =
        hash_current(Some(b"# README\n"), true).ok_or_else(|| "missing base hash".to_owned())?;
    let result = service::commit_direct(
        &fixture.root,
        Some(1),
        &[LayerChange::Write {
            path: lp("README.md")?,
            content: b"# reset\n".to_vec(),
        }],
        &[(lp("README.md")?, Some(base_hash))],
    )?;
    assert!(result.success(), "commit creates a cached per-root writer");
    assert!(service::service_cache_contains_root_for_tests(
        &fixture.root
    ));

    reset_process_state_for_tests();

    assert!(!service::service_cache_contains_root_for_tests(
        &fixture.root
    ));
    let stack = LayerStack::open(fixture.root.clone())?;
    assert_eq!(stack.active_lease_count(), 0, "lease registry was reset");
    Ok(())
}

#[test]
fn service_cache_is_bounded_lru() -> TestResult {
    let mut cache = ServiceCache::default();
    let base = std::env::temp_dir().join(format!("eos-service-cache-{}", unique_suffix()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base)?;

    let capacity = u64::try_from(SERVICE_CACHE_MAX)?;
    let first = base.join("root-000");
    for index in 0..=SERVICE_CACHE_MAX {
        let root = base.join(format!("root-{index:03}"));
        std::fs::create_dir_all(&root)?;
        let _ = cache.insert_or_get(normalize_root_key(&root), root_service(&root)?, 0.0);
    }

    assert_eq!(cache.entries.len(), SERVICE_CACHE_MAX);
    assert_eq!(cache.stats.creates_total, capacity + 1, "every root is new");
    assert_eq!(cache.stats.evictions_total, 1, "oldest root evicted");

    // root-000 was evicted, so re-inserting it creates again and evicts the
    // next-oldest entry instead of hitting the cache.
    let _ = cache.insert_or_get(normalize_root_key(&first), root_service(&first)?, 0.0);
    assert_eq!(cache.stats.hits_total, 0, "no lookup ever hit");
    assert_eq!(
        cache.stats.creates_total,
        capacity + 2,
        "evicted root recreated"
    );
    assert_eq!(
        cache.stats.evictions_total, 2,
        "recreate evicts next oldest"
    );
    assert_eq!(cache.entries.len(), SERVICE_CACHE_MAX);

    let _ = std::fs::remove_dir_all(base);
    Ok(())
}
