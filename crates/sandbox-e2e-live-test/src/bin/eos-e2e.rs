fn main() -> std::process::ExitCode {
    eprintln!(
        "eos-e2e orchestrator is not implemented in Phase 0-1. \
         Set EOS_E2E_RUN_ROOT to a directory containing run-manifest.json \
         (pointing at a real-runtime gateway socket), then run \
         `cargo test -p sandbox-e2e-live-test -- --test-threads=1`."
    );
    std::process::ExitCode::from(2)
}
