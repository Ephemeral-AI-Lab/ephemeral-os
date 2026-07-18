//! Single-threaded Linux namespace subprocess bodies for
//! `sandbox-daemon ns-holder` and `sandbox-daemon ns-runner`.
//!
//! `unshare(CLONE_NEWUSER)` and `setns()` into a user namespace require a
//! single-threaded caller. The daemon stays multithreaded and delegates those
//! syscalls to this no-tokio crate.
#![deny(unsafe_op_in_unsafe_fn)]

pub mod gate;
pub mod holder;
pub mod runner;
pub mod thp;
