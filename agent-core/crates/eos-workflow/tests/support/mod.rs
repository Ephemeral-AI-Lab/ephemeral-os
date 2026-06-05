//! Shared `#[cfg(test)]` fixtures for the crate's per-module AC tests:
//! in-memory `Store` implementations (`stores`), agent-runner doubles and the
//! workflow agent registry/builders (`runners`). The proving tests live next to
//! the code they cover (`starter::tests`, `lifecycle::tests`, etc.).

mod runners;
mod stores;

pub(crate) use runners::*;
pub(crate) use stores::*;
