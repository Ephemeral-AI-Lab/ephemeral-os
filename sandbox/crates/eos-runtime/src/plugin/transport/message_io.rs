//! PPC message I/O over the connected service socket.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use eos_plugin::{PluginError, PpcMessage};

use crate::PpcError;

const MAX_PPC_MESSAGE_BYTES: usize = eos_plugin::wire::MAX_PPC_MESSAGE_BYTES;

#[derive(Clone)]
pub(super) struct MessageWriter {
    stream: Arc<Mutex<UnixStream>>,
}

impl MessageWriter {
    pub(super) fn new(stream: UnixStream) -> Self {
        Self {
            stream: Arc::new(Mutex::new(stream)),
        }
    }

    pub(super) fn write_with_timeout(
        &self,
        message: &PpcMessage,
        timeout: Duration,
    ) -> Result<(), PpcError> {
        self.write_inner(message, Some(timeout))
    }

    pub(super) fn write(&self, message: &PpcMessage) -> Result<(), PpcError> {
        self.write_inner(message, None)
    }

    fn write_inner(&self, message: &PpcMessage, timeout: Option<Duration>) -> Result<(), PpcError> {
        let mut writer = self
            .stream
            .lock()
            .map_err(|_| PpcError::LockPoisoned("plugin ppc writer"))?;
        if let Some(timeout) = timeout {
            writer.set_write_timeout(Some(timeout))?;
        }
        writer.write_all(&message.encode()?)?;
        writer.flush()?;
        Ok(())
    }
}

pub(super) fn read_message(stream: &mut UnixStream) -> Result<PpcMessage, PpcError> {
    let bytes = read_message_bytes(stream)?;
    PpcMessage::decode(&bytes).map_err(PpcError::from)
}

/// Read one newline-terminated PPC message from `stream`, capped at the protocol
/// request-byte ceiling.
pub fn read_message_bytes(stream: &mut UnixStream) -> Result<Vec<u8>, PpcError> {
    let mut bytes = Vec::new();
    let mut one = [0_u8; 1];
    loop {
        let read = stream.read(&mut one)?;
        if read == 0 {
            return Err(
                PluginError::Ppc("plugin PPC stream closed before reply".to_owned()).into(),
            );
        }
        bytes.push(one[0]);
        if one[0] == b'\n' {
            return Ok(bytes);
        }
        if bytes.len() >= MAX_PPC_MESSAGE_BYTES {
            return Err(PluginError::Ppc(format!(
                "plugin PPC reply exceeds {MAX_PPC_MESSAGE_BYTES} byte limit"
            ))
            .into());
        }
    }
}
