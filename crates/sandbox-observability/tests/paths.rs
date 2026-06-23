use std::error::Error;
use std::path::PathBuf;

use sandbox_observability::ObservabilityPaths;

#[test]
fn derives_observability_database_from_daemon_socket_path() -> Result<(), Box<dyn Error>> {
    let daemon_runtime_dir = PathBuf::from("/eos/runtime/daemon");
    let socket_path = daemon_runtime_dir.join("runtime.sock");

    let paths = ObservabilityPaths::from_socket_path(&socket_path)?;

    assert_eq!(paths.daemon_runtime_dir(), daemon_runtime_dir);
    assert_eq!(
        paths.observability_dir(),
        daemon_runtime_dir.join("observability")
    );
    assert_eq!(
        paths.database_path(),
        daemon_runtime_dir
            .join("observability")
            .join("observability.sqlite")
    );

    Ok(())
}
