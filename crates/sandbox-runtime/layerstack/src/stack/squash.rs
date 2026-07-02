//! LayerStack squash: fold published layers into equivalent flattened layers.
//!
//! [`flatten`] is the pure build primitive: it folds a block's layer
//! directories into one staging tree with merged-view-equivalent content.

#[allow(
    dead_code,
    reason = "dead only in the lib target until the phase-3 squash transaction consumes it"
)]
pub(crate) mod flatten;
