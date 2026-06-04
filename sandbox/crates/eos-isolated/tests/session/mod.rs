use super::capacity::{
    check_host_capacity_against_budget, host_capacity_budget_bytes_from_memavailable_kib,
    parse_memavailable_kib, required_host_capacity_bytes,
};
use super::support::next_handle_id;
use super::IsolatedError;

#[test]
fn parses_memavailable_from_proc_meminfo() {
    let meminfo = "MemTotal:       1024 kB\nMemAvailable:    2048 kB\n";
    assert_eq!(parse_memavailable_kib(meminfo), Some(2_048));
}

#[test]
fn host_capacity_budget_matches_python_floor() {
    assert_eq!(
        host_capacity_budget_bytes_from_memavailable_kib(1_001, 0.5),
        512_512
    );
}

#[test]
fn host_capacity_required_saturates() {
    assert_eq!(required_host_capacity_bytes(usize::MAX, u64::MAX), u64::MAX);
}

#[test]
fn host_capacity_rejects_when_required_exceeds_budget() -> Result<(), Box<dyn std::error::Error>> {
    let error = match check_host_capacity_against_budget(2, 10, 29) {
        Ok(()) => return Err("expected host RAM pressure rejection".into()),
        Err(error) => error,
    };
    let (required_bytes, budget_bytes) = match error {
        IsolatedError::HostRamPressure {
            required_bytes,
            budget_bytes,
        } => (required_bytes, budget_bytes),
        other => return Err(format!("expected host RAM pressure error, got {other}").into()),
    };
    assert_eq!(required_bytes, 30);
    assert_eq!(budget_bytes, 29);
    Ok(())
}

#[test]
fn next_handle_id_puts_counter_in_veth_name_prefix() {
    let first = next_handle_id();
    let second = next_handle_id();

    assert_eq!(first.len(), 22);
    assert_eq!(second.len(), 22);
    assert_ne!(&first[..6], &second[..6]);
}
