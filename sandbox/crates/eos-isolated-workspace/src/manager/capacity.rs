use crate::error::IsolatedError;

use super::IsolatedManager;

const HOST_BUDGET_FALLBACK_BYTES: u64 = 1_u64 << 62;
const KIB_BYTES: u64 = 1_024;

impl IsolatedManager {
    pub(super) fn check_host_capacity(&self) -> Result<(), IsolatedError> {
        check_host_capacity_against_budget(
            self.handles.len(),
            self.caps.upperdir_bytes,
            host_capacity_budget_bytes(self.caps.memavail_fraction),
        )
    }
}

pub(super) fn check_host_capacity_against_budget(
    open_handles: usize,
    upperdir_bytes: u64,
    budget_bytes: u64,
) -> Result<(), IsolatedError> {
    let required_bytes = required_host_capacity_bytes(open_handles, upperdir_bytes);
    if required_bytes > budget_bytes {
        return Err(IsolatedError::HostRamPressure {
            required_bytes,
            budget_bytes,
        });
    }
    Ok(())
}

pub(super) fn required_host_capacity_bytes(open_handles: usize, upperdir_bytes: u64) -> u64 {
    u64::try_from(open_handles)
        .unwrap_or(u64::MAX)
        .saturating_add(1)
        .saturating_mul(upperdir_bytes)
}

fn host_capacity_budget_bytes(memavail_fraction: f64) -> u64 {
    std::fs::read_to_string("/proc/meminfo")
        .ok()
        .and_then(|meminfo| parse_memavailable_kib(&meminfo))
        .map_or(HOST_BUDGET_FALLBACK_BYTES, |memavailable_kib| {
            host_capacity_budget_bytes_from_memavailable_kib(memavailable_kib, memavail_fraction)
        })
}

pub(super) fn parse_memavailable_kib(meminfo: &str) -> Option<u64> {
    meminfo.lines().find_map(|line| {
        let rest = line.trim_start().strip_prefix("MemAvailable:")?;
        rest.split_whitespace().next()?.parse().ok()
    })
}

pub(super) fn host_capacity_budget_bytes_from_memavailable_kib(
    memavailable_kib: u64,
    memavail_fraction: f64,
) -> u64 {
    let memavailable_bytes = memavailable_kib.saturating_mul(KIB_BYTES);
    f64_floor_to_u64_saturating(u64_to_f64_lossy(memavailable_bytes) * memavail_fraction)
}

fn f64_floor_to_u64_saturating(value: f64) -> u64 {
    if !value.is_finite() {
        return if value.is_sign_positive() {
            u64::MAX
        } else {
            0
        };
    }
    if value <= 0.0 {
        return 0;
    }
    let floored = value.floor();
    if floored >= u64_to_f64_lossy(u64::MAX) {
        return u64::MAX;
    }
    format!("{floored:.0}").parse().unwrap_or(u64::MAX)
}

fn u64_to_f64_lossy(value: u64) -> f64 {
    const U32_FACTOR: f64 = 4_294_967_296.0;
    let high = u32::try_from(value >> 32).unwrap_or(u32::MAX);
    let low = u32::try_from(value & u64::from(u32::MAX)).unwrap_or(u32::MAX);
    f64::from(high).mul_add(U32_FACTOR, f64::from(low))
}
