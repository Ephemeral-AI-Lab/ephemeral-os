use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use layerstack::{service, LayerChange, LayerPath, LayerStack};

type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Debug, Clone, Copy)]
struct Snapshot {
    depth: usize,
    layer_dirs: usize,
    payload_bytes: u64,
    storage_bytes: u64,
}

#[derive(Debug, Clone, Copy)]
struct SquashTiming {
    elapsed_s: f64,
    peak_payload_bytes: u64,
    depth_after: usize,
}

#[derive(Debug, Clone, Copy)]
struct RemountCompactionTiming {
    total_s: f64,
    compact_s: f64,
    retarget_s: f64,
    cleanup_s: f64,
    peak_payload_bytes: u64,
    before_layer_count: usize,
    after_layer_count: usize,
}

#[derive(Debug, Clone, Copy)]
struct CsvRow<'a> {
    kind: &'a str,
    case: &'a str,
    layers: usize,
    file_size: usize,
    files: usize,
    leases: usize,
    depth_before: usize,
    depth_after: usize,
    layer_dirs_before: usize,
    layer_dirs_after: usize,
    payload_before: u64,
    payload_after: u64,
    storage_before: u64,
    storage_after: u64,
    peak_payload: u64,
    squash_s: f64,
    compact_s: f64,
    retarget_s: f64,
    cleanup_s: f64,
    total_maintenance_s: f64,
    publish_s: f64,
    lease_s: f64,
    read_s: f64,
}

impl<'a> CsvRow<'a> {
    fn new(
        kind: &'a str,
        case: &'a str,
        layers: usize,
        file_size: usize,
        files: usize,
        leases: usize,
        before: Snapshot,
        after: Snapshot,
    ) -> Self {
        Self {
            kind,
            case,
            layers,
            file_size,
            files,
            leases,
            depth_before: before.depth,
            depth_after: after.depth,
            layer_dirs_before: before.layer_dirs,
            layer_dirs_after: after.layer_dirs,
            payload_before: before.payload_bytes,
            payload_after: after.payload_bytes,
            storage_before: before.storage_bytes,
            storage_after: after.storage_bytes,
            peak_payload: 0,
            squash_s: 0.0,
            compact_s: 0.0,
            retarget_s: 0.0,
            cleanup_s: 0.0,
            total_maintenance_s: 0.0,
            publish_s: 0.0,
            lease_s: 0.0,
            read_s: 0.0,
        }
    }
}

