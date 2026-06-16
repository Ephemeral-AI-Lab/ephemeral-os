use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use layerstack::{LayerChange, LayerPath, LayerRef, LayerStack, Manifest, MANIFEST_SCHEMA_VERSION};

type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

const PAYLOAD_UNIT_BYTES: usize = 1 << 20;

#[derive(Debug, Clone)]
struct Snapshot {
    manifest: Manifest,
    payload_bytes: u64,
}

#[derive(Debug)]
struct ReclaimRow<'a> {
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
    let base = std::env::temp_dir().join(format!(
        "layerstack-gap-reclaim-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    fs::create_dir_all(&base)?;
    println!("base_dir,{}", base.display());
    println!(
        "milestone,case,layers,protected_layers,lease_count,unleased_intervals,bytes_before,bytes_after,bytes_removed,bytes_added,pinned_bytes,active_depth_before,active_depth_after,bytes_after_release,duration_s,success,notes"
    );

    let rows = [
        single_protected_l4_gap(&base)?,
        mounted_l4_prefix_gap(&base)?,
        mounted_l4_prefix_normalized_gap(&base)?,
        delete_above_protected_skip(&base)?,
        delete_above_protected_delta(&base)?,
        opaque_above_protected_delta(&base)?,
        copy_through_fully_protected_depth_guard(&base)?,
        command_admission_bounded_snapshot(&base)?,
    ];
    for row in &rows {
        print_row(row);
    }
    if rows.iter().any(|row| !row.success) {
        return Err("one or more gap reclaim benchmark rows failed".into());
    }
    Ok(())
}

fn single_protected_l4_gap(base: &Path) -> Result<ReclaimRow<'static>> {
    let root = case_root(base, "single-protected-l4");
    let mut stack = LayerStack::open(root.clone())?;
    for index in 1..=6 {
        publish_blob(&mut stack, "blob.bin", index)?;
    }
    let active = stack.read_active_manifest()?;
    let protected_l4 = active.layers[2].clone();
    let lease = stack.acquire_snapshot("protect-l4-only")?;
    stack.retarget_lease_manifest(
        &lease.lease_id,
        Manifest::new(
            active.version,
            vec![protected_l4.clone()],
            MANIFEST_SCHEMA_VERSION,
        )?,
    )?;

    let protected = vec![protected_l4];
    let before = snapshot(&stack, &root)?;
    let start = Instant::now();
    let outcome = stack.reclaim_lease_aware_view_checkpoints(2)?;
    let duration_s = start.elapsed().as_secs_f64();
    let after = snapshot(&stack, &root)?;
    let bytes_added = new_layer_payload(&root, &before.manifest, outcome.manifest.as_ref())?;
    let pinned_bytes = layer_payload_sum(&root, &protected)?;

    stack.release_lease(&lease.lease_id)?;
    stack.squash(1)?;
    let after_release = snapshot(&stack, &root)?;

    let success = outcome.view_checkpoint_count == 2
        && outcome.skipped_delta_interval_count == 0
        && outcome.removed_layer_count == 5
        && before.payload_bytes == 6 * PAYLOAD_UNIT_BYTES as u64
        && after.payload_bytes == 3 * PAYLOAD_UNIT_BYTES as u64
        && after_release.payload_bytes == PAYLOAD_UNIT_BYTES as u64;

    Ok(row_from_snapshots(
        "single_protected_l4_same_file_view_reclaim",
        before,
        after,
        after_release.payload_bytes,
        bytes_added,
        pinned_bytes,
        duration_s,
        outcome.protected_layer_count,
        outcome.planned_reclaiming_interval_count,
        success,
        "real_storage;protect_l4_only;6S_to_3S_then_1S",
        "M2",
    ))
}

