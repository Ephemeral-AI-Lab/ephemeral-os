use std::sync::mpsc;

use crate::commit::route::PublishDecision;
use crate::commit::worker::queue::{
    cas_exhaustion_result, disjoint_batches, PublishConflict, WorkItem, MAX_OCC_CAS_RETRIES,
};
use crate::commit::worker::PreparedChangeset;
use crate::commit::CommitStatus;
use crate::model::LayerPath;

fn prepared(
    path: &str,
    atomic: bool,
) -> Result<PreparedChangeset, Box<dyn std::error::Error + Send + Sync>> {
    let path = LayerPath::parse(path)?;
    let changes = vec![crate::model::LayerChange::Write {
        path: path.clone(),
        content: b"x".to_vec(),
    }];
    Ok(PreparedChangeset::try_new(
        &changes,
        vec![PublishDecision::gated(path, None)],
        atomic,
    )?)
}

fn item(path: &str, atomic: bool) -> Result<WorkItem, Box<dyn std::error::Error + Send + Sync>> {
    let (reply, _) = mpsc::channel();
    Ok(WorkItem {
        prepared: prepared(path, atomic)?,
        reply,
    })
}

#[test]
fn batches_disjoint_non_atomic_changesets() -> Result<(), Box<dyn std::error::Error + Send + Sync>>
{
    let batches = disjoint_batches(vec![item("a.txt", false)?, item("b.txt", false)?]);
    assert_eq!(batches.len(), 1);
    assert_eq!(batches[0].len(), 2);
    Ok(())
}

#[test]
fn atomic_changesets_are_not_batched() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let batches = disjoint_batches(vec![item("a.txt", true)?, item("b.txt", true)?]);
    assert_eq!(batches.len(), 2);
    assert_eq!(batches[0].len(), 1);
    assert_eq!(batches[1].len(), 1);
    Ok(())
}

#[test]
fn overlapping_non_atomic_changesets_are_not_batched(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let batches = disjoint_batches(vec![item("a.txt", false)?, item("a.txt", false)?]);
    assert_eq!(batches.len(), 2);
    assert_eq!(batches[0].len(), 1);
    assert_eq!(batches[1].len(), 1);
    Ok(())
}

#[test]
fn cas_retry_exhaustion_surfaces_aborted_version(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let prepared = prepared("a.txt", true)?;
    let result = cas_exhaustion_result(
        &prepared,
        &PublishConflict {
            observed_version: Some(42),
        },
        MAX_OCC_CAS_RETRIES,
    );
    assert!(!result.success());
    assert_eq!(result.files[0].status, CommitStatus::AbortedVersion);
    assert_eq!(result.files[0].observed_version, Some(42));
    assert_eq!(
        result.files[0].observed_state.as_deref(),
        Some("manifest_conflict")
    );
    assert_eq!(
        result
            .files
            .iter()
            .filter(|file| file.status == CommitStatus::AbortedVersion)
            .count(),
        1
    );
    Ok(())
}
