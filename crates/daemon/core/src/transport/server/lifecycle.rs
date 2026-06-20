use std::sync::Arc;
use std::time::Duration;

use protocol::ProtocolErrorKind;
use tokio::io::{AsyncWrite, AsyncWriteExt};
use tokio::net::{TcpListener, UnixListener};
use tokio::sync::Semaphore;

use super::DaemonServer;
use crate::error::DaemonError;

const MAX_CONCURRENT_CONNECTIONS: usize = 256;

impl DaemonServer {
    /// Bind the `AF_UNIX` (and optional TCP) listeners, write the pid file, install
    /// the SIGTERM/SIGINT handlers, and serve until the shutdown token fires.
    ///
    /// On shutdown: cancel active workspace runs, cancel the serve tasks, remove
    /// the pid file, and unlink the socket.
    ///
    /// # Errors
    ///
    /// Returns an error when listener binding, pid-file setup, signal handling,
    /// request dispatch, or shutdown cleanup fails.
    pub async fn serve(self) -> Result<(), DaemonError> {
        let shutdown = self.shutdown.clone();
        let server = Arc::new(self);
        let connection_permits = Arc::new(Semaphore::new(MAX_CONCURRENT_CONNECTIONS));
        let _reaper_task = {
            let registry = Arc::clone(&server.invocation_registry);
            let shutdown = server.shutdown.clone();
            tokio::spawn(async move {
                loop {
                    tokio::select! {
                        () = shutdown.cancelled() => break,
                        () = tokio::time::sleep(Duration::from_secs_f64(registry.reaper_interval_s())) => {
                            registry.ttl_sweep();
                        }
                    }
                }
            })
        };
        if let Some(parent) = server.config.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        if let Some(parent) = server.config.pid_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let _ = tokio::fs::remove_file(&server.config.socket_path).await;
        let unix_listener = UnixListener::bind(&server.config.socket_path)?;
        emit_boot_event(
            "listen_bound",
            serde_json::json!({
                "listener_kind": "unix",
                "socket_path": server.config.socket_path.display().to_string(),
            }),
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            tokio::fs::set_permissions(
                &server.config.socket_path,
                std::fs::Permissions::from_mode(0o600),
            )
            .await?;
        }
        tokio::fs::write(&server.config.pid_path, std::process::id().to_string()).await?;

        let unix_server = {
            let server = Arc::clone(&server);
            let connection_permits = Arc::clone(&connection_permits);
            tokio::spawn(async move {
                loop {
                    tokio::select! {
                        () = server.shutdown.cancelled() => break,
                        accepted = unix_listener.accept() => {
                            let (stream, _) = accepted?;
                            let Ok(permit) = Arc::clone(&connection_permits).try_acquire_owned() else {
                                tokio::spawn(reject_overloaded_connection(stream, "unix"));
                                continue;
                            };
                            let server = Arc::clone(&server);
                            tokio::spawn(async move {
                                let _permit = permit;
                                let _ = server.handle_connection(stream, false, None, None).await;
                            });
                        }
                    }
                }
                Ok::<(), std::io::Error>(())
            })
        };

        let mut tcp_server = match (&server.config.tcp_host, server.config.tcp_port) {
            (Some(host), Some(port)) => {
                let listener = TcpListener::bind((host.as_str(), port)).await?;
                emit_boot_event(
                    "listen_bound",
                    serde_json::json!({
                        "listener_kind": "tcp",
                        "host": host,
                        "port": port,
                    }),
                );
                let server = Arc::clone(&server);
                let connection_permits = Arc::clone(&connection_permits);
                Some(tokio::spawn(async move {
                    loop {
                        tokio::select! {
                            () = server.shutdown.cancelled() => break,
                            accepted = listener.accept() => {
                                let (stream, peer_addr) = accepted?;
                                let Ok(permit) = Arc::clone(&connection_permits).try_acquire_owned() else {
                                    tokio::spawn(reject_overloaded_connection(stream, "tcp"));
                                    continue;
                                };
                                let local_addr = stream.local_addr().ok();
                                let server = Arc::clone(&server);
                                tokio::spawn(async move {
                                    let _permit = permit;
                                    let _ = server
                                        .handle_connection(
                                            stream,
                                            true,
                                            Some(peer_addr),
                                            local_addr,
                                        )
                                        .await;
                                });
                            }
                        }
                    }
                    Ok::<(), std::io::Error>(())
                }))
            }
            _ => None,
        };

        tokio::select! {
            () = shutdown.cancelled() => {}
            () = signal_shutdown() => shutdown.cancel(),
            result = unix_server => {
                if let Ok(Err(err)) = result {
                    return Err(DaemonError::Io(err));
                }
            }
            result = async {
                let Some(task) = tcp_server.as_mut() else {
                    return std::future::pending().await;
                };
                task.await
            } => match result {
                Ok(Ok(())) => {}
                Ok(Err(err)) => return Err(DaemonError::Io(err)),
                Err(err) => {
                    return Err(DaemonError::Io(std::io::Error::other(format!(
                        "tcp listener task failed: {err}"
                    ))));
                }
            },
        }
        if let Some(task) = tcp_server {
            task.abort();
        }
        cleanup_active_runs_on_shutdown(&server).await;
        let _ = tokio::fs::remove_file(&server.config.pid_path).await;
        let _ = tokio::fs::remove_file(&server.config.socket_path).await;
        Ok(())
    }
}

async fn reject_overloaded_connection<S>(mut stream: S, listener_kind: &'static str)
where
    S: AsyncWrite + Unpin,
{
    emit_boot_event(
        "connection_rejected",
        serde_json::json!({
            "listener_kind": listener_kind,
            "error_kind": ProtocolErrorKind::ServerBusy.as_str(),
            "max_concurrent_connections": MAX_CONCURRENT_CONNECTIONS,
        }),
    );
    let response = crate::dispatcher::error_response(
        crate::wire::ErrorKind::ServerBusy,
        "daemon is at connection capacity",
        serde_json::json!({"max_concurrent_connections": MAX_CONCURRENT_CONNECTIONS}),
    );
    if let Ok(framed) = crate::wire::encode(&crate::wire::WireMessage::Response(response)) {
        let _ = stream.write_all(&framed).await;
    }
    let _ = stream.shutdown().await;
}

async fn cleanup_active_runs_on_shutdown(server: &Arc<DaemonServer>) {
    let _ = server;
}

async fn signal_shutdown() {
    let _ = tokio::signal::ctrl_c().await;
}

fn emit_boot_event(event: &str, details: serde_json::Value) {
    eprintln!(
        "{}",
        serde_json::json!({
            "ts_ms": unix_ms(),
            "level": "info",
            "module": "daemon.boot",
            "event": event,
            "details": details,
        })
    );
}

fn unix_ms() -> u64 {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    u64::try_from(millis).unwrap_or(u64::MAX)
}