fn mounted_l4_prefix_gap(base: &Path) -> Result<ReclaimRow<'static>> {
    let root = case_root(base, "mounted-l4-prefix");
    let mut stack = LayerStack::open(root.clone())?;
    for index in 1..=4 {
        publish_blob(&mut stack, "blob.bin", index)?;
    }
    let lease = stack.acquire_snapshot("mounted-l4-prefix")?;
    let protected = lease.manifest.layers.clone();
    for index in 5..=6 {
        publish_blob(&mut stack, "blob.bin", index)?;
    }

    let before = snapshot(&stack, &root)?;
    let start = Instant::now();
    let outcome = stack.reclaim_lease_aware_view_checkpoints(2)?;
    let duration_s = start.elapsed().as_secs_f64();
    let after = snapshot(&stack, &root)?;
    let bytes_added = new_layer_payload(&root, &before.manifest, outcome.manifest.as_ref())?;
    let pinned_bytes = layer_payload_sum(&root, &protected)?;

    stack.release_lease(&lease.lease_id)?;
    stack.squash(1)?;
    let after_release = snapshot(&stack, &root)?;

    let success = outcome.view_checkpoint_count == 1
        && outcome.skipped_delta_interval_count == 0
        && outcome.removed_layer_count == 2
        && before.payload_bytes == 6 * PAYLOAD_UNIT_BYTES as u64
        && after.payload_bytes == 5 * PAYLOAD_UNIT_BYTES as u64
        && after_release.payload_bytes == PAYLOAD_UNIT_BYTES as u64;

    Ok(row_from_snapshots(
        "mounted_l4_prefix_same_file_view_reclaim",
        before,
        after,
        after_release.payload_bytes,
        bytes_added,
        pinned_bytes,
        duration_s,
        outcome.protected_layer_count,
        outcome.planned_reclaiming_interval_count,
        success,
        "real_storage;mounted_l4_prefix;6S_to_5S_then_1S",
        "M2",
    ))
}

fn mounted_l4_prefix_normalized_gap(base: &Path) -> Result<ReclaimRow<'static>> {
    let root = case_root(base, "mounted-l4-prefix-normalized");
    let mut stack = LayerStack::open(root.clone())?;
    for index in 1..=4 {
        publish_blob(&mut stack, "blob.bin", index)?;
    }
    let lease = stack.acquire_snapshot("mounted-l4-prefix-normalized")?;
    for index in 5..=6 {
        publish_blob(&mut stack, "blob.bin", index)?;
    }

    let before = snapshot(&stack, &root)?;
    let start = Instant::now();
    let normalized = stack.compact_leased_parent_for_remount(&lease.lease_id, 2)?;
    let outcome = stack.reclaim_lease_aware_view_checkpoints(2)?;
    let duration_s = start.elapsed().as_secs_f64();
    let after = snapshot(&stack, &root)?;
    let bytes_added = new_layer_payload(&root, &before.manifest, outcome.manifest.as_ref())?;
    let lease_manifest = normalized.lease_manifest.as_ref().ok_or_else(|| {
        std::io::Error::other("live lease parent prefix normalization did not retarget lease")
    })?;
    let pinned_bytes = layer_payload_sum(&root, &lease_manifest.layers)?;

    stack.release_lease(&lease.lease_id)?;
    stack.squash(1)?;
    let after_release = snapshot(&stack, &root)?;

    let success = normalized.compacted_parent_layer_count == 3
        && normalized.removed_layer_count == 3
        && normalized.lease_depth_before == 4
        && normalized.lease_depth_after == 2
        && outcome.view_checkpoint_count == 1
        && outcome.skipped_delta_interval_count == 0
        && outcome.removed_layer_count == 2
        && before.payload_bytes == 6 * PAYLOAD_UNIT_BYTES as u64
        && after.payload_bytes == 3 * PAYLOAD_UNIT_BYTES as u64
        && after_release.payload_bytes == PAYLOAD_UNIT_BYTES as u64;

    Ok(row_from_snapshots(
        "mounted_l4_prefix_normalized_live_lease_reclaim",
        before,
        after,
        after_release.payload_bytes,
        bytes_added,
        pinned_bytes,
        duration_s,
        lease_manifest.layers.len(),
        outcome.planned_reclaiming_interval_count,
        success,
        "real_storage;live_lease_parent_prefix_normalized;6S_to_3S_then_1S",
        "M6",
    ))
}

