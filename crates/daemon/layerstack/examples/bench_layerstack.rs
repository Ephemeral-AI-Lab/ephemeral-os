use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use layerstack::{LayerChange, LayerPath, LayerStack};

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
        "kind,case,layers,file_size,files,leases,depth_before,depth_after,layer_dirs_before,layer_dirs_after,payload_before,payload_after,storage_before,storage_after,peak_payload,squash_s,publish_s,lease_s,read_s"
    );

    retained_edit_growth(&base)?;
    lease_growth(&base)?;
    squash_same_file(&base)?;
    squash_many_files(&base)?;
    squash_large_file(&base)?;

    Ok(())
}

fn retained_edit_growth(base: &Path) -> Result {
    for layers in [1_usize, 2, 5, 10, 20, 50] {
        let root = case_root(base, "retained", layers);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_s = publish_same_file_rewrites(&mut stack, layers, 1 << 20, "blob.bin")?;
        let snap = snapshot(&mut stack, &root)?;
        println!(
            "retained_edits,same_file_1MiB,{layers},1048576,1,0,{depth},{depth},{dirs},{dirs},{payload},{payload},{storage},{storage},0,0,{publish_s:.6},0,0",
            depth = snap.depth,
            dirs = snap.layer_dirs,
            payload = snap.payload_bytes,
            storage = snap.storage_bytes
        );
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
        println!(
            "leases,same_stack_10x1MiB,10,1048576,1,{leases},{bd},{ad},{bdirs},{adirs},{bp},{ap},{bs},{as_},0,0,{publish_s:.6},{lease_s:.6},0",
            bd = before.depth,
            ad = after.depth,
            bdirs = before.layer_dirs,
            adirs = after.layer_dirs,
            bp = before.payload_bytes,
            ap = after.payload_bytes,
            bs = before.storage_bytes,
            as_ = after.storage_bytes
        );
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
        println!(
            "leases,versioned_1MiB_rewrites,{leases},1048576,1,{leases},{depth},{depth},{dirs},{dirs},{payload},{payload},{storage},{storage},0,0,{publish_s:.6},0,0",
            depth = snap.depth,
            dirs = snap.layer_dirs,
            payload = snap.payload_bytes,
            storage = snap.storage_bytes
        );
        for lease in held {
            stack.release_lease(&lease.lease_id)?;
        }
    }
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
        println!(
            "squash_same_file,1MiB_rewrites,{layers},1048576,1,0,{bd},{ad},{bdirs},{adirs},{bp},{ap},{bs},{as_},{peak},{squash_s:.6},{publish_s:.6},0,{read_s:.6}",
            bd = before.depth,
            ad = after.depth,
            bdirs = before.layer_dirs,
            adirs = after.layer_dirs,
            bp = before.payload_bytes,
            ap = after.payload_bytes,
            bs = before.storage_bytes,
            as_ = after.storage_bytes,
            peak = squash.peak_payload_bytes,
            squash_s = squash.elapsed_s
        );
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
    println!(
        "squash_with_lease,1MiB_rewrites_lease_held,50,1048576,1,1,{bd},{sad},{bdirs},{sadirs},{bp},{sap},{bs},{sas},{peak},{squash_s:.6},{publish_s:.6},{release_s:.6},0",
        bd = before.depth,
        sad = after_squash.depth,
        bdirs = before.layer_dirs,
        sadirs = after_squash.layer_dirs,
        bp = before.payload_bytes,
        sap = after_squash.payload_bytes,
        bs = before.storage_bytes,
        sas = after_squash.storage_bytes,
        peak = squash.peak_payload_bytes,
        squash_s = squash.elapsed_s
    );
    println!(
        "squash_with_lease_after_release,1MiB_rewrites_lease_released,50,1048576,1,0,{sad},{rad},{sadirs},{radirs},{sap},{rap},{sas},{ras},0,0,0,{release_s:.6},0",
        sad = after_squash.depth,
        rad = after_release.depth,
        sadirs = after_squash.layer_dirs,
        radirs = after_release.layer_dirs,
        sap = after_squash.payload_bytes,
        rap = after_release.payload_bytes,
        sas = after_squash.storage_bytes,
        ras = after_release.storage_bytes
    );

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
    println!(
        "defer_squash_until_release,1MiB_rewrites_lease_released,50,1048576,1,0,{bd},{ad},{bdirs},{adirs},{bp},{ap},{bs},{as_},{peak},{squash_s:.6},{publish_s:.6},{release_s:.6},0",
        bd = before.depth,
        ad = after_squash.depth,
        bdirs = before.layer_dirs,
        adirs = after_squash.layer_dirs,
        bp = before.payload_bytes,
        ap = after_squash.payload_bytes,
        bs = before.storage_bytes,
        as_ = after_squash.storage_bytes,
        peak = squash.peak_payload_bytes,
        squash_s = squash.elapsed_s
    );
    println!(
        "defer_squash_release_only,1MiB_rewrites_before_deferred_squash,50,1048576,1,0,{bd},{ad},{bdirs},{adirs},{bp},{ap},{bs},{as_},0,0,0,{release_s:.6},0",
        bd = before.depth,
        ad = after_release.depth,
        bdirs = before.layer_dirs,
        adirs = after_release.layer_dirs,
        bp = before.payload_bytes,
        ap = after_release.payload_bytes,
        bs = before.storage_bytes,
        as_ = after_release.storage_bytes
    );
    Ok(())
}

