use std::error::Error;
use std::path::PathBuf;

use sandbox_observability::ObservabilityPaths;

#[test]
fn derives_observability_database_from_daemon_socket_path() -> Result<(), Box<dyn Error>> {
    let runtime_root = PathBuf::from("/tmp/eos-daemons");
    let sandbox_id = "sandbox-1";
    let socket_path = runtime_root.join(sandbox_id).join("runtime.sock");

    let paths = ObservabilityPaths::from_socket_path(&socket_path)?;

    assert_eq!(paths.runtime_dir(), runtime_root.join(sandbox_id));
    assert_eq!(
        paths.observability_dir(),
        runtime_root.join(sandbox_id).join("observability")
    );
    assert_eq!(
        paths.database_path(),
        runtime_root
            .join(sandbox_id)
            .join("observability")
            .join("observability.sqlite")
    );

    Ok(())
}
