use std::io::Write;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use anyhow::{Context, Result};

use crate::engine::Engine;
use crate::router::{handle, Surface};
use crate::wire::{
    error_response, parse_request, read_request_line, response_line, server_busy_response,
    REQUEST_READ_TIMEOUT,
};

pub(crate) const MAX_CONCURRENT_CONNECTIONS: usize = 256;

pub(crate) struct ConnectionLimiter {
    active: AtomicUsize,
}

pub(crate) struct ConnectionPermit {
    limiter: Arc<ConnectionLimiter>,
}

impl ConnectionLimiter {
    pub(crate) fn new() -> Self {
        Self {
            active: AtomicUsize::new(0),
        }
    }

    pub(crate) fn try_acquire(self: &Arc<Self>) -> Option<ConnectionPermit> {
        let mut active = self.active.load(Ordering::Acquire);
        loop {
            if active >= MAX_CONCURRENT_CONNECTIONS {
                return None;
            }
            match self.active.compare_exchange_weak(
                active,
                active + 1,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => {
                    return Some(ConnectionPermit {
                        limiter: Arc::clone(self),
                    });
                }
                Err(next) => active = next,
            }
        }
    }
}

impl Drop for ConnectionPermit {
    fn drop(&mut self) {
        self.limiter.active.fetch_sub(1, Ordering::AcqRel);
    }
}

pub(crate) fn operator_socket_path(listen: &Path) -> PathBuf {
    let mut name = listen.file_name().unwrap_or_default().to_os_string();
    name.push(".operator");
    listen.with_file_name(name)
}

pub(crate) fn serve(listen: &Path, engine: Arc<dyn Engine>) -> Result<()> {
    let operator_path = operator_socket_path(listen);
    let operator = bind(&operator_path)?;
    let connection_limiter = Arc::new(ConnectionLimiter::new());
    {
        let engine = Arc::clone(&engine);
        let socket_path: Arc<str> = Arc::from(operator_path.to_string_lossy().as_ref());
        let connection_limiter = Arc::clone(&connection_limiter);
        std::thread::spawn(move || {
            accept_loop(
                &operator,
                Surface::Operator,
                &socket_path,
                engine,
                connection_limiter,
            );
        });
    }
    let client = bind(listen)?;
    eprintln!(
        "ephai-sandbox-gateway: serving {} (operator: {})",
        listen.display(),
        operator_path.display()
    );
    let socket_path: Arc<str> = Arc::from(listen.to_string_lossy().as_ref());
    accept_loop(
        &client,
        Surface::Client,
        &socket_path,
        engine,
        connection_limiter,
    );
    Ok(())
}

fn bind(path: &Path) -> Result<UnixListener> {
    if path.exists() {
        std::fs::remove_file(path)
            .with_context(|| format!("remove stale socket {}", path.display()))?;
    }
    if let Some(parent) = path.parent() {
        let parent_existed = parent.exists();
        std::fs::create_dir_all(parent)
            .with_context(|| format!("create socket dir {}", parent.display()))?;
        #[cfg(unix)]
        if !parent_existed {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700))
                .with_context(|| format!("chmod 700 {}", parent.display()))?;
        }
    }
    let listener = UnixListener::bind(path).with_context(|| format!("bind {}", path.display()))?;
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
    socket_path: &Arc<str>,
    engine: Arc<dyn Engine>,
    connection_limiter: Arc<ConnectionLimiter>,
) {
    loop {
        let Ok((stream, _)) = listener.accept() else {
            continue;
        };
        let Some(permit) = connection_limiter.try_acquire() else {
            write_overload_response(stream);
            continue;
        };
        let engine = Arc::clone(&engine);
        let socket_path = Arc::clone(socket_path);
        std::thread::spawn(move || {
            let _permit = permit;
            handle_connection(stream, surface, &socket_path, &*engine);
        });
    }
}

fn write_overload_response(mut stream: UnixStream) {
    let line = response_line(&server_busy_response(MAX_CONCURRENT_CONNECTIONS));
    if stream.write_all(&line).is_ok() {
        let _ = stream.flush();
    }
    let _ = stream.shutdown(std::net::Shutdown::Write);
}

pub(crate) fn handle_connection(
    stream: UnixStream,
    surface: Surface,
    _socket_path: &str,
    engine: &dyn Engine,
) {
    let _ = stream.set_read_timeout(Some(REQUEST_READ_TIMEOUT));
    let parsed = read_request_line(&stream).and_then(|line| parse_request(&line));
    let response = match parsed {
        Ok(request) => handle(engine, surface, &request),
        Err(err) => error_response(err.kind, &err.message),
    };
    let mut stream = stream;
    let line = response_line(&response);
    let write_result = stream.write_all(&line);
    if write_result.is_ok() {
        let _ = stream.flush();
    }
    let _ = stream.shutdown(std::net::Shutdown::Write);
}