fn delete_above_protected_skip(base: &Path) -> Result<ReclaimRow<'static>> {
    let root = case_root(base, "delete-above-protected-skip");
    let mut stack = LayerStack::open(root.clone())?;
    publish_blob(&mut stack, "a.txt", 1)?;
    let lease = stack.acquire_snapshot("protect-lower-file")?;
    let protected = lease.manifest.layers.clone();
    stack.publish_layer(&[LayerChange::Delete {
        path: LayerPath::parse("a.txt")?,
    }])?;

    let before = snapshot(&stack, &root)?;
    let start = Instant::now();
    let outcome = stack.reclaim_lease_aware_view_checkpoints(1)?;
    let duration_s = start.elapsed().as_secs_f64();
    let after = snapshot(&stack, &root)?;
    let bytes_added = new_layer_payload(&root, &before.manifest, outcome.manifest.as_ref())?;
    let pinned_bytes = layer_payload_sum(&root, &protected)?;

    stack.release_lease(&lease.lease_id)?;
    stack.squash(1)?;
    let after_release = snapshot(&stack, &root)?;

    let success = outcome.manifest.is_none()
        && outcome.view_checkpoint_count == 0
        && outcome.skipped_delta_interval_count == 1
        && outcome.removed_layer_count == 0
        && before.payload_bytes == after.payload_bytes
        && after_release.payload_bytes == 0;

    Ok(row_from_snapshots(
        "delete_above_protected_skipped_until_delta",
        before,
        after,
        after_release.payload_bytes,
        bytes_added,
        pinned_bytes,
        duration_s,
        outcome.protected_layer_count,
        outcome.planned_reclaiming_interval_count,
        success,
        "real_storage;boundary_marker_skipped_until_phase3",
        "M2",
    ))
}

fn delete_above_protected_delta(base: &Path) -> Result<ReclaimRow<'static>> {
    let root = case_root(base, "delete-above-protected-delta");
    let mut stack = LayerStack::open(root.clone())?;
    publish_blob(&mut stack, "a.txt", 1)?;
    let lease = stack.acquire_snapshot("protect-lower-file")?;
    let protected = lease.manifest.layers.clone();
    stack.publish_layer(&[LayerChange::Delete {
        path: LayerPath::parse("a.txt")?,
    }])?;

    let before = snapshot(&stack, &root)?;
    let start = Instant::now();
    let outcome = stack.reclaim_lease_aware_checkpoints(1)?;
    let duration_s = start.elapsed().as_secs_f64();
    let after = snapshot(&stack, &root)?;
    let bytes_added = new_layer_payload(&root, &before.manifest, outcome.manifest.as_ref())?;
    let pinned_bytes = layer_payload_sum(&root, &protected)?;

    stack.release_lease(&lease.lease_id)?;
    stack.squash(1)?;
    let after_release = snapshot(&stack, &root)?;

    let success = outcome.delta_checkpoint_count == 1
        && outcome.skipped_delta_interval_count == 0
        && outcome.removed_layer_count == 1
        && before.payload_bytes == PAYLOAD_UNIT_BYTES as u64
        && after.payload_bytes == PAYLOAD_UNIT_BYTES as u64
        && after_release.payload_bytes == 0;

    Ok(row_from_snapshots(
        "delete_above_protected_delta_checkpoint",
        before,
        after,
        after_release.payload_bytes,
        bytes_added,
        pinned_bytes,
        duration_s,
        outcome.protected_layer_count,
        outcome.planned_reclaiming_interval_count,
        success,
        "real_storage;delta_delete_preserves_hidden_lower_file",
        "M3",
    ))
}

