//! SSE transport for the milestone stream. It turns an
//! [`EventSubscription`](eos_backend_runtime::EventSubscription) into a wire
//! stream; the replay/live handoff correctness is owned by `eos-backend-runtime`.

pub mod sse;
