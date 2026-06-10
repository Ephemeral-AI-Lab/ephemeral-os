//! Saturating-converter semantics for daemon-emitted timings/metrics.
//!
//! Referenced from `src/runtime/response_timings.rs` via `#[path]` so these
//! tests can reach the `pub(crate)` converters.

use super::{u64_to_f64_saturating, usize_to_f64_saturating};

/// Regression: a workspace / upperdir / run-dir tree larger than ~4.29 GB must
/// NOT be silently clamped to `u32::MAX` on the `*_tree_bytes` wire path. The
/// daemon converter previously capped at `u32::MAX`; it now shares
/// `eos-workspace`'s uncapped semantics.
#[test]
fn tree_bytes_above_u32_max_are_not_clamped() {
    let five_gb: u64 = 5_000_000_000;
    assert!(five_gb > u64::from(u32::MAX));
    assert!(u64_to_f64_saturating(five_gb) > f64::from(u32::MAX));
}

/// The daemon converters are exactly `eos-workspace`'s — one saturating
/// semantics across the whole timing surface (no divergent second copy).
#[test]
fn converters_match_workspace_api() {
    for value in [0_u64, 1, u64::from(u32::MAX), 5_000_000_000, u64::MAX] {
        assert_eq!(
            u64_to_f64_saturating(value),
            eos_workspace_runtime::contract::u64_to_f64_saturating(value),
        );
    }
    let big: usize = 5_000_000_000;
    assert_eq!(
        usize_to_f64_saturating(big),
        eos_workspace_runtime::contract::usize_to_f64_saturating(big),
    );
}
