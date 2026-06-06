//! Streaming transports for the milestone stream: [`sse`] and [`ws`]. Both turn
//! an [`EventSubscription`](eos_backend_runtime::EventSubscription) into a wire
//! stream; the replay/live handoff correctness is owned by `eos-backend-runtime`.

pub mod sse;
pub mod ws;
