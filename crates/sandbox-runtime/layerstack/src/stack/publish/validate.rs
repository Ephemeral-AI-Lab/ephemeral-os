use crate::error::LayerStackError;
use crate::model::Manifest;
use crate::stack::MergedView;

use super::fingerprint::content_fingerprint;
use super::model::{PublishReject, SourceConflict};
use super::plan::PublishPlan;

pub(crate) fn validate_source_paths(
    view: &MergedView,
    active: &Manifest,
    plan: &PublishPlan,
) -> Result<(), LayerStackError> {
    for validation in plan.source_validations() {
        let actual = content_fingerprint(view, active, &validation.path)?;
        if actual != validation.expected {
            return Err(LayerStackError::PublishRejected(Box::new(
                PublishReject::source_conflict(SourceConflict {
                    path: validation.path.clone(),
                    expected: validation.expected.clone(),
                    actual,
                }),
            )));
        }
    }
    Ok(())
}
