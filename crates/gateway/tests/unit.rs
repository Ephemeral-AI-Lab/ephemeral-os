#![allow(dead_code, unused_imports)]

#[path = "../src/client.rs"]
mod client;
#[path = "../src/engine.rs"]
mod engine;
#[path = "../src/router.rs"]
mod router;
#[path = "../src/serve.rs"]
mod serve;
#[path = "../src/transport.rs"]
mod transport;
#[path = "../src/wire.rs"]
mod wire;

pub(crate) use client::*;
pub(crate) use serve::*;
pub(crate) use transport::*;

#[path = "unit/client.rs"]
mod client_tests;
#[path = "unit/serve.rs"]
mod serve_tests;
#[path = "unit/transport.rs"]
mod transport_tests;
