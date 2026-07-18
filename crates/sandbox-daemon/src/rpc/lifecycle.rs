use std::os::unix::fs::PermissionsExt as _;
use std::sync::Arc;

use tokio::io::{AsyncWrite, AsyncWriteExt};
use tokio::net::{TcpListener, UnixListener};
use tokio::sync::Semaphore;
use tokio_util::task::TaskTracker;

use super::SandboxDaemonServer;
use crate::rpc::error::SandboxDaemonError;

impl SandboxDaemonServer {
    /// Bind the `AF_UNIX` (and optional TCP) listeners, write the pid file, install
    /// the Ctrl-C handler, and serve until the shutdown token fires.
    ///
    /// On shutdown: cancel the serve tasks, remove the pid file, and unlink the
    /// socket.
    ///
    /// # Errors
    ///
    /// Returns an error when listener binding, pid-file setup, signal handling,
    /// request dispatch, or shutdown cleanup fails.
    pub async fn serve(self) -> Result<(), SandboxDaemonError> {
        let shutdown = self.shutdown.clone();
        let server = Arc::new(self);
        let connection_permits = Arc::new(Semaphore::new(server.config.max_concurrent_connections));
        let connection_tasks = TaskTracker::new();
        if let Some(parent) = server.config.socket_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        if let Some(parent) = server.config.pid_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let _ = tokio::fs::remove_file(&server.config.socket_path).await;
        let unix_listener = UnixListener::bind(&server.config.socket_path)?;
        tokio::fs::set_permissions(
            &server.config.socket_path,
            std::fs::Permissions::from_mode(0o600),
        )
        .await?;
        tokio::fs::write(&server.config.pid_path, std::process::id().to_string()).await?;

        let mut unix_server = {
            let server = Arc::clone(&server);
            let connection_permits = Arc::clone(&connection_permits);
            let connection_tasks = connection_tasks.clone();
            tokio::spawn(async move {
                loop {
                    tokio::select! {
                        () = server.shutdown.cancelled() => break,
                        accepted = unix_listener.accept() => {
                            let (stream, _) = accepted?;
                            let Ok(permit) = Arc::clone(&connection_permits).try_acquire_owned() else {
                                connection_tasks.spawn(reject_overloaded_connection(
                                    stream,
                                    server.config.max_concurrent_connections,
                                ));
                                continue;
                            };
                            let server = Arc::clone(&server);
                            connection_tasks.spawn(async move {
                                let _permit = permit;
                                let _ = server.handle_connection(stream, false, None, None).await;
                            });
                        }
                    }
                }
                Ok::<(), std::io::Error>(())
            })
        };

        let mut http_server = match server.config.http_bind() {
            Some((host, port)) => {
                let listener = TcpListener::bind((host, port)).await?;
                Some(crate::http::spawn(
                    listener,
                    server.config.clone(),
                    Arc::clone(&server.operations),
                    server.observer(),
                    server.shutdown.clone(),
                ))
            }
            None => None,
        };

        let mut tcp_server = match (&server.config.tcp_host, server.config.tcp_port) {
            (Some(host), Some(port)) => {
                let listener = TcpListener::bind((host.as_str(), port)).await?;
                let server = Arc::clone(&server);
                let connection_permits = Arc::clone(&connection_permits);
                let connection_tasks = connection_tasks.clone();
                Some(tokio::spawn(async move {
                    loop {
                        tokio::select! {
                            () = server.shutdown.cancelled() => break,
                            accepted = listener.accept() => {
                                let (stream, peer_addr) = accepted?;
                                let Ok(permit) = Arc::clone(&connection_permits).try_acquire_owned() else {
                                    connection_tasks.spawn(reject_overloaded_connection(
                                        stream,
                                        server.config.max_concurrent_connections,
                                    ));
                                    continue;
                                };
                                let local_addr = stream.local_addr().ok();
                                let server = Arc::clone(&server);
                                connection_tasks.spawn(async move {
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
            result = &mut unix_server => {
                match result {
                    Ok(Ok(())) => {}
                    Ok(Err(err)) => return Err(SandboxDaemonError::Io(err)),
                    Err(err) => {
                        return Err(SandboxDaemonError::Io(std::io::Error::other(format!(
                            "unix listener task failed: {err}"
                        ))));
                    }
                }
            }
            result = async {
                let Some(task) = tcp_server.as_mut() else {
                    return std::future::pending().await;
                };
                task.await
            } => match result {
                Ok(Ok(())) => {}
                Ok(Err(err)) => return Err(SandboxDaemonError::Io(err)),
                Err(err) => {
                    return Err(SandboxDaemonError::Io(std::io::Error::other(format!(
                        "tcp listener task failed: {err}"
                    ))));
                }
            },
        }
        shutdown.cancel();
        if !unix_server.is_finished() {
            unix_server.abort();
            let _ = unix_server.await;
        }
        if let Some(task) = tcp_server.as_mut() {
            if !task.is_finished() {
                task.abort();
                let _ = task.await;
            }
        }
        drop(tcp_server);
        if let Some(task) = http_server.as_mut() {
            let _ = task.await;
        }
        drop(http_server);
        // All listener tasks have stopped accepting before the tracked
        // request/connection tasks are allowed to drain.
        drain_connection_tasks(&connection_tasks).await;
        let _ = tokio::fs::remove_file(&server.config.pid_path).await;
        let _ = tokio::fs::remove_file(&server.config.socket_path).await;
        Ok(())
    }
}

async fn reject_overloaded_connection<S>(mut stream: S, max_concurrent_connections: usize)
where
    S: AsyncWrite + Unpin,
{
    let response = super::error_response(
        "server_busy",
        "daemon is at connection capacity",
        serde_json::json!({"max_concurrent_connections": max_concurrent_connections}),
    );
    let mut framed = serde_json::to_vec(&response).expect("daemon overload response serializes");
    framed.push(b'\n');
    let _ = stream.write_all(&framed).await;
    let _ = stream.shutdown().await;
}

pub(crate) async fn drain_connection_tasks(connection_tasks: &TaskTracker) {
    connection_tasks.close();
    connection_tasks.wait().await;
}

async fn signal_shutdown() {
    let _ = tokio::signal::ctrl_c().await;
}
