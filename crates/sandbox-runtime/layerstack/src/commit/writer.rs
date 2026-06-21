use std::path::PathBuf;

use crate::model::LayerChange;

use super::error::CommitError;
use super::model::ChangesetResult;
use super::route::PublishDecision;
use super::worker::{CommitQueue, CommitTransaction, PreparedChangeset};

pub(crate) struct CommitWriter {
    commit_queue: CommitQueue,
}

impl CommitWriter {
    pub(crate) fn new(root: PathBuf) -> Result<Self, CommitError> {
        let transaction = CommitTransaction { root: root.clone() };
        let mut commit_queue = CommitQueue::new(transaction);
        commit_queue.start()?;
        Ok(Self { commit_queue })
    }

    pub(crate) fn apply_changeset_with_decisions(
        &self,
        changes: &[LayerChange],
        snapshot_version: Option<u64>,
        atomic: bool,
        decisions: Vec<PublishDecision>,
    ) -> Result<ChangesetResult, CommitError> {
        let prepared = PreparedChangeset::try_new(changes, decisions, atomic)?;
        let receiver = self.commit_queue.submit(prepared)?;
        let mut result = receiver
            .recv()
            .map_err(|_| CommitError::ReplyDisconnected)??;
        if let (Some(published), Some(snapshot)) =
            (result.published_manifest_version, snapshot_version)
        {
            result.timings.insert(
                "occ.apply.manifest_lag".to_owned(),
                published.saturating_sub(snapshot + 1) as f64,
            );
        }
        Ok(result)
    }

    pub(crate) fn apply_layerstack_changeset(
        &self,
        changes: &[LayerChange],
        snapshot_version: Option<u64>,
        decisions: Vec<PublishDecision>,
    ) -> Result<ChangesetResult, CommitError> {
        self.apply_changeset_with_decisions(changes, snapshot_version, true, decisions)
    }
}

impl Drop for CommitWriter {
    fn drop(&mut self) {
        let _ = self.commit_queue.close();
    }
}
