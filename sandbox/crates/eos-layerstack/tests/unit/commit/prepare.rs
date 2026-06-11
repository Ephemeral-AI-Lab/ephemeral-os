use std::collections::BTreeMap;

use crate::model::{LayerChange, LayerPath};

use super::super::outcome::{CommitStatus, FileResult};
use super::super::queue::PublishConflict;
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

struct RecordingTransaction;

impl CommitTransactionPort for RecordingTransaction {
    fn revalidate_and_publish(
        &self,
        combined: &PreparedChangeset,
    ) -> Result<ChangesetResult, PublishConflict> {
        let mut timings = BTreeMap::new();
        timings.insert("occ.commit.total_s".to_owned(), 0.123);
        Ok(ChangesetResult {
            files: combined
                .path_groups
                .iter()
                .map(|group| FileResult {
                    path: group.path.clone(),
                    status: CommitStatus::Committed,
                    message: String::new(),
                })
                .collect(),
            published_manifest_version: Some(3),
            timings,
        })
    }
}

#[test]
fn apply_changeset_adds_public_apply_timing_envelope() -> TestResult {
    let queue = CommitQueue::with_config(RecordingTransaction, 64, 0.0, 3);
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
    assert!(
        result
            .timings
            .get("occ.apply.commit_worker_s")
            .copied()
            .unwrap_or_default()
            >= 0.123
    );
    assert!(result.timings.contains_key("occ.apply.commit_s"));
    assert!(result.timings.contains_key("occ.apply.total_s"));
    assert_eq!(
        result.timings.get("occ.apply.manifest_lag").copied(),
        Some(1.0)
    );
    Ok(())
}
