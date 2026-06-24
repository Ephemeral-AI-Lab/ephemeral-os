#![forbid(unsafe_code)]

pub(crate) use time::OffsetDateTime;

#[path = "../src/config.rs"]
mod config;
#[path = "../src/contract.rs"]
#[allow(dead_code)]
mod contract;
#[path = "../src/process.rs"]
pub mod process;
#[path = "../src/pty.rs"]
mod pty;
#[path = "../src/transcript.rs"]
mod transcript;

pub use config::CommandConfig;
pub use contract::CommandError;
pub use process::{CommandProcess, CommandProcessSpec};

pub(crate) use process::*;
pub(crate) use pty::*;
pub(crate) use transcript::*;

#[path = "unit/process.rs"]
mod process_tests;
#[path = "unit/pty.rs"]
mod pty_tests;
#[path = "unit/transcript.rs"]
mod transcript_tests;
