//! PPC frame I/O over the connected service socket.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use eos_plugin::{PluginError, PpcEnvelope};

use crate::error::DaemonError;

const MAX_PPC_FRAME_BYTES: usize = eos_protocol::MAX_REQUEST_BYTES;

#[derive(Clone)]
pub(super) struct FrameWriter {
    stream: Arc<Mutex<UnixStream>>,
}

impl FrameWriter {
    pub(super) fn new(stream: UnixStream) -> Self {
        Self {
            stream: Arc::new(Mutex::new(stream)),
        }
    }

    pub(super) fn write_with_timeout(
        &self,
        frame: &PpcEnvelope,
        timeout: Duration,
    ) -> Result<(), DaemonError> {
        self.write_inner(frame, Some(timeout))
    }

    pub(super) fn write(&self, frame: &PpcEnvelope) -> Result<(), DaemonError> {
        self.write_inner(frame, None)
    }

    fn write_inner(
        &self,
        frame: &PpcEnvelope,
        timeout: Option<Duration>,
    ) -> Result<(), DaemonError> {
        let mut writer = self
            .stream
            .lock()
            .map_err(|_| DaemonError::StateLockPoisoned("plugin ppc writer"))?;
        if let Some(timeout) = timeout {
            writer.set_write_timeout(Some(timeout))?;
        }
        writer.write_all(&frame.encode()?)?;
        writer.flush()?;
        Ok(())
    }
}

pub(super) fn read_envelope(stream: &mut UnixStream) -> Result<PpcEnvelope, DaemonError> {
    let bytes = read_frame(stream)?;
    PpcEnvelope::decode(&bytes).map_err(DaemonError::from)
}

pub(in crate::plugin) fn read_frame(stream: &mut UnixStream) -> Result<Vec<u8>, DaemonError> {
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
        if bytes.len() >= MAX_PPC_FRAME_BYTES {
            return Err(PluginError::Ppc(format!(
                "plugin PPC reply exceeds {MAX_PPC_FRAME_BYTES} byte limit"
            ))
            .into());
        }
    }
}
