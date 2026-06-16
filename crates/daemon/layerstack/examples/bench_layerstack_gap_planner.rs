use std::time::Instant;

use layerstack::{
    plan_lease_aware_gaps, LayerRef, LeaseAwareCheckpointMode, LeaseAwarePlan, Manifest,
    MANIFEST_SCHEMA_VERSION,
};

type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

const PAYLOAD_UNIT_BYTES: u64 = 1 << 20;
const ITERATIONS: usize = 10_000;

struct PlannerCase {
    name: &'static str,
    active_newest_first: Vec<String>,
    protected: Vec<String>,
    expected_intervals: usize,
    expected_reclaiming_layers: usize,
    expected_depth_after: usize,
    expected_modes: Vec<LeaseAwareCheckpointMode>,
    notes: &'static str,
}

struct PlannerRow<'a> {
    milestone: &'a str,
    case: &'a str,
    layers: usize,
    protected_layers: usize,
    lease_count: usize,
    unleased_intervals: usize,
    bytes_before: u64,
    bytes_after: u64,
    bytes_removed: u64,
    bytes_added: u64,
    pinned_bytes: u64,
    active_depth_before: usize,
    active_depth_after: usize,
    bytes_after_release: u64,
    duration_s: f64,
    success: bool,
    notes: &'a str,
}

fn main() -> Result {
    println!(
        "milestone,case,layers,protected_layers,lease_count,unleased_intervals,bytes_before,bytes_after,bytes_removed,bytes_added,pinned_bytes,active_depth_before,active_depth_after,bytes_after_release,duration_s,success,notes"
    );
    for case in cases() {
        let row = run_case(&case)?;
        print_row(&row);
        if !row.success {
            return Err(format!("gap planner case failed: {}", case.name).into());
        }
    }
    Ok(())
}

fn cases() -> Vec<PlannerCase> {
    let fully_leased = (1..=50)
        .rev()
        .map(|index| format!("P{index}"))
        .collect::<Vec<_>>();
    let mut prefix_suffix = (1..=10)
        .rev()
        .map(|index| format!("N{index}"))
        .collect::<Vec<_>>();
    prefix_suffix.extend((1..=50).rev().map(|index| format!("P{index}")));
    let protected_suffix = (1..=50)
        .rev()
        .map(|index| format!("P{index}"))
        .collect::<Vec<_>>();

    vec![
        PlannerCase {
            name: "fully_leased_50_same_file",
            active_newest_first: fully_leased.clone(),
            protected: fully_leased,
            expected_intervals: 0,
            expected_reclaiming_layers: 0,
            expected_depth_after: 50,
            expected_modes: vec![],
            notes: "planner_only;all_layers_protected;estimated_same_file_bytes",
        },
        PlannerCase {
            name: "protected_suffix_50_unleased_prefix_10",
            active_newest_first: prefix_suffix,
            protected: protected_suffix,
            expected_intervals: 1,
            expected_reclaiming_layers: 10,
            expected_depth_after: 51,
            expected_modes: vec![LeaseAwareCheckpointMode::DeltaRequired],
            notes: "planner_only;unleased_prefix_above_protected_suffix;estimated_same_file_bytes",
        },
        PlannerCase {
            name: "disjoint_historical_protected_versions",
            active_newest_first: strings(&[
                "L12", "L11", "L10", "L9", "L8", "L7", "L6", "L5", "L4", "L3", "L2", "L1",
            ]),
            protected: strings(&["L10", "L7", "L3"]),
            expected_intervals: 4,
            expected_reclaiming_layers: 9,
            expected_depth_after: 7,
            expected_modes: vec![
                LeaseAwareCheckpointMode::DeltaRequired,
                LeaseAwareCheckpointMode::DeltaRequired,
                LeaseAwareCheckpointMode::DeltaRequired,
                LeaseAwareCheckpointMode::View,
            ],
            notes: "planner_only;disjoint_historical_leases;estimated_same_file_bytes",
        },
        PlannerCase {
            name: "alternating_single_unleased_layers",
            active_newest_first: strings(&["n6", "p5", "n4", "p3", "n2", "p1"]),
            protected: strings(&["p5", "p3", "p1"]),
            expected_intervals: 0,
            expected_reclaiming_layers: 0,
            expected_depth_after: 6,
            expected_modes: vec![],
            notes: "planner_only;single_layer_gaps_kept_by_min_interval;estimated_same_file_bytes",
        },
        PlannerCase {
            name: "single_protected_l4_gap_same_file",
            active_newest_first: strings(&["n6", "n5", "l4", "n3", "n2", "n1"]),
            protected: strings(&["l4"]),
            expected_intervals: 2,
            expected_reclaiming_layers: 5,
            expected_depth_after: 3,
            expected_modes: vec![
                LeaseAwareCheckpointMode::DeltaRequired,
                LeaseAwareCheckpointMode::View,
            ],
            notes: "planner_only;target_3S_while_leased_1S_after_release;estimated_same_file_bytes",
        },
        PlannerCase {
            name: "mounted_l4_protects_lower_prefix_same_file",
            active_newest_first: strings(&["n6", "n5", "l4", "n3", "n2", "n1"]),
            protected: strings(&["l4", "n3", "n2", "n1"]),
            expected_intervals: 1,
            expected_reclaiming_layers: 2,
            expected_depth_after: 5,
            expected_modes: vec![LeaseAwareCheckpointMode::DeltaRequired],
            notes: "planner_only;current_mounted_lowerdir_prefix;estimated_same_file_bytes",
        },
        PlannerCase {
            name: "mounted_l4_after_parent_normalization_same_file",
            active_newest_first: strings(&["n6", "n5", "l4", "c_n3_n1"]),
            protected: strings(&["l4", "c_n3_n1"]),
            expected_intervals: 1,
            expected_reclaiming_layers: 2,
            expected_depth_after: 3,
            expected_modes: vec![LeaseAwareCheckpointMode::DeltaRequired],
            notes: "planner_only;live_lease_parent_prefix_normalized;estimated_same_file_bytes",
        },
    ]
}

