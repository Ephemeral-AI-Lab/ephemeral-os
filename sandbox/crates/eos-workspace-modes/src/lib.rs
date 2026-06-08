//! Workspace mode implementations over the `eos-workspace` leaf contracts.
//!
//! * [`ephemeral`] — the publish-capable fresh-overlay lifecycle (capture
//!   upperdir, classify changes, finalize through an injected publisher).
//! * [`isolated`] — a persistent, network-isolated session that captures writes
//!   for AUDIT ONLY and NEVER publishes (the `nix`/netfilter/rtnl surface).
//!
//! Neither module links `eos-occ`: the no-publish / single-writer guard keeps
//! that edge daemon-side.

pub mod ephemeral;
pub mod isolated;
