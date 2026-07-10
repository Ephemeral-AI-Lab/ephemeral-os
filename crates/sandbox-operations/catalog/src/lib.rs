#![forbid(unsafe_code)]

pub mod internal;
pub mod routed;
pub mod routes;

#[cfg(feature = "manager")]
pub mod manager;
#[cfg(feature = "observability")]
pub mod observability;
#[cfg(feature = "runtime")]
pub mod runtime;
