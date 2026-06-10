use std::path::Path;
use std::sync::Arc;

use crate::commit::{CommitQueue, CommitService, CommitTransaction};
use crate::route::StackRouteProvider;
use crate::test_fixture::{unique_suffix, TestResult};

use super::{normalize_root_key, RootService, ServiceCache, SERVICE_CACHE_MAX};

fn root_service(root: &Path) -> TestResult<RootService> {
    let transaction = CommitTransaction {
        root: root.to_path_buf(),
    };
    let provider = Arc::new(StackRouteProvider {
        root: root.to_path_buf(),
    });
    Ok(Arc::new(CommitService::with_route_provider(
        CommitQueue::new(transaction),
        provider,
    )?))
}

#[test]
fn service_cache_is_bounded_lru() -> TestResult {
    let mut cache = ServiceCache::default();
    let base = std::env::temp_dir().join(format!("eos-service-cache-{}", unique_suffix()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base)?;

    let first = base.join("root-000");
    for index in 0..=SERVICE_CACHE_MAX {
        let root = base.join(format!("root-{index:03}"));
        std::fs::create_dir_all(&root)?;
        let (lookup, _rejected) =
            cache.insert_or_get(normalize_root_key(&root), root_service(&root)?, 0.0);
        assert!(lookup.cache_created);
    }

    assert_eq!(cache.entries.len(), SERVICE_CACHE_MAX);
    assert_eq!(cache.stats.evictions_total, 1);

    let (recreated, _rejected) =
        cache.insert_or_get(normalize_root_key(&first), root_service(&first)?, 0.0);
    assert!(!recreated.cache_hit);
    assert!(recreated.cache_created);
    assert_eq!(recreated.evicted_count, 1);

    let _ = std::fs::remove_dir_all(base);
    Ok(())
}
