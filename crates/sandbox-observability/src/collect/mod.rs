//! Pure on-disk collectors. Each reads bytes from a storage path and returns a
//! plain struct; none depend on runtime implementation crates. The daemon calls
//! them and packs the results into `Sample.metrics`.

pub mod cgroup;
pub mod disk;
mod layerstack;

pub use layerstack::{sample_layerstack, LayerBytes, LayerStackBytes};