fn opaque_above_protected_delta(base: &Path) -> Result<ReclaimRow<'static>> {
    let root = case_root(base, "opaque-above-protected-delta");
    let mut stack = LayerStack::open(root.clone())?;
    publish_blob(&mut stack, "dir/protected.txt", 1)?;
    let lease = stack.acquire_snapshot("protect-lower-dir")?;
    let protected = lease.manifest.layers.clone();
    publish_blob(&mut stack, "dir/old-unleased.txt", 2)?;
    stack.publish_layer(&[LayerChange::OpaqueDir {
        path: LayerPath::parse("dir")?,
    }])?;

    let before = snapshot(&stack, &root)?;
    let start = Instant::now();
    let outcome = stack.reclaim_lease_aware_checkpoints(2)?;
    let duration_s = start.elapsed().as_secs_f64();
    let after = snapshot(&stack, &root)?;
    let bytes_added = new_layer_payload(&root, &before.manifest, outcome.manifest.as_ref())?;
    let pinned_bytes = layer_payload_sum(&root, &protected)?;

    stack.release_lease(&lease.lease_id)?;
    stack.squash(1)?;
    let after_release = snapshot(&stack, &root)?;

    let success = outcome.delta_checkpoint_count == 1
        && outcome.skipped_delta_interval_count == 0
        && outcome.removed_layer_count == 2
        && before.payload_bytes == 2 * PAYLOAD_UNIT_BYTES as u64
        && after.payload_bytes == PAYLOAD_UNIT_BYTES as u64
        && after_release.payload_bytes == 0;

    Ok(row_from_snapshots(
        "opaque_above_protected_delta_checkpoint",
        before,
        after,
        after_release.payload_bytes,
        bytes_added,
        pinned_bytes,
        duration_s,
        outcome.protected_layer_count,
        outcome.planned_reclaiming_interval_count,
        success,
        "real_storage;delta_opaque_hides_lower_descendants",
        "M3",
    ))
}

fn copy_through_fully_protected_depth_guard(base: &Path) -> Result<ReclaimRow<'static>> {
    let root = case_root(base, "copy-through-fully-protected");
    let mut stack = LayerStack::open(root.clone())?;
    for index in 1..=6 {
        publish_blob(&mut stack, "blob.bin", index)?;
    }
    let lease = stack.acquire_snapshot("protect-full-stack")?;

    let before = snapshot(&stack, &root)?;
    let start = Instant::now();
    let outcome = stack.copy_through_active_for_depth_guard(1)?;
    let duration_s = start.elapsed().as_secs_f64();
    let after = snapshot(&stack, &root)?;

    stack.release_lease(&lease.lease_id)?;
    let after_release = snapshot(&stack, &root)?;

    let success = outcome.checkpoint_count == 1
        && outcome.removed_layer_count == 0
        && outcome.bytes_added == PAYLOAD_UNIT_BYTES as u64
        && outcome.protected_pinned_bytes == 6 * PAYLOAD_UNIT_BYTES as u64
        && before.payload_bytes == 6 * PAYLOAD_UNIT_BYTES as u64
        && after.payload_bytes == 7 * PAYLOAD_UNIT_BYTES as u64
        && after_release.payload_bytes == PAYLOAD_UNIT_BYTES as u64;

    Ok(row_from_snapshots(
        "copy_through_fully_protected_depth_guard",
        before,
        after,
        after_release.payload_bytes,
        outcome.bytes_added,
        outcome.protected_pinned_bytes,
        duration_s,
        outcome.protected_layer_count,
        0,
        success,
        "real_storage;copy_through_reports_added_and_pinned_bytes",
        "M4",
    ))
}

