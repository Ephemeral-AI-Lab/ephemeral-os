mod error;
mod service;

use crate::operation::CliOperationFamilySpec;

pub use error::LayerStackServiceError;
pub use service::{
    LayerStackRevision, LayerStackService, PublishChangesRequest, PublishChangesResult,
    SquashLayerStackResult,
};

pub(crate) const LAYERSTACK_FAMILY: CliOperationFamilySpec = CliOperationFamilySpec {
    id: "layerstack",
    title: "Layer Stack",
    summary: "Inspect and compact runtime layer stack state.",
    description: "Inspect and compact the sandbox runtime layer stack.",
};

pub(crate) fn operation_entries() -> &'static [crate::operation::OperationEntry] {
    service::operation_entries()
}