fn main() -> Result {
    let base = std::env::temp_dir().join(format!(
        "layerstack-bench-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    fs::create_dir_all(&base)?;
    println!("base_dir,{}", base.display());
    println!(
        "kind,case,layers,file_size,files,leases,depth_before,depth_after,layer_dirs_before,layer_dirs_after,payload_before,payload_after,storage_before,storage_after,peak_payload,squash_s,compact_s,retarget_s,cleanup_s,total_maintenance_s,publish_s,lease_s,read_s"
    );

    retained_edit_growth(&base)?;
    lease_growth(&base)?;
    launch_normalization(&base)?;
    squash_same_file(&base)?;
    squash_many_files(&base)?;
    squash_large_file(&base)?;
    remount_compaction_same_file(&base)?;
    remount_compaction_many_files(&base)?;
    remount_compaction_large_file(&base)?;
    remount_compaction_exhaustive(&base)?;

    Ok(())
}

fn retained_edit_growth(base: &Path) -> Result {
    for layers in [1_usize, 2, 5, 10, 20, 50] {
        let root = case_root(base, "retained", layers);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_s = publish_same_file_rewrites(&mut stack, layers, 1 << 20, "blob.bin")?;
        let snap = snapshot(&mut stack, &root)?;
        let mut row = CsvRow::new(
            "retained_edits",
            "same_file_1MiB",
            layers,
            1 << 20,
            1,
            0,
            snap,
            snap,
        );
        row.publish_s = publish_s;
        print_row(row);
    }
    Ok(())
}

fn lease_growth(base: &Path) -> Result {
    let root = case_root(base, "leases", 0);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_same_file_rewrites(&mut stack, 10, 1 << 20, "blob.bin")?;
    let before = snapshot(&mut stack, &root)?;
    for leases in [0_usize, 1, 10, 50, 200] {
        let start = Instant::now();
        let mut held = Vec::with_capacity(leases);
        for i in 0..leases {
            held.push(stack.acquire_snapshot(&format!("lease-{i}"))?);
        }
        let lease_s = start.elapsed().as_secs_f64();
        let after = snapshot(&mut stack, &root)?;
        let mut row = CsvRow::new(
            "leases",
            "same_stack_10x1MiB",
            10,
            1 << 20,
            1,
            leases,
            before,
            after,
        );
        row.publish_s = publish_s;
        row.lease_s = lease_s;
        print_row(row);
        for lease in held {
            stack.release_lease(&lease.lease_id)?;
        }
    }

    for leases in [1_usize, 10, 50] {
        let root = case_root(base, "versioned_leases", leases);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_start = Instant::now();
        let mut held = Vec::with_capacity(leases);
        for i in 0..leases {
            stack.publish_layer(&[LayerChange::Write {
                path: LayerPath::parse("blob.bin")?,
                content: content(1 << 20, i as u8),
            }])?;
            held.push(stack.acquire_snapshot(&format!("versioned-lease-{i}"))?);
        }
        let publish_s = publish_start.elapsed().as_secs_f64();
        let snap = snapshot(&mut stack, &root)?;
        let mut row = CsvRow::new(
            "leases",
            "versioned_1MiB_rewrites",
            leases,
            1 << 20,
            1,
            leases,
            snap,
            snap,
        );
        row.publish_s = publish_s;
        print_row(row);
        for lease in held {
            stack.release_lease(&lease.lease_id)?;
        }
    }
    Ok(())
}

fn launch_normalization(base: &Path) -> Result {
    let root = case_root(base, "launch_normalize_no_lease", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_same_file_rewrites(&mut stack, 50, 1 << 20, "blob.bin")?;
    let before = snapshot(&mut stack, &root)?;
    let lease_start = Instant::now();
    let command_snapshot =
        service::acquire_bounded_snapshot_for_command(&root, "command-no-legacy-lease", 16)?;
    let lease_s = lease_start.elapsed().as_secs_f64();
    let after = snapshot(&mut stack, &root)?;
    assert!(
        command_snapshot.normalization.triggered,
        "50-layer launch should normalize before command lease"
    );
    assert_eq!(command_snapshot.snapshot.layer_paths.len(), 1);
    let mut row = CsvRow::new(
        "launch_normalization",
        "new_command_50x1MiB_max16",
        50,
        1 << 20,
        1,
        1,
        before,
        after,
    );
    row.publish_s = publish_s;
    row.lease_s = lease_s;
    row.compact_s = lease_s;
    row.total_maintenance_s = lease_s;
    print_row(row);
    stack.release_lease(&command_snapshot.snapshot.lease_id)?;

    let root = case_root(base, "launch_normalize_legacy_lease", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_same_file_rewrites(&mut stack, 50, 1 << 20, "blob.bin")?;
    let legacy_lease = stack.acquire_snapshot("legacy-open-session")?;
    let before = snapshot(&mut stack, &root)?;
    let lease_start = Instant::now();
    let command_snapshot =
        service::acquire_bounded_snapshot_for_command(&root, "command-with-legacy-lease", 16)?;
    let lease_s = lease_start.elapsed().as_secs_f64();
    let after = snapshot(&mut stack, &root)?;
    assert!(
        command_snapshot.normalization.triggered,
        "new command should still get a bounded generation while legacy lease pins history"
    );
    assert_eq!(command_snapshot.snapshot.layer_paths.len(), 1);
    assert!(
        after.payload_bytes >= before.payload_bytes,
        "legacy lease pins old layers, so launch normalization reports temporary storage pressure"
    );
    let mut row = CsvRow::new(
        "launch_normalization",
        "new_command_50x1MiB_max16_legacy_lease_pins_history",
        50,
        1 << 20,
        1,
        2,
        before,
        after,
    );
    row.publish_s = publish_s;
    row.lease_s = lease_s;
    row.compact_s = lease_s;
    row.total_maintenance_s = lease_s;
    row.peak_payload = after.payload_bytes;
    print_row(row);
    stack.release_lease(&command_snapshot.snapshot.lease_id)?;
    stack.release_lease(&legacy_lease.lease_id)?;
    let _ = stack.squash(1)?;

    Ok(())
}

fn squash_same_file(base: &Path) -> Result {
    for layers in [10_usize, 20, 50] {
        let root = case_root(base, "squash_same", layers);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_s = publish_same_file_rewrites(&mut stack, layers, 1 << 20, "blob.bin")?;
        let before = snapshot(&mut stack, &root)?;
        let squash = timed_squash(&mut stack, &root, 1)?;
        let after = snapshot(&mut stack, &root)?;
        let read_start = Instant::now();
        let (bytes, exists) = stack.read_bytes("blob.bin")?;
        assert!(exists);
        assert_eq!(bytes.unwrap_or_default().len(), 1 << 20);
        let read_s = read_start.elapsed().as_secs_f64();
        let mut row = CsvRow::new(
            "squash_same_file",
            "1MiB_rewrites",
            layers,
            1 << 20,
            1,
            0,
            before,
            after,
        );
        row.peak_payload = squash.peak_payload_bytes;
        row.squash_s = squash.elapsed_s;
        row.total_maintenance_s = squash.elapsed_s;
        row.publish_s = publish_s;
        row.read_s = read_s;
        print_row(row);
        assert_eq!(squash.depth_after, after.depth);
    }

    let root = case_root(base, "squash_same_with_lease", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_same_file_rewrites(&mut stack, 50, 1 << 20, "blob.bin")?;
    let lease = stack.acquire_snapshot("lease-before-squash")?;
    let before = snapshot(&mut stack, &root)?;
    let squash = timed_squash(&mut stack, &root, 2)?;
    let after_squash = snapshot(&mut stack, &root)?;
    let release_start = Instant::now();
    stack.release_lease(&lease.lease_id)?;
    let release_s = release_start.elapsed().as_secs_f64();
    let after_release = snapshot(&mut stack, &root)?;
    let mut row = CsvRow::new(
        "squash_with_lease",
        "1MiB_rewrites_lease_held",
        50,
        1 << 20,
        1,
        1,
        before,
        after_squash,
    );
    row.peak_payload = squash.peak_payload_bytes;
    row.squash_s = squash.elapsed_s;
    row.total_maintenance_s = squash.elapsed_s;
    row.publish_s = publish_s;
    row.lease_s = release_s;
    print_row(row);

    let mut row = CsvRow::new(
        "squash_with_lease_after_release",
        "1MiB_rewrites_lease_released",
        50,
        1 << 20,
        1,
        0,
        after_squash,
        after_release,
    );
    row.lease_s = release_s;
    print_row(row);

    let root = case_root(base, "defer_squash_until_release", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_same_file_rewrites(&mut stack, 50, 1 << 20, "blob.bin")?;
    let lease = stack.acquire_snapshot("lease-before-deferred-squash")?;
    let before = snapshot(&mut stack, &root)?;
    let release_start = Instant::now();
    stack.release_lease(&lease.lease_id)?;
    let release_s = release_start.elapsed().as_secs_f64();
    let after_release = snapshot(&mut stack, &root)?;
    let squash = timed_squash(&mut stack, &root, 1)?;
    let after_squash = snapshot(&mut stack, &root)?;
    let mut row = CsvRow::new(
        "defer_squash_until_release",
        "1MiB_rewrites_lease_released",
        50,
        1 << 20,
        1,
        0,
        before,
        after_squash,
    );
    row.peak_payload = squash.peak_payload_bytes;
    row.squash_s = squash.elapsed_s;
    row.total_maintenance_s = squash.elapsed_s;
    row.publish_s = publish_s;
    row.lease_s = release_s;
    print_row(row);

    let mut row = CsvRow::new(
        "defer_squash_release_only",
        "1MiB_rewrites_before_deferred_squash",
        50,
        1 << 20,
        1,
        0,
        before,
        after_release,
    );
    row.lease_s = release_s;
    print_row(row);
    Ok(())
}

fn squash_many_files(base: &Path) -> Result {
    for (files, layers) in [(1_000_usize, 10_usize), (5_000, 10)] {
        let root = case_root(base, "squash_many_files", files);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_s = publish_disjoint_files(&mut stack, files, layers, 1024)?;
        let before = snapshot(&mut stack, &root)?;
        let squash = timed_squash(&mut stack, &root, 1)?;
        let after = snapshot(&mut stack, &root)?;
        let mut row = CsvRow::new(
            "squash_many_files",
            "1KiB_files",
            layers,
            1024,
            files,
            0,
            before,
            after,
        );
        row.peak_payload = squash.peak_payload_bytes;
        row.squash_s = squash.elapsed_s;
        row.total_maintenance_s = squash.elapsed_s;
        row.publish_s = publish_s;
        print_row(row);
    }
    Ok(())
}

fn squash_large_file(base: &Path) -> Result {
    for (layers, file_size) in [(8_usize, 16_usize << 20), (4, 64_usize << 20)] {
        let root = case_root(base, "squash_large_file", file_size);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_s = publish_same_file_rewrites(&mut stack, layers, file_size, "large.bin")?;
        let before = snapshot(&mut stack, &root)?;
        let squash = timed_squash(&mut stack, &root, 1)?;
        let after = snapshot(&mut stack, &root)?;
        let mut row = CsvRow::new(
            "squash_large_file",
            "same_file_rewrites",
            layers,
            file_size,
            1,
            0,
            before,
            after,
        );
        row.peak_payload = squash.peak_payload_bytes;
        row.squash_s = squash.elapsed_s;
        row.total_maintenance_s = squash.elapsed_s;
        row.publish_s = publish_s;
        print_row(row);
    }
    Ok(())
}

fn remount_compaction_same_file(base: &Path) -> Result {
    for layers in [10_usize, 50] {
        let root = case_root(base, "remount_same_file", layers);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_s = publish_same_file_rewrites(&mut stack, layers, 1 << 20, "blob.bin")?;
        let lease = stack.acquire_snapshot("mounted-session")?;
        let before = snapshot(&mut stack, &root)?;
        let timing = timed_remount_compaction(&mut stack, &root, &lease)?;
        let after = snapshot(&mut stack, &root)?;
        let read_start = Instant::now();
        let (bytes, exists) = stack.read_bytes("blob.bin")?;
        assert!(exists);
        assert_eq!(bytes.unwrap_or_default().len(), 1 << 20);
        let read_s = read_start.elapsed().as_secs_f64();
        let mut row = CsvRow::new(
            "remount_compaction",
            "same_file_1MiB_open_lease",
            layers,
            1 << 20,
            1,
            1,
            before,
            after,
        );
        row.peak_payload = timing.peak_payload_bytes;
        row.compact_s = timing.compact_s;
        row.retarget_s = timing.retarget_s;
        row.cleanup_s = timing.cleanup_s;
        row.total_maintenance_s = timing.total_s;
        row.publish_s = publish_s;
        row.read_s = read_s;
        print_row(row);
        assert_eq!(timing.before_layer_count, layers);
        assert_eq!(timing.after_layer_count, 1);
    }
    Ok(())
}

fn remount_compaction_many_files(base: &Path) -> Result {
    for (files, layers) in [(1_000_usize, 10_usize), (5_000, 10)] {
        let root = case_root(base, "remount_many_files", files);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_s = publish_disjoint_files(&mut stack, files, layers, 1024)?;
        let lease = stack.acquire_snapshot("mounted-session")?;
        let before = snapshot(&mut stack, &root)?;
        let timing = timed_remount_compaction(&mut stack, &root, &lease)?;
        let after = snapshot(&mut stack, &root)?;
        let mut row = CsvRow::new(
            "remount_compaction",
            "many_1KiB_files_open_lease",
            layers,
            1024,
            files,
            1,
            before,
            after,
        );
        row.peak_payload = timing.peak_payload_bytes;
        row.compact_s = timing.compact_s;
        row.retarget_s = timing.retarget_s;
        row.cleanup_s = timing.cleanup_s;
        row.total_maintenance_s = timing.total_s;
        row.publish_s = publish_s;
        print_row(row);
        assert_eq!(timing.before_layer_count, layers);
        assert_eq!(timing.after_layer_count, 1);
    }
    Ok(())
}

fn remount_compaction_large_file(base: &Path) -> Result {
    for (layers, file_size) in [(8_usize, 16_usize << 20), (4, 64_usize << 20)] {
        let root = case_root(base, "remount_large_file", file_size);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_s = publish_same_file_rewrites(&mut stack, layers, file_size, "large.bin")?;
        let lease = stack.acquire_snapshot("mounted-session")?;
        let before = snapshot(&mut stack, &root)?;
        let timing = timed_remount_compaction(&mut stack, &root, &lease)?;
        let after = snapshot(&mut stack, &root)?;
        let mut row = CsvRow::new(
            "remount_compaction",
            "large_same_file_open_lease",
            layers,
            file_size,
            1,
            1,
            before,
            after,
        );
        row.peak_payload = timing.peak_payload_bytes;
        row.compact_s = timing.compact_s;
        row.retarget_s = timing.retarget_s;
        row.cleanup_s = timing.cleanup_s;
        row.total_maintenance_s = timing.total_s;
        row.publish_s = publish_s;
        print_row(row);
        assert_eq!(timing.before_layer_count, layers);
        assert_eq!(timing.after_layer_count, 1);
    }
    Ok(())
}

fn remount_compaction_exhaustive(base: &Path) -> Result {
    let root = case_root(base, "remount_multi_lease_same_file", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_same_file_rewrites(&mut stack, 50, 1 << 20, "blob.bin")?;
    let (leases, lease_s) = acquire_leases(&stack, 5, "same-snapshot")?;
    let before = snapshot(&mut stack, &root)?;
    let lease_refs = leases.iter().collect::<Vec<_>>();
    let timing = timed_remount_compaction_for_leases(&mut stack, &root, &leases[0], &lease_refs)?;
    let after = snapshot(&mut stack, &root)?;
    assert_eq!(stack.active_lease_count(), 5);
    assert_file_len(&stack, "blob.bin", 1 << 20)?;
    print_remount_row(RemountRow {
        case: "same_file_1MiB_5_open_leases",
        layers: 50,
        file_size: 1 << 20,
        files: 1,
        leases: 5,
        before,
        after,
        timing,
        publish_s,
        lease_s,
        read_s: 0.0,
    });

    let root = case_root(base, "remount_multi_lease_rotating_files", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_rotating_file_rewrites(&mut stack, 50, 5, 1 << 20)?;
    let (leases, lease_s) = acquire_leases(&stack, 5, "rotating-snapshot")?;
    let before = snapshot(&mut stack, &root)?;
    let lease_refs = leases.iter().collect::<Vec<_>>();
    let timing = timed_remount_compaction_for_leases(&mut stack, &root, &leases[0], &lease_refs)?;
    let after = snapshot(&mut stack, &root)?;
    assert_eq!(stack.active_lease_count(), 5);
    for index in 0..5 {
        assert_file_len(&stack, &format!("hot/file-{index}.bin"), 1 << 20)?;
    }
    print_remount_row(RemountRow {
        case: "rotating_5_files_1MiB_5_open_leases",
        layers: 50,
        file_size: 1 << 20,
        files: 5,
        leases: 5,
        before,
        after,
        timing,
        publish_s,
        lease_s,
        read_s: 0.0,
    });

    let root = case_root(base, "remount_hot_unique_side_files", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_hot_with_unique_side_files(&mut stack, 50, 1 << 20, 64 << 10)?;
    let (leases, lease_s) = acquire_leases(&stack, 3, "hot-unique-snapshot")?;
    let before = snapshot(&mut stack, &root)?;
    let lease_refs = leases.iter().collect::<Vec<_>>();
    let timing = timed_remount_compaction_for_leases(&mut stack, &root, &leases[0], &lease_refs)?;
    let after = snapshot(&mut stack, &root)?;
    assert_eq!(stack.active_lease_count(), 3);
    assert_file_len(&stack, "hot/blob.bin", 1 << 20)?;
    assert_file_len(&stack, "side/file-049.bin", 64 << 10)?;
    print_remount_row(RemountRow {
        case: "hot_1MiB_plus_50_unique_64KiB_files_3_open_leases",
        layers: 50,
        file_size: 1 << 20,
        files: 51,
        leases: 3,
        before,
        after,
        timing,
        publish_s,
        lease_s,
        read_s: 0.0,
    });

    let root = case_root(base, "remount_rewrite_all_hot_files", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let publish_s = publish_multi_file_rewrite_layers(&mut stack, 50, 5, 256 << 10)?;
    let (leases, lease_s) = acquire_leases(&stack, 2, "rewrite-all-snapshot")?;
    let before = snapshot(&mut stack, &root)?;
    let lease_refs = leases.iter().collect::<Vec<_>>();
    let timing = timed_remount_compaction_for_leases(&mut stack, &root, &leases[0], &lease_refs)?;
    let after = snapshot(&mut stack, &root)?;
    assert_eq!(stack.active_lease_count(), 2);
    for index in 0..5 {
        assert_file_len(&stack, &format!("rewrite/file-{index}.bin"), 256 << 10)?;
    }
    print_remount_row(RemountRow {
        case: "rewrite_5_files_256KiB_each_layer_2_open_leases",
        layers: 50,
        file_size: 256 << 10,
        files: 5,
        leases: 2,
        before,
        after,
        timing,
        publish_s,
        lease_s,
        read_s: 0.0,
    });

    let root = case_root(base, "remount_current_plus_historical_leases", 50);
    let mut stack = LayerStack::open(root.clone())?;
    let mut publish_s = 0.0;
    let mut lease_s = 0.0;
    let mut historical_leases = Vec::new();
    for layer in 0..50 {
        let publish_start = Instant::now();
        stack.publish_layer(&[LayerChange::Write {
            path: LayerPath::parse("blob.bin")?,
            content: content(1 << 20, layer as u8),
        }])?;
        publish_s += publish_start.elapsed().as_secs_f64();
        if matches!(layer, 9 | 19 | 29 | 39) {
            let lease_start = Instant::now();
            historical_leases.push(stack.acquire_snapshot(&format!("historical-{layer}"))?);
            lease_s += lease_start.elapsed().as_secs_f64();
        }
    }
    let lease_start = Instant::now();
    let current_lease = stack.acquire_snapshot("current-mounted-session")?;
    lease_s += lease_start.elapsed().as_secs_f64();
    let before = snapshot(&mut stack, &root)?;
    let timing =
        timed_remount_compaction_for_leases(&mut stack, &root, &current_lease, &[&current_lease])?;
    let after = snapshot(&mut stack, &root)?;
    assert_eq!(historical_leases.len(), 4);
    assert_eq!(stack.active_lease_count(), 5);
    assert_file_len(&stack, "blob.bin", 1 << 20)?;
    print_remount_row(RemountRow {
        case: "same_file_current_plus_4_historical_leases",
        layers: 50,
        file_size: 1 << 20,
        files: 1,
        leases: 5,
        before,
        after,
        timing,
        publish_s,
        lease_s,
        read_s: 0.0,
    });

    Ok(())
}

fn publish_same_file_rewrites(
    stack: &mut LayerStack,
    layers: usize,
    file_size: usize,
    path: &str,
) -> Result<f64> {
    let start = Instant::now();
    for i in 0..layers {
        stack.publish_layer(&[LayerChange::Write {
            path: LayerPath::parse(path)?,
            content: content(file_size, i as u8),
        }])?;
    }
    Ok(start.elapsed().as_secs_f64())
}

fn publish_rotating_file_rewrites(
    stack: &mut LayerStack,
    layers: usize,
    file_count: usize,
    file_size: usize,
) -> Result<f64> {
    let start = Instant::now();
    for layer in 0..layers {
        let index = layer % file_count;
        stack.publish_layer(&[LayerChange::Write {
            path: LayerPath::parse(&format!("hot/file-{index}.bin"))?,
            content: content(file_size, layer as u8),
        }])?;
    }
    Ok(start.elapsed().as_secs_f64())
}

fn publish_hot_with_unique_side_files(
    stack: &mut LayerStack,
    layers: usize,
    hot_size: usize,
    side_size: usize,
) -> Result<f64> {
    let start = Instant::now();
    for layer in 0..layers {
        stack.publish_layer(&[
            LayerChange::Write {
                path: LayerPath::parse("hot/blob.bin")?,
                content: content(hot_size, layer as u8),
            },
            LayerChange::Write {
                path: LayerPath::parse(&format!("side/file-{layer:03}.bin"))?,
                content: content(side_size, layer as u8),
            },
        ])?;
    }
    Ok(start.elapsed().as_secs_f64())
}

fn publish_multi_file_rewrite_layers(
    stack: &mut LayerStack,
    layers: usize,
    file_count: usize,
    file_size: usize,
) -> Result<f64> {
    let start = Instant::now();
    for layer in 0..layers {
        let mut changes = Vec::with_capacity(file_count);
        for index in 0..file_count {
            changes.push(LayerChange::Write {
                path: LayerPath::parse(&format!("rewrite/file-{index}.bin"))?,
                content: content(file_size, layer.wrapping_add(index) as u8),
            });
        }
        stack.publish_layer(&changes)?;
    }
    Ok(start.elapsed().as_secs_f64())
}

fn publish_disjoint_files(
    stack: &mut LayerStack,
    files: usize,
    layers: usize,
    file_size: usize,
) -> Result<f64> {
    let start = Instant::now();
    let per_layer = files / layers;
    for layer in 0..layers {
        let mut changes = Vec::with_capacity(per_layer);
        for offset in 0..per_layer {
            let index = layer * per_layer + offset;
            changes.push(LayerChange::Write {
                path: LayerPath::parse(&format!("tree/file-{index:05}.bin"))?,
                content: content(file_size, index as u8),
            });
        }
        stack.publish_layer(&changes)?;
    }
    Ok(start.elapsed().as_secs_f64())
}

fn timed_squash(stack: &mut LayerStack, root: &Path, max_depth: usize) -> Result<SquashTiming> {
    let stop = Arc::new(AtomicBool::new(false));
    let peak = Arc::new(AtomicU64::new(snapshot(stack, root)?.payload_bytes));
    let poller = spawn_payload_poller(root, Arc::clone(&stop), Arc::clone(&peak));

    let start = Instant::now();
    let outcome = stack.squash(max_depth)?;
    let elapsed_s = start.elapsed().as_secs_f64();
    stop.store(true, Ordering::Relaxed);
    let _ = poller.join();
    let depth_after = outcome
        .manifest
        .as_ref()
        .map_or(stack.read_active_manifest()?.depth(), |manifest| {
            manifest.depth()
        });
    Ok(SquashTiming {
        elapsed_s,
        peak_payload_bytes: peak.load(Ordering::Relaxed),
        depth_after,
    })
}

fn timed_remount_compaction(
    stack: &mut LayerStack,
    root: &Path,
    lease: &layerstack::Lease,
) -> Result<RemountCompactionTiming> {
    timed_remount_compaction_for_leases(stack, root, lease, &[lease])
}

fn timed_remount_compaction_for_leases(
    stack: &mut LayerStack,
    root: &Path,
    source_lease: &layerstack::Lease,
    retarget_leases: &[&layerstack::Lease],
) -> Result<RemountCompactionTiming> {
    let stop = Arc::new(AtomicBool::new(false));
    let peak = Arc::new(AtomicU64::new(snapshot(stack, root)?.payload_bytes));
    let poller = spawn_payload_poller(root, Arc::clone(&stop), Arc::clone(&peak));
    let layer_paths = source_lease
        .layer_paths
        .iter()
        .map(PathBuf::from)
        .collect::<Vec<_>>();

    let total_start = Instant::now();
    let compact_start = Instant::now();
    let compaction =
        service::compact_snapshot_for_remount(root, source_lease.manifest_version, &layer_paths)?;
    let compact_s = compact_start.elapsed().as_secs_f64();
    let before_layer_count = compaction.before_layer_count;
    let after_layer_count = compaction.after_layer_count;

    let retarget_start = Instant::now();
    for lease in retarget_leases {
        stack.retarget_lease_manifest(&lease.lease_id, compaction.manifest.clone())?;
    }
    let retarget_s = retarget_start.elapsed().as_secs_f64();

    let cleanup_start = Instant::now();
    let _ = stack.squash(1)?;
    let cleanup_s = cleanup_start.elapsed().as_secs_f64();
    let total_s = total_start.elapsed().as_secs_f64();

    stop.store(true, Ordering::Relaxed);
    let _ = poller.join();

    Ok(RemountCompactionTiming {
        total_s,
        compact_s,
        retarget_s,
        cleanup_s,
        peak_payload_bytes: peak.load(Ordering::Relaxed),
        before_layer_count,
        after_layer_count,
    })
}

fn spawn_payload_poller(
    root: &Path,
    stop: Arc<AtomicBool>,
    peak: Arc<AtomicU64>,
) -> std::thread::JoinHandle<()> {
    let poll_root = root.to_path_buf();
    std::thread::spawn(move || {
        while !stop.load(Ordering::Relaxed) {
            if let Ok(bytes) = payload_bytes(&poll_root.join("layers")) {
                let mut current = peak.load(Ordering::Relaxed);
                while bytes > current {
                    match peak.compare_exchange(
                        current,
                        bytes,
                        Ordering::Relaxed,
                        Ordering::Relaxed,
                    ) {
                        Ok(_) => break,
                        Err(next) => current = next,
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    })
}

fn acquire_leases(
    stack: &LayerStack,
    count: usize,
    owner_prefix: &str,
) -> Result<(Vec<layerstack::Lease>, f64)> {
    let start = Instant::now();
    let mut leases = Vec::with_capacity(count);
    for index in 0..count {
        leases.push(stack.acquire_snapshot(&format!("{owner_prefix}-{index}"))?);
    }
    Ok((leases, start.elapsed().as_secs_f64()))
}

fn assert_file_len(stack: &LayerStack, path: &str, expected_len: usize) -> Result {
    let (bytes, exists) = stack.read_bytes(path)?;
    assert!(exists, "{path} should exist");
    assert_eq!(bytes.unwrap_or_default().len(), expected_len, "{path}");
    Ok(())
}

fn snapshot(stack: &mut LayerStack, root: &Path) -> Result<Snapshot> {
    let manifest = stack.read_active_manifest()?;
    let metrics = stack.storage_metrics()?;
    Ok(Snapshot {
        depth: manifest.depth(),
        layer_dirs: metrics.layer_dirs,
        payload_bytes: payload_bytes(&root.join("layers"))?,
        storage_bytes: metrics.storage_bytes,
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

fn content(size: usize, seed: u8) -> Vec<u8> {
    let mut bytes = vec![0; size];
    for (index, byte) in bytes.iter_mut().enumerate() {
        *byte = seed.wrapping_add((index % 251) as u8);
    }
    bytes
}

fn case_root(base: &Path, label: &str, value: usize) -> PathBuf {
    base.join(format!("{label}-{value}"))
}

fn print_row(row: CsvRow<'_>) {
    println!(
        "{},{},{},{},{},{},{},{},{},{},{},{},{},{},{},{:.6},{:.6},{:.6},{:.6},{:.6},{:.6},{:.6},{:.6}",
        row.kind,
        row.case,
        row.layers,
        row.file_size,
        row.files,
        row.leases,
        row.depth_before,
        row.depth_after,
        row.layer_dirs_before,
        row.layer_dirs_after,
        row.payload_before,
        row.payload_after,
        row.storage_before,
        row.storage_after,
        row.peak_payload,
        row.squash_s,
        row.compact_s,
        row.retarget_s,
        row.cleanup_s,
        row.total_maintenance_s,
        row.publish_s,
        row.lease_s,
        row.read_s
    );
}

struct RemountRow<'a> {
    case: &'a str,
    layers: usize,
    file_size: usize,
    files: usize,
    leases: usize,
    before: Snapshot,
    after: Snapshot,
    timing: RemountCompactionTiming,
    publish_s: f64,
    lease_s: f64,
    read_s: f64,
}

fn print_remount_row(row: RemountRow<'_>) {
    let mut csv = CsvRow::new(
        "remount_compaction_exhaustive",
        row.case,
        row.layers,
        row.file_size,
        row.files,
        row.leases,
        row.before,
        row.after,
    );
    csv.peak_payload = row.timing.peak_payload_bytes;
    csv.compact_s = row.timing.compact_s;
    csv.retarget_s = row.timing.retarget_s;
    csv.cleanup_s = row.timing.cleanup_s;
    csv.total_maintenance_s = row.timing.total_s;
    csv.publish_s = row.publish_s;
    csv.lease_s = row.lease_s;
    csv.read_s = row.read_s;
    print_row(csv);
}
