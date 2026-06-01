//! Ephemeral workspace contract surfaces shared with deferred plugin dispatch.
//!
//! The Rust daemon owns the concrete shared-workspace runtime path directly:
//! file fast paths, shell/search overlay execution, capture, and OCC publish all
//! live in `eos-daemon`. This crate intentionally contains no runtime pipeline,
//! registry, route model, or implementation scaffolding.
//!
//! The one remaining contract is [`OccRuntimeServicesPort`]. Deferred plugin PPC
//! dispatch consumes it for WRITE_ALLOWED and self-managed plugin commits so
//! both paths must use the same daemon-owned per-root OCC writer instead of
//! inventing a second publish entry point.
#![forbid(unsafe_code)]

pub mod error;
pub mod ports;

pub use error::{EphemeralError, Result};
pub use ports::{OccRuntimeServicesPort, PublishedFile};
