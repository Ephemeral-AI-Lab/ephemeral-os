//! `eos-api admin <op>` — the operator CLI. Connects to the operator socket
//! beside the client socket (never the client socket) and performs one op.

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;

use anyhow::{bail, Context, Result};
use serde_json::{json, Value};

use crate::server::admin_socket_path;

/// Parsed `admin` subcommand arguments.
pub struct AdminArgs {
    /// The op to invoke (canonical name or alias).
    pub op: String,
    /// Client socket path (the operator socket is derived from it).
    pub listen: std::path::PathBuf,
    /// Optional sandbox id for daemon-bound/host per-sandbox ops.
    pub sandbox_id: Option<String>,
    /// Op args as a JSON object.
    pub args: Value,
}

impl AdminArgs {
    /// Parse `admin <op> [--listen <socket>] [--sandbox <id>] [--args <json>]`.
    ///
    /// # Errors
    /// Returns an error on unknown flags or malformed `--args` JSON.
    pub fn parse(mut argv: std::env::Args) -> Result<Self> {
        let Some(op) = argv.next() else {
            bail!("usage: eos-api admin <op> [--listen <socket>] [--sandbox <id>] [--args <json>]");
        };
        let mut listen = default_listen();
        let mut sandbox_id = None;
        let mut args = json!({});
        while let Some(flag) = argv.next() {
            match flag.as_str() {
                "--listen" => {
                    listen = argv.next().context("--listen requires a value")?.into();
                }
                "--sandbox" => {
                    sandbox_id = Some(argv.next().context("--sandbox requires a value")?);
                }
                "--args" => {
                    let raw = argv.next().context("--args requires a value")?;
                    args = serde_json::from_str(&raw).context("--args must be JSON")?;
                    if !args.is_object() {
                        bail!("--args must be a JSON object");
                    }
                }
                other => bail!("unknown admin flag {other:?}"),
            }
        }
        Ok(Self {
            op,
            listen,
            sandbox_id,
            args,
        })
    }
}

fn default_listen() -> std::path::PathBuf {
    std::path::PathBuf::from("/tmp/eos-api.sock")
}

/// Run one admin op and print the response line to stdout.
///
/// # Errors
/// Returns an error on socket/transport failure (an error ENVELOPE is still
/// a successful round trip and is printed).
pub fn run(args: &AdminArgs) -> Result<()> {
    let mut envelope = serde_json::Map::new();
    envelope.insert("op".to_owned(), json!(args.op));
    if let Some(sandbox_id) = &args.sandbox_id {
        envelope.insert("sandbox_id".to_owned(), json!(sandbox_id));
    }
    envelope.insert("invocation_id".to_owned(), json!(admin_invocation_id()));
    envelope.insert("args".to_owned(), args.args.clone());
    let response = round_trip(&admin_socket_path(&args.listen), &Value::Object(envelope))?;
    println!("{response}");
    Ok(())
}

fn round_trip(socket: &Path, envelope: &Value) -> Result<Value> {
    let mut stream = UnixStream::connect(socket)
        .with_context(|| format!("connect operator socket {}", socket.display()))?;
    let mut line = serde_json::to_vec(envelope)?;
    line.push(b'\n');
    stream.write_all(&line)?;
    stream.flush()?;
    let mut reader = BufReader::new(stream);
    let mut response = String::new();
    reader.read_line(&mut response)?;
    serde_json::from_str(response.trim_end())
        .with_context(|| format!("decode response {response:?}"))
}

fn admin_invocation_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    format!("admin-{}-{nanos:x}", std::process::id())
}
