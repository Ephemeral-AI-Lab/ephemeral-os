//! `eos-backend-obs` — non-blocking observability: `PersistingSink` (`AuditSink`
//! with a bounded queue and async drainer), the daemon audit ingestor with
//! `boot_epoch_id` cursor handling, and the stats queries.
//!
//! Scaffolded in Phase 1 (workspace shape); the sink/ingestor/stats land in
//! Phase 6.
