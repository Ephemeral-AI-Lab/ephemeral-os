use crate::service::StackObservation;
use crate::test_fixture::Fixture;
use crate::{
    process_state_test_lock, reset_process_state_for_tests, LayerChange, LayerPath, LayerStack,
};

fn publish(
    stack: &mut LayerStack,
    path: &str,
    content: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    stack.publish_layer(&[LayerChange::Write {
        path: LayerPath::parse(path)?,
        content: content.as_bytes().to_vec(),
    }])?;
    Ok(())
}

fn leased_by(obs: &StackObservation) -> Vec<usize> {
    obs.layers.iter().map(|s| s.leased_by_workspaces).collect()
}

fn booked_by_ids(obs: &StackObservation, index: usize) -> Vec<String> {
    obs.layers
        .iter()
        .take(index)
        .filter(|status| status.leased_by_workspaces > 0)
        .map(|status| status.layer.layer_id.clone())
        .collect()
}

fn ids_at(obs: &StackObservation, indices: &[usize]) -> Vec<String> {
    indices
        .iter()
        .map(|&i| obs.layers[i].layer.layer_id.clone())
        .collect()
}

#[test]
fn observe_reports_leased_and_booked_layers_over_l0_to_l4(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let _state_guard = process_state_test_lock();
    reset_process_state_for_tests();
    let fixture = Fixture::new("observe_l0_l4")?;
    let mut stack = LayerStack::open(fixture.root.clone())?;

    // Seeded base is l0; build l1..l4 and take a lease at the moment l2 and then
    // l3 are the newest layer, so the leases target {l2, l3}.
    publish(&mut stack, "f1.txt", "one")?;
    publish(&mut stack, "f2.txt", "two")?;
    let _lease_l2 = stack.acquire_snapshot("ws-l2")?;
    publish(&mut stack, "f3.txt", "three")?;
    let lease_l3 = stack.acquire_snapshot("ws-l3")?;
    publish(&mut stack, "f4.txt", "four")?;

    let obs = stack.observe()?;
    assert_eq!(obs.manifest_version, 5);
    assert_eq!(obs.active_lease_count, 2);
    assert_eq!(obs.layers.len(), 5);

    // Only l2 and l3 are some lease's newest layer.
    assert_eq!(leased_by(&obs), vec![0, 1, 1, 0, 0]);

    // booked by leased layers (the §1 rule): each base is booked by the leased
    // layers above it whose mount pulls it in.
    assert!(booked_by_ids(&obs, 0).is_empty());
    assert!(booked_by_ids(&obs, 1).is_empty());
    assert_eq!(booked_by_ids(&obs, 2), ids_at(&obs, &[1]));
    assert_eq!(booked_by_ids(&obs, 3), ids_at(&obs, &[1, 2]));
    assert_eq!(booked_by_ids(&obs, 4), ids_at(&obs, &[1, 2]));

    // Releasing the l3 lease leaves l3 (and l4) leased by 0 ws / booked by —.
    assert!(stack.release_lease(&lease_l3.lease_id)?);
    let obs = stack.observe()?;
    assert_eq!(obs.active_lease_count, 1);
    assert_eq!(leased_by(&obs), vec![0, 0, 1, 0, 0]);
    assert!(booked_by_ids(&obs, 0).is_empty());
    assert!(booked_by_ids(&obs, 1).is_empty());
    assert!(booked_by_ids(&obs, 2).is_empty());
    assert_eq!(booked_by_ids(&obs, 3), ids_at(&obs, &[2]));
    assert_eq!(booked_by_ids(&obs, 4), ids_at(&obs, &[2]));

    Ok(())
}
