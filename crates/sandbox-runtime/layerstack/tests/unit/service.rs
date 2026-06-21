use crate::test_fixture::Fixture;
use crate::{process_state_test_lock, reset_process_state_for_tests, service, LayerStack};

#[test]
fn process_state_reset_clears_lease_registry(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let _state_guard = process_state_test_lock();
    reset_process_state_for_tests();
    let fixture = Fixture::new("process_state_reset")?;
    let _snapshot = service::acquire_snapshot_with_lease(&fixture.root, "reset-test")?;
    {
        let stack = LayerStack::open(fixture.root.clone())?;
        assert_eq!(stack.active_lease_count(), 1, "lease registry has snapshot");
    }

    reset_process_state_for_tests();

    let stack = LayerStack::open(fixture.root.clone())?;
    assert_eq!(stack.active_lease_count(), 0, "lease registry was reset");
    Ok(())
}
