//! The two Unix-socket listeners: the public client socket (`visibility:
//! public` only) and the operator socket beside it (`<listen>.admin`,
//! public + operator). One thread per connection: read one frame, route,
//! write one line, half-close.

use std::io::Write;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, Result};

use crate::public::Catalog;
use crate::router::{self, Engine, Surface};
use crate::wire::{error_envelope, parse_request, read_request_line, response_line};

/// The operator socket path beside the client socket.
#[must_use]
pub fn admin_socket_path(listen: &Path) -> PathBuf {
    let mut name = listen.file_name().unwrap_or_default().to_os_string();
    name.push(".admin");
    listen.with_file_name(name)
}

/// Serve both sockets forever.
///
/// # Errors
/// Returns an error if either socket cannot be bound.
pub fn serve(listen: &Path, catalog: Arc<Catalog>, engine: Arc<dyn Engine>) -> Result<()> {
    let admin = bind(&admin_socket_path(listen))?;
    {
        let catalog = Arc::clone(&catalog);
        let engine = Arc::clone(&engine);
        std::thread::spawn(move || accept_loop(&admin, Surface::Admin, catalog, engine));
    }
    let client = bind(listen)?;
    eprintln!(
        "eos-api: serving {} (admin: {})",
        listen.display(),
        admin_socket_path(listen).display()
    );
    accept_loop(&client, Surface::Client, catalog, engine);
    Ok(())
}

fn bind(path: &Path) -> Result<UnixListener> {
    if path.exists() {
        std::fs::remove_file(path)
            .with_context(|| format!("remove stale socket {}", path.display()))?;
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("create socket dir {}", parent.display()))?;
    }
    let listener = UnixListener::bind(path).with_context(|| format!("bind {}", path.display()))?;
    // Access control on this hop IS filesystem permissions.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))
            .with_context(|| format!("chmod 600 {}", path.display()))?;
    }
    Ok(listener)
}

fn accept_loop(
    listener: &UnixListener,
    surface: Surface,
    catalog: Arc<Catalog>,
    engine: Arc<dyn Engine>,
) {
    loop {
        let Ok((stream, _)) = listener.accept() else {
            continue;
        };
        // One request per connection, one thread per connection.
        let catalog = Arc::clone(&catalog);
        let engine = Arc::clone(&engine);
        std::thread::spawn(move || handle_connection(stream, surface, &catalog, &*engine));
    }
}

fn handle_connection(stream: UnixStream, surface: Surface, catalog: &Catalog, engine: &dyn Engine) {
    let _ = stream.set_read_timeout(Some(crate::wire::REQUEST_READ_TIMEOUT));
    let response = match read_request_line(&stream).and_then(|line| parse_request(&line)) {
        Ok(request) => router::handle(catalog, engine, surface, &request),
        Err(err) => error_envelope(err.kind, &err.message),
    };
    let mut stream = stream;
    let _ = stream.write_all(&response_line(&response));
    let _ = stream.flush();
    // Half-close: signal end-of-response while the peer may still be reading.
    let _ = stream.shutdown(std::net::Shutdown::Write);
}
