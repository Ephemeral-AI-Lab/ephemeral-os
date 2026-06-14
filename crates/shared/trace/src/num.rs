//! Numeric conversions shared across trace producers.

/// Convert a `usize` count to `f64`, saturating at `u32::MAX`.
///
/// Trace/resource numbers are emitted as JSON `f64`; clamping at `u32::MAX`
/// keeps the conversion lossless for realistic counts and deterministic for
/// pathological ones.
pub fn usize_to_f64_saturating(value: usize) -> f64 {
    u32::try_from(value).map_or_else(|_| f64::from(u32::MAX), f64::from)
}
