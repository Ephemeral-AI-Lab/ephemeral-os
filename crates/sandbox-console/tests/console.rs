#![forbid(unsafe_code)]

mod support;

#[path = "console/assets.rs"]
mod assets_tests;
#[path = "console/catalog.rs"]
mod catalog_tests;
#[path = "console/health.rs"]
mod health_tests;
#[path = "console/proxy.rs"]
mod proxy_tests;
#[path = "console/rpc.rs"]
mod rpc_tests;
