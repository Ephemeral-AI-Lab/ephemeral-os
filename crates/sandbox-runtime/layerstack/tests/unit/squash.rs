use std::path::PathBuf;

use super::*;

fn layer(id: &str) -> LayerRef {
    LayerRef {
        layer_id: id.to_owned(),
        path: format!("layers/{id}"),
    }
}

fn kept_ids(entries: &[SquashPlanEntry]) -> Vec<&str> {
    entries
        .iter()
        .filter_map(|entry| match entry {
            SquashPlanEntry::Keep(layer) => Some(layer.layer_id.as_str()),
            SquashPlanEntry::Segment(_) => None,
        })
        .collect()
}

fn folded_ids(plan: &SquashPlan) -> Vec<Vec<&str>> {
    plan.checkpoint_segments()
        .iter()
        .map(|segment| {
            segment
                .layers
                .iter()
                .map(|layer| layer.layer_id.as_str())
                .collect()
        })
        .collect()
}

#[test]
fn squash_segments_around_lease_heads() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let layers: Vec<LayerRef> = (0..9).map(|index| layer(&format!("L{index}"))).collect();
    let manifest = Manifest::new(9, layers.clone(), MANIFEST_SCHEMA_VERSION)?;
    let squasher = LayerCheckpointSquasher::new(PathBuf::from("/squash-plan-only"));

    let leased = squasher
        .plan(&manifest, 5, &[layers[3].clone(), layers[6].clone()], 1)?
        .expect("plan");
    assert_eq!(leased.entries.len(), 5);
    assert_eq!(kept_ids(&leased.entries), ["L3", "L6"]);
    assert_eq!(
        folded_ids(&leased),
        vec![vec!["L0", "L1", "L2"], vec!["L4", "L5"], vec!["L7", "L8"]]
    );

    let unleased = squasher.plan(&manifest, 5, &[], 1)?.expect("plan");
    assert_eq!(unleased.entries.len(), 1);
    assert!(kept_ids(&unleased.entries).is_empty());
    assert_eq!(unleased.checkpoint_segments().len(), 1);

    let adjacent = squasher
        .plan(&manifest, 5, &[layers[4].clone(), layers[5].clone()], 1)?
        .expect("plan");
    assert_eq!(adjacent.entries.len(), 4);
    assert_eq!(kept_ids(&adjacent.entries), ["L4", "L5"]);
    assert_eq!(adjacent.checkpoint_segments().len(), 2);
    Ok(())
}