fn run_case(case: &PlannerCase) -> Result<PlannerRow<'_>> {
    let manifest = manifest(&case.active_newest_first)?;
    let protected = case
        .protected
        .iter()
        .map(|id| layer(id))
        .collect::<Vec<_>>();

    let start = Instant::now();
    let mut plan = plan_lease_aware_gaps(&manifest, &protected, 2)?;
    for _ in 1..ITERATIONS {
        plan = plan_lease_aware_gaps(&manifest, &protected, 2)?;
    }
    let duration_s = start.elapsed().as_secs_f64() / ITERATIONS as f64;

    let modes = plan
        .reclaiming_intervals()
        .map(|interval| interval.checkpoint_mode)
        .collect::<Vec<_>>();
    let success = plan.reclaiming_interval_count == case.expected_intervals
        && plan.reclaiming_layer_count == case.expected_reclaiming_layers
        && plan.active_depth_after_reclaiming_checkpoints() == case.expected_depth_after
        && modes == case.expected_modes
        && intervals_exclude_protected_layers(&plan, &protected);

    let bytes_before = manifest.layers.len() as u64 * PAYLOAD_UNIT_BYTES;
    let bytes_removed = plan.reclaiming_layer_count as u64 * PAYLOAD_UNIT_BYTES;
    let bytes_added = plan.reclaiming_interval_count as u64 * PAYLOAD_UNIT_BYTES;
    let bytes_after = bytes_before - bytes_removed + bytes_added;

    Ok(PlannerRow {
        milestone: "M1",
        case: case.name,
        layers: manifest.layers.len(),
        protected_layers: plan.protected_layer_count,
        lease_count: usize::from(!protected.is_empty()),
        unleased_intervals: plan.reclaiming_interval_count,
        bytes_before,
        bytes_after,
        bytes_removed,
        bytes_added,
        pinned_bytes: plan.protected_layer_count as u64 * PAYLOAD_UNIT_BYTES,
        active_depth_before: manifest.depth(),
        active_depth_after: plan.active_depth_after_reclaiming_checkpoints(),
        bytes_after_release: if manifest.layers.is_empty() {
            0
        } else {
            PAYLOAD_UNIT_BYTES
        },
        duration_s,
        success,
        notes: case.notes,
    })
}

fn intervals_exclude_protected_layers(plan: &LeaseAwarePlan, protected: &[LayerRef]) -> bool {
    plan.reclaiming_intervals()
        .flat_map(|interval| interval.layers.iter())
        .all(|layer| !protected.contains(layer))
}

fn manifest(ids: &[String]) -> Result<Manifest> {
    Ok(Manifest::new(
        i64::try_from(ids.len())?,
        ids.iter().map(String::as_str).map(layer).collect(),
        MANIFEST_SCHEMA_VERSION,
    )?)
}

fn layer(id: &str) -> LayerRef {
    LayerRef {
        layer_id: id.to_owned(),
        path: format!("layers/{id}"),
    }
}

fn strings(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| (*value).to_owned()).collect()
}

fn print_row(row: &PlannerRow<'_>) {
    println!(
        "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{:.9},{},{}",
        row.milestone,
        row.case,
        row.layers,
        row.protected_layers,
        row.lease_count,
        row.unleased_intervals,
        row.bytes_before,
        row.bytes_after,
        row.bytes_removed,
        row.bytes_added,
        row.pinned_bytes,
        row.active_depth_before,
        row.active_depth_after,
        row.bytes_after_release,
        row.duration_s,
        row.success,
        row.notes
    );
}