fn command_admission_bounded_snapshot(base: &Path) -> Result<ReclaimRow<'static>> {
    let root = case_root(base, "command-admission-bounded-snapshot");
    let mut stack = LayerStack::open(root.clone())?;
    for index in 1..=6 {
        publish_blob(&mut stack, "blob.bin", index)?;
    }
    let legacy = stack.acquire_snapshot("legacy-running-command")?;

    let before = snapshot(&stack, &root)?;
    let start = Instant::now();
    let admitted = stack.acquire_bounded_snapshot_for_command("new-command", 1)?;
    let duration_s = start.elapsed().as_secs_f64();
    let after = snapshot(&stack, &root)?;

    stack.release_lease(&admitted.lease.lease_id)?;
    stack.release_lease(&legacy.lease_id)?;
    let after_release = snapshot(&stack, &root)?;

    let success = admitted.lease.manifest.depth() == 1
        && admitted.copy_through.checkpoint_count == 1
        && admitted.copy_through.protected_pinned_bytes == 6 * PAYLOAD_UNIT_BYTES as u64
        && before.payload_bytes == 6 * PAYLOAD_UNIT_BYTES as u64
        && after.payload_bytes == 7 * PAYLOAD_UNIT_BYTES as u64
        && after_release.payload_bytes == PAYLOAD_UNIT_BYTES as u64;

    Ok(row_from_snapshots(
        "command_admission_bounded_snapshot_with_legacy_lease",
        before,
        after,
        after_release.payload_bytes,
        admitted.copy_through.bytes_added,
        admitted.copy_through.protected_pinned_bytes,
        duration_s,
        admitted.copy_through.protected_layer_count,
        0,
        success,
        "real_storage;new_command_lease_depth_1_legacy_lease_pinned",
        "M5",
    ))
}

fn row_from_snapshots(
    case: &'static str,
    before: Snapshot,
    after: Snapshot,
    bytes_after_release: u64,
    bytes_added: u64,
    pinned_bytes: u64,
    duration_s: f64,
    protected_layers: usize,
    unleased_intervals: usize,
    success: bool,
    notes: &'static str,
    milestone: &'static str,
) -> ReclaimRow<'static> {
    let bytes_removed = before
        .payload_bytes
        .saturating_add(bytes_added)
        .saturating_sub(after.payload_bytes);
    ReclaimRow {
        milestone,
        case,
        layers: before.manifest.layers.len(),
        protected_layers,
        lease_count: usize::from(protected_layers > 0),
        unleased_intervals,
        bytes_before: before.payload_bytes,
        bytes_after: after.payload_bytes,
        bytes_removed,
        bytes_added,
        pinned_bytes,
        active_depth_before: before.manifest.depth(),
        active_depth_after: after.manifest.depth(),
        bytes_after_release,
        duration_s,
        success,
        notes,
    }
}

fn publish_blob(stack: &mut LayerStack, path: &str, seed: u8) -> Result {
    stack.publish_layer(&[LayerChange::Write {
        path: LayerPath::parse(path)?,
        content: vec![seed; PAYLOAD_UNIT_BYTES],
    }])?;
    Ok(())
}

fn snapshot(stack: &LayerStack, root: &Path) -> Result<Snapshot> {
    Ok(Snapshot {
        manifest: stack.read_active_manifest()?,
        payload_bytes: payload_bytes(&root.join("layers"))?,
    })
}

fn new_layer_payload(root: &Path, before: &Manifest, after: Option<&Manifest>) -> Result<u64> {
    let before_ids = before
        .layers
        .iter()
        .map(|layer| layer.layer_id.as_str())
        .collect::<BTreeSet<_>>();
    let Some(after) = after else {
        return Ok(0);
    };
    after
        .layers
        .iter()
        .filter(|layer| !before_ids.contains(layer.layer_id.as_str()))
        .try_fold(0_u64, |total, layer| {
            Ok(total + payload_bytes(&root.join(&layer.path))?)
        })
}

fn layer_payload_sum(root: &Path, layers: &[LayerRef]) -> Result<u64> {
    layers.iter().try_fold(0_u64, |total, layer| {
        Ok(total + payload_bytes(&root.join(&layer.path))?)
    })
}

fn payload_bytes(path: &Path) -> std::io::Result<u64> {
    let mut total = 0_u64;
    if !path.exists() {
        return Ok(0);
    }
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let meta = fs::symlink_metadata(entry.path())?;
        if meta.is_dir() {
            total += payload_bytes(&entry.path())?;
        } else if meta.is_file() || meta.file_type().is_symlink() {
            total += meta.len();
        }
    }
    Ok(total)
}

fn case_root(base: &Path, label: &str) -> PathBuf {
    base.join(label)
}

fn print_row(row: &ReclaimRow<'_>) {
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
