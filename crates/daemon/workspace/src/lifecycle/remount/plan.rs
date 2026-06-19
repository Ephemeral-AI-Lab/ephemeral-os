use std::path::PathBuf;

use crate::profile::IsolatedNetworkError;

use super::RemountProbe;

#[derive(Debug, Clone)]
pub(super) struct RemountPlan {
    caller_id: String,
    layer_paths: Vec<PathBuf>,
    probe: RemountProbe,
}

impl RemountPlan {
    pub(super) fn new(
        caller_id: String,
        layer_paths: Vec<PathBuf>,
        probe: RemountProbe,
    ) -> Result<Self, IsolatedNetworkError> {
        if caller_id.trim().is_empty() {
            return Err(IsolatedNetworkError::InvalidArgument(
                "caller_id is required".to_owned(),
            ));
        }
        if layer_paths.is_empty() {
            return Err(IsolatedNetworkError::InvalidArgument(
                "layer_paths must not be empty".to_owned(),
            ));
        }
        Ok(Self {
            caller_id,
            layer_paths,
            probe,
        })
    }

    #[must_use]
    pub(super) fn caller_id(&self) -> &str {
        &self.caller_id
    }

    pub(super) fn into_parts(self) -> (String, Vec<PathBuf>, RemountProbe) {
        (self.caller_id, self.layer_paths, self.probe)
    }
}
