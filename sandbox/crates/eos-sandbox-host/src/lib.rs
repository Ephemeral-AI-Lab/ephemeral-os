//! Host-side sandbox engine: owns and reaches sandbox containers.
//!
//! # Dependency law (SPEC §2)
//!
//! `eos-api → eos-sandbox-host → (std + boring externals only)`. This crate
//! must NEVER depend on a workspace-internal crate: no compiled code is shared
//! across the host/box boundary. The wire vocabulary it speaks ([`wire`]) is a
//! deliberate host-side copy of the daemon protocol; drift is caught by the
//! conformance tests against `contract/fixtures/`, not by a shared crate.
//!
//! # What lives here
//!
//! - [`docker`] — docker CLI/Engine-API plumbing (run/exec/put_archive/port).
//! - [`container`] — one container + one daemon: provision, adopt, restart.
//! - [`client`] — the box-hop wire client (loopback TCP, one request per
//!   connection).
//! - [`wire`] — the host-side protocol constants and envelope builders.
//!
//! This crate must never parse op semantics beyond catalog metadata.
#![forbid(unsafe_code)]

pub mod client;
pub mod container;
pub mod docker;
mod tar;
pub mod wire;
