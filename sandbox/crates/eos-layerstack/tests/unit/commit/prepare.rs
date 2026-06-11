use std::sync::Arc;

use crate::model::{LayerChange, LayerPath};
use crate::test_fixture::Fixture;

use super::*;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

struct AllGatedRouteProvider;

impl RouteProvider for AllGatedRouteProvider {
    fn is_ignored(&self, _path: &LayerPath) -> Result<bool, CommitError> {
        Ok(false)
    }

    fn base_hash(&self, _path: &LayerPath) -> Result<Option<String>, CommitError> {
        Ok(None)
    }
}

#[test]
fn apply_changeset_adds_public_apply_timing_envelope() -> TestResult {
    let fixture = Fixture::new("commit_prepare_timing")?;
    let queue = CommitQueue::with_config(
        CommitTransaction {
            root: fixture.root.clone(),
        },
        64,
        0.0,
        3,
    );
    let service = CommitService::with_route_provider(queue, Arc::new(AllGatedRouteProvider))?;
    let path = LayerPath::parse("timed.txt")?;
    let result = service.apply_changeset_with_base_hashes(
        &[LayerChange::Write {
            path,
            content: b"x".to_vec(),
        }],
        Some(1),
        true,
        &[],
    )?;

    assert!(result.success());
    assert!(result.timings.contains_key("occ.apply.commit_queue_wait_s"));
    assert_eq!(
        result
            .timings
            .get("occ.apply.commit_resume_wait_s")
            .copied(),
        Some(0.0)
    );
    assert!(result.timings.contains_key("occ.apply.commit_worker_s"));
    assert!(result.timings.contains_key("occ.apply.commit_s"));
    assert!(result.timings.contains_key("occ.apply.total_s"));
    assert_eq!(
        result.timings.get("occ.apply.manifest_lag").copied(),
        Some(0.0)
    );
    Ok(())
}
