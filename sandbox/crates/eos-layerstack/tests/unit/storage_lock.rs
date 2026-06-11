use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::time::Duration;

use super::StorageWriterLockLease;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

static NEXT_TMP: AtomicU64 = AtomicU64::new(0);

#[test]
fn shared_guards_overlap_and_block_exclusive() -> TestResult {
    let fixture = Fixture::new("shared-overlap")?;
    let lease = StorageWriterLockLease::acquire(&fixture.root)?;
    let shared = lease.shared()?;

    let (shared_tx, shared_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let root = fixture.root.clone();
    let shared_thread = std::thread::spawn(move || -> TestResult {
        let lease = StorageWriterLockLease::acquire(&root)?;
        let _shared = lease.shared()?;
        shared_tx.send(())?;
        release_rx.recv()?;
        Ok(())
    });
    shared_rx.recv_timeout(Duration::from_secs(1))?;
    release_tx.send(())?;
    join_test_thread(shared_thread)?;

    let (exclusive_tx, exclusive_rx) = mpsc::channel();
    let root = fixture.root.clone();
    let exclusive_thread = std::thread::spawn(move || -> TestResult {
        let lease = StorageWriterLockLease::acquire(&root)?;
        let _exclusive = lease.exclusive()?;
        exclusive_tx.send(())?;
        Ok(())
    });
    assert!(
        exclusive_rx
            .recv_timeout(Duration::from_millis(50))
            .is_err(),
        "exclusive guard acquired while a shared guard was still held"
    );
    drop(shared);
    exclusive_rx.recv_timeout(Duration::from_secs(1))?;
    join_test_thread(exclusive_thread)?;
    Ok(())
}

#[test]
fn exclusive_guard_is_reentrant_and_blocks_shared() -> TestResult {
    let fixture = Fixture::new("exclusive-reentrant")?;
    let lease = StorageWriterLockLease::acquire(&fixture.root)?;
    let exclusive = lease.exclusive()?;
    let nested = lease.exclusive()?;

    let (shared_tx, shared_rx) = mpsc::channel();
    let root = fixture.root.clone();
    let shared_thread = std::thread::spawn(move || -> TestResult {
        let lease = StorageWriterLockLease::acquire(&root)?;
        let _shared = lease.shared()?;
        shared_tx.send(())?;
        Ok(())
    });
    assert!(
        shared_rx.recv_timeout(Duration::from_millis(50)).is_err(),
        "shared guard acquired while the outer exclusive guard was held"
    );
    drop(nested);
    assert!(
        shared_rx.recv_timeout(Duration::from_millis(50)).is_err(),
        "shared guard acquired while the reentrant exclusive guard was still held"
    );
    drop(exclusive);
    shared_rx.recv_timeout(Duration::from_secs(1))?;
    join_test_thread(shared_thread)?;
    Ok(())
}

fn join_test_thread(handle: std::thread::JoinHandle<TestResult>) -> TestResult {
    handle
        .join()
        .map_err(|_| std::io::Error::other("test thread panicked"))?
}

struct Fixture {
    root: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> TestResult<Self> {
        let root = std::env::temp_dir().join(format!(
            "eos-layerstack-storage-lock-{label}-{}-{}",
            std::process::id(),
            NEXT_TMP.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root)?;
        Ok(Self { root })
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
