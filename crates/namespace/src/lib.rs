//! Single-threaded namespace child bodies for `eosd ns-holder` and
//! `eosd ns-runner`.
//!
//! `unshare(CLONE_NEWUSER)` and `setns()` into a user namespace require a
//! single-threaded caller. The daemon stays multithreaded and delegates those
//! syscalls to this no-tokio crate.
#![deny(unsafe_op_in_unsafe_fn)]

pub mod holder;
pub mod protocol;
pub mod runner;
