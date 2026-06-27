//! Pure on-disk collectors. Each reads bytes from a storage path and returns a
//! plain struct; none depend on runtime implementation crates.

mod layerstack;

pub use layerstack::{sample_layerstack, LayerBytes, LayerStackBytes};