fn squash_many_files(base: &Path) -> Result {
    for (files, layers) in [(1_000_usize, 10_usize), (5_000, 10)] {
        let root = case_root(base, "squash_many_files", files);
        let mut stack = LayerStack::open(root.clone())?;
        let publish_start = Instant::now();
        let per_layer = files / layers;
        for layer in 0..layers {
            let mut changes = Vec::with_capacity(per_layer);
            for offset in 0..per_layer {
                let index = layer * per_layer + offset;
                changes.push(LayerChange::Write {
                    path: LayerPath::parse(&format!("tree/file-{index:05}.bin"))?,
                    content: content(1024, index as u8),
                });
            }
            stack.publish_layer(&changes)?;
        }
        let publish_s = publish_start.elapsed().as_secs_f64();
        let before = snapshot(&mut stack, &root)?;
        let squash = timed_squash(&mut stack, &root, 1)?;
        let after = snapshot(&mut stack, &root)?;
        println!(
            "squash_many_files,1KiB_files,{layers},1024,{files},0,{bd},{ad},{bdirs},{adirs},{bp},{ap},{bs},{as_},{peak},{squash_s:.6},{publish_s:.6},0,0",
            bd = before.depth,
            ad = after.depth,
            bdirs = before.layer_dirs,
            adirs = after.layer_dirs,
            bp = before.payload_bytes,
            ap = after.payload_bytes,
            bs = before.storage_bytes,
            as_ = after.storage_bytes,
            peak = squash.peak_payload_bytes,
            squash_s = squash.elapsed_s
        );
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
        println!(
            "squash_large_file,same_file_rewrites,{layers},{file_size},1,0,{bd},{ad},{bdirs},{adirs},{bp},{ap},{bs},{as_},{peak},{squash_s:.6},{publish_s:.6},0,0",
            bd = before.depth,
            ad = after.depth,
            bdirs = before.layer_dirs,
            adirs = after.layer_dirs,
            bp = before.payload_bytes,
            ap = after.payload_bytes,
            bs = before.storage_bytes,
            as_ = after.storage_bytes,
            peak = squash.peak_payload_bytes,
            squash_s = squash.elapsed_s
        );
    }
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

fn timed_squash(stack: &mut LayerStack, root: &Path, max_depth: usize) -> Result<SquashTiming> {
    let stop = Arc::new(AtomicBool::new(false));
    let peak = Arc::new(AtomicU64::new(snapshot(stack, root)?.payload_bytes));
    let poll_root = root.to_path_buf();
    let poll_stop = Arc::clone(&stop);
    let poll_peak = Arc::clone(&peak);
    let poller = std::thread::spawn(move || {
        while !poll_stop.load(Ordering::Relaxed) {
            if let Ok(bytes) = payload_bytes(&poll_root.join("layers")) {
                let mut current = poll_peak.load(Ordering::Relaxed);
                while bytes > current {
                    match poll_peak.compare_exchange(
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
    });

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
