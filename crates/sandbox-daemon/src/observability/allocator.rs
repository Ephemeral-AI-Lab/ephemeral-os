//! On-demand allocator capability for the daemon self-metrics view.
//!
//! The daemon does not select a custom global allocator. Packaged Linux
//! binaries target musl, whose public allocator extension is limited to the
//! per-pointer `malloc_usable_size`; it has no process-wide allocated, active,
//! mapped, or resident totals. `/proc` memory totals are process metrics and
//! must not be relabeled as allocator metrics. Keep this explicit unsupported
//! result until the selected allocator provides a bounded native stats API.

use sandbox_observability_telemetry::collect::process_topology::DaemonAllocatorMetrics;

pub(crate) const fn collect_current() -> DaemonAllocatorMetrics {
    DaemonAllocatorMetrics {
        supported: false,
        allocated_bytes: None,
        active_bytes: None,
        mapped_bytes: None,
        resident_bytes: None,
    }
}
