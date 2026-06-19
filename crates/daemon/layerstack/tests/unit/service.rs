use std::path::Path;

use crate::commit::CommitWriter;
use crate::model::LayerChange;
use crate::test_fixture::{lp, unique_suffix, Fixture, TestResult};
use crate::{reset_process_state_for_tests, service, CommitOptions, LayerStack, MergedView};

use super::{
    normalize_root_key, snapshot_manifest, snapshot_manifest_preserving_layer_ids, RootService,
    ServiceCache, SERVICE_CACHE_MAX,
};

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
fn snapshot_manifest_preserving_layer_ids_uses_layer_dir_names() -> TestResult {
    let root = std::path::PathBuf::from("/stack");
    let manifest = snapshot_manifest_preserving_layer_ids(
        &root,
        7,
        &[
            root.join("layers/L000002-a"),
            root.join("layers/B000001-base"),
        ],
    )?;

    assert_eq!(manifest.layers[0].layer_id, "L000002-a");
    assert_eq!(manifest.layers[1].layer_id, "B000001-base");
    assert_eq!(manifest.layers[0].path, "layers/L000002-a");
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
    let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;
    let layer_paths = manifest
        .layers
        .iter()
        .map(|layer| fixture.root.join(&layer.path))
        .collect::<Vec<_>>();

    let result = service::publish_command_capture_lane_aware(
        &fixture.root,
        manifest.version,
        &layer_paths,
        &[LayerChange::Write {
            path: lp("README.md")?,
            content: b"# updated\n".to_vec(),
        }],
        &[],
        CommitOptions::default(),
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

    let manifest = LayerStack::open(fixture.root.clone())?.read_active_manifest()?;
    let layer_paths = manifest
        .layers
        .iter()
        .map(|layer| fixture.root.join(&layer.path))
        .collect::<Vec<_>>();
    let result = service::publish_command_capture_lane_aware(
        &fixture.root,
        manifest.version,
        &layer_paths,
        &[LayerChange::Write {
            path: lp("README.md")?,
            content: b"# reset\n".to_vec(),
        }],
        &[],
        CommitOptions::default(),
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
fn compact_snapshot_for_remount_retargets_active_lease_to_one_layer() -> TestResult {
    reset_process_state_for_tests();
    let fixture = Fixture::new("compact_snapshot_retarget")?;
    for index in 0..4 {
        LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
            path: lp("large.txt")?,
            content: format!("value-{index}\n").into_bytes(),
        }])?;
    }
    let snapshot = service::acquire_snapshot(&fixture.root, "lease-compaction")?;
    assert!(
        snapshot.layer_paths.len() > 1,
        "test requires a retained multi-layer snapshot"
    );

    let compaction = service::compact_snapshot_for_remount(
        &fixture.root,
        snapshot.manifest_version,
        &snapshot.layer_paths,
    )?;
    assert_eq!(compaction.before_layer_count, snapshot.layer_paths.len());
    assert_eq!(compaction.after_layer_count, 1);
    assert_eq!(compaction.layer_paths.len(), 1);
    let (bytes, exists) =
        MergedView::new(fixture.root.clone()).read_bytes("large.txt", &compaction.manifest)?;
    assert!(exists);
    assert_eq!(bytes, Some(b"value-3\n".to_vec()));

    let mut stack = LayerStack::open(fixture.root.clone())?;
    assert_eq!(stack.active_lease_count(), 1);
    assert_eq!(stack.leased_layers().len(), snapshot.layer_paths.len());
    assert!(stack.retarget_lease_manifest(&snapshot.lease_id, compaction.manifest)?);
    assert_eq!(stack.active_lease_count(), 1, "retarget keeps lease alive");
    assert_eq!(
        stack.leased_layers().len(),
        1,
        "lease now pins only the compact checkpoint"
    );
    Ok(())
}

#[test]
fn acquire_bounded_snapshot_for_command_normalizes_before_lease() -> TestResult {
    reset_process_state_for_tests();
    let fixture = Fixture::new("bounded_command_snapshot")?;
    for index in 0..5 {
        LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
            path: lp("large.txt")?,
            content: vec![u8::try_from(index)?; 1024],
        }])?;
    }

    let command_snapshot =
        service::acquire_bounded_snapshot_for_command(&fixture.root, "command-launch", 2)?;

    assert!(
        command_snapshot.normalization.triggered,
        "depth above max must normalize before the command lease is acquired"
    );
    assert!(
        command_snapshot.normalization.active_depth_before > 2,
        "test must start above the configured max depth"
    );
    assert_eq!(command_snapshot.normalization.active_depth_after, 1);
    assert_eq!(command_snapshot.normalization.checkpoint_count, 1);
    assert_eq!(command_snapshot.snapshot.layer_paths.len(), 1);

    let stack = LayerStack::open(fixture.root.clone())?;
    assert_eq!(
        stack.read_active_manifest()?.depth(),
        1,
        "active generation is bounded before command launch"
    );
    assert_eq!(stack.active_lease_count(), 1);
    assert_eq!(
        stack.leased_layers().len(),
        1,
        "new command lease points at the compact generation"
    );
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
