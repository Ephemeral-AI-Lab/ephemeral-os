pub mod error {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/error.rs"));
}

pub mod id {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/id.rs"));
}

pub mod promise {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/promise.rs"));
}

pub mod pty {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pty.rs"));

    const _TERMINATE_PROCESS_GROUP_REF: fn(i32) = terminate_process_group;
}

pub mod execution {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/execution.rs"));
}

pub use execution::{ExecutionHandle, InteractiveExecution};
pub use id::NamespaceExecutionId;
