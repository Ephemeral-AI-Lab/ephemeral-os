use std::sync::{Arc, Mutex};

use crate::model::LayerPath;

use super::*;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Clone)]
struct RecordingTransaction {
    calls: Arc<Mutex<Vec<PreparedChangeset>>>,
    conflicts_before_success: Arc<Mutex<u32>>,
}

impl RecordingTransaction {
    fn new(conflicts_before_success: u32) -> Self {
        Self {
            calls: Arc::new(Mutex::new(Vec::new())),
            conflicts_before_success: Arc::new(Mutex::new(conflicts_before_success)),
        }
    }
}

impl CommitTransactionPort for RecordingTransaction {
    fn revalidate_and_publish(
        &self,
        combined: &PreparedChangeset,
    ) -> Result<ChangesetResult, PublishConflict> {
        self.calls
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .push(combined.clone());
        let should_conflict = {
            let mut remaining = self
                .conflicts_before_success
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if *remaining > 0 {
                *remaining -= 1;
                true
            } else {
                false
            }
        };
        if should_conflict {
            return Err(PublishConflict {
                observed_version: Some(42),
            });
        }
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
            published_manifest_version: Some(2),
            timings: std::collections::BTreeMap::new(),
        })
    }
}

fn prepared(path: &str, atomic: bool) -> TestResult<PreparedChangeset> {
    let path = LayerPath::parse(path)?;
    Ok(PreparedChangeset {
        snapshot_version: Some(1),
        path_groups: vec![PublishDecision {
            path: path.clone(),
            route: Route::Gated,
            base_hash: None,
            message: None,
        }],
        changes: vec![crate::model::LayerChange::Write {
            path,
            content: b"x".to_vec(),
        }],
        atomic,
    })
}

fn recv_ok(
    receiver: &mpsc::Receiver<Result<ChangesetResult, CommitError>>,
) -> TestResult<ChangesetResult> {
    match receiver.recv()? {
        Ok(result) => Ok(result),
        Err(error) => Err(Box::new(error)),
    }
}

#[test]
fn batches_disjoint_non_atomic_changesets() -> TestResult {
    let transaction = RecordingTransaction::new(0);
    let calls = transaction.calls.clone();
    let mut queue = CommitQueue::with_config(transaction, 64, 0.02, 3);
    queue.start()?;
    let first = queue.submit(prepared("a.txt", false)?)?;
    let second = queue.submit(prepared("b.txt", false)?)?;

    assert!(recv_ok(&first)?.success());
    assert!(recv_ok(&second)?.success());
    queue.close()?;

    {
        let calls = calls
            .lock()
            .map_err(|_| std::io::Error::other("calls lock poisoned"))?;
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].path_groups.len(), 2);
        drop(calls);
    }
    Ok(())
}

#[test]
fn atomic_changesets_are_not_batched() -> TestResult {
    let transaction = RecordingTransaction::new(0);
    let calls = transaction.calls.clone();
    let mut queue = CommitQueue::with_config(transaction, 64, 0.02, 3);
    queue.start()?;
    let first = queue.submit(prepared("a.txt", true)?)?;
    let second = queue.submit(prepared("b.txt", true)?)?;

    assert!(recv_ok(&first)?.success());
    assert!(recv_ok(&second)?.success());
    queue.close()?;

    {
        let calls = calls
            .lock()
            .map_err(|_| std::io::Error::other("calls lock poisoned"))?;
        assert_eq!(calls.len(), 2);
        assert_eq!(calls[0].path_groups.len(), 1);
        assert_eq!(calls[1].path_groups.len(), 1);
        drop(calls);
    }
    Ok(())
}

#[test]
fn retries_cas_conflict_then_succeeds() -> TestResult {
    let transaction = RecordingTransaction::new(1);
    let calls = transaction.calls.clone();
    let mut queue = CommitQueue::with_config(transaction, 64, 0.0, 3);
    queue.start()?;
    let result = queue.submit(prepared("a.txt", true)?)?;

    assert!(recv_ok(&result)?.success());
    queue.close()?;

    assert_eq!(
        calls
            .lock()
            .map_err(|_| std::io::Error::other("calls lock poisoned"))?
            .len(),
        2
    );
    Ok(())
}

#[test]
fn cas_retry_exhaustion_surfaces_aborted_version() -> TestResult {
    let transaction = RecordingTransaction::new(3);
    let mut queue = CommitQueue::with_config(transaction, 64, 0.0, 3);
    queue.start()?;
    let result = queue.submit(prepared("a.txt", true)?)?;

    let result = recv_ok(&result)?;
    queue.close()?;

    assert!(!result.success());
    assert_eq!(result.files[0].status, CommitStatus::AbortedVersion);
    Ok(())
}
