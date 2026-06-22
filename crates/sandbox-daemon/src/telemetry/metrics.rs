use std::sync::Arc;
use std::time::Duration;

use opentelemetry::metrics::{Counter, Gauge, Histogram, Meter, MeterProvider as _};
use opentelemetry::KeyValue;
use opentelemetry_otlp::{Protocol, WithExportConfig};
use opentelemetry_sdk::metrics::{PeriodicReader, SdkMeterProvider};
use sandbox_runtime::{
    CgroupMonitorSample, CgroupMonitorTargetKind, CgroupReadErrorKind, CommandCancellationReason,
    PublishRejectionReason, RemountFailureReason, RuntimeMetricStatus, RuntimeMetricsRecorder,
    RuntimeMetricsRecorderHandle, RuntimeOperationName, WorkspacePhase,
};

use super::{otlp_resource, TelemetryInstallError, TelemetryMetricsConfig};

pub(crate) fn init_otlp_metrics(
    service_name: &str,
    endpoint: &str,
    timeout: Duration,
    sandbox_id: &str,
    config: &TelemetryMetricsConfig,
) -> Result<(SdkMeterProvider, RuntimeMetricsRecorderHandle), TelemetryInstallError> {
    let exporter = opentelemetry_otlp::MetricExporter::builder()
        .with_http()
        .with_endpoint(endpoint.to_owned())
        .with_timeout(timeout)
        .with_protocol(Protocol::HttpBinary)
        .build()
        .map_err(|err| TelemetryInstallError::MetricsExporterBuild(err.to_string()))?;
    let reader = PeriodicReader::builder(exporter)
        .with_interval(Duration::from_millis(config.export_interval_ms))
        .build();
    let provider = SdkMeterProvider::builder()
        .with_resource(otlp_resource(service_name, sandbox_id))
        .with_reader(reader)
        .build();
    let recorder = Arc::new(OpenTelemetryRuntimeMetricsRecorder::new(
        provider.meter("sandbox-daemon"),
        config.cgroup_samples_enabled,
    ));
    Ok((provider, recorder))
}

struct OpenTelemetryRuntimeMetricsRecorder {
    runtime_latency_ms: Histogram<f64>,
    workspace_phase_latency_ms: Histogram<f64>,
    publish_rejections: Counter<u64>,
    remount_failures: Counter<u64>,
    command_cancellations: Counter<u64>,
    cgroup_read_errors: Counter<u64>,
    cgroup_cpu_usage_usec: Gauge<f64>,
    cgroup_cpu_delta_usage_usec: Gauge<f64>,
    cgroup_cpu_percent: Gauge<f64>,
    cgroup_memory_current_bytes: Gauge<f64>,
    cgroup_memory_peak_bytes: Gauge<f64>,
    cgroup_pids_current: Gauge<f64>,
    cgroup_pids_peak: Gauge<f64>,
    cgroup_pids_sampled_count: Gauge<f64>,
    cgroup_pressure_some_avg10: Gauge<f64>,
    cgroup_pressure_some_avg60: Gauge<f64>,
    cgroup_pressure_some_avg300: Gauge<f64>,
    cgroup_pressure_some_total_usec: Gauge<f64>,
    cgroup_pressure_full_avg10: Gauge<f64>,
    cgroup_pressure_full_avg60: Gauge<f64>,
    cgroup_pressure_full_avg300: Gauge<f64>,
    cgroup_pressure_full_total_usec: Gauge<f64>,
    cgroup_disk_upperdir_bytes: Gauge<f64>,
    cgroup_disk_upperdir_files: Gauge<f64>,
    cgroup_disk_upperdir_dirs: Gauge<f64>,
    cgroup_disk_upperdir_symlinks: Gauge<f64>,
    cgroup_disk_upperdir_read_errors: Gauge<f64>,
    cgroup_disk_upperdir_scan_truncated: Gauge<f64>,
    cgroup_samples_enabled: bool,
}

impl OpenTelemetryRuntimeMetricsRecorder {
    fn new(meter: Meter, cgroup_samples_enabled: bool) -> Self {
        Self {
            runtime_latency_ms: meter
                .f64_histogram("sandbox_runtime_operation_latency_ms")
                .with_unit("ms")
                .build(),
            workspace_phase_latency_ms: meter
                .f64_histogram("sandbox_workspace_phase_latency_ms")
                .with_unit("ms")
                .build(),
            publish_rejections: meter
                .u64_counter("sandbox_publish_rejections_total")
                .build(),
            remount_failures: meter.u64_counter("sandbox_remount_failures_total").build(),
            command_cancellations: meter
                .u64_counter("sandbox_command_cancellations_total")
                .build(),
            cgroup_read_errors: meter
                .u64_counter("sandbox_cgroup_read_errors_total")
                .build(),
            cgroup_cpu_usage_usec: meter
                .f64_gauge("sandbox_cgroup_cpu_usage_usec")
                .with_unit("us")
                .build(),
            cgroup_cpu_delta_usage_usec: meter
                .f64_gauge("sandbox_cgroup_cpu_delta_usage_usec")
                .with_unit("us")
                .build(),
            cgroup_cpu_percent: meter
                .f64_gauge("sandbox_cgroup_cpu_percent")
                .with_unit("%")
                .build(),
            cgroup_memory_current_bytes: meter
                .f64_gauge("sandbox_cgroup_memory_current_bytes")
                .with_unit("By")
                .build(),
            cgroup_memory_peak_bytes: meter
                .f64_gauge("sandbox_cgroup_memory_peak_bytes")
                .with_unit("By")
                .build(),
            cgroup_pids_current: meter.f64_gauge("sandbox_cgroup_pids_current").build(),
            cgroup_pids_peak: meter.f64_gauge("sandbox_cgroup_pids_peak").build(),
            cgroup_pids_sampled_count: meter.f64_gauge("sandbox_cgroup_pids_sampled_count").build(),
            cgroup_pressure_some_avg10: meter
                .f64_gauge("sandbox_cgroup_pressure_some_avg10")
                .build(),
            cgroup_pressure_some_avg60: meter
                .f64_gauge("sandbox_cgroup_pressure_some_avg60")
                .build(),
            cgroup_pressure_some_avg300: meter
                .f64_gauge("sandbox_cgroup_pressure_some_avg300")
                .build(),
            cgroup_pressure_some_total_usec: meter
                .f64_gauge("sandbox_cgroup_pressure_some_total_usec")
                .with_unit("us")
                .build(),
            cgroup_pressure_full_avg10: meter
                .f64_gauge("sandbox_cgroup_pressure_full_avg10")
                .build(),
            cgroup_pressure_full_avg60: meter
                .f64_gauge("sandbox_cgroup_pressure_full_avg60")
                .build(),
            cgroup_pressure_full_avg300: meter
                .f64_gauge("sandbox_cgroup_pressure_full_avg300")
                .build(),
            cgroup_pressure_full_total_usec: meter
                .f64_gauge("sandbox_cgroup_pressure_full_total_usec")
                .with_unit("us")
                .build(),
            cgroup_disk_upperdir_bytes: meter
                .f64_gauge("sandbox_cgroup_disk_upperdir_bytes")
                .with_unit("By")
                .build(),
            cgroup_disk_upperdir_files: meter
                .f64_gauge("sandbox_cgroup_disk_upperdir_files")
                .build(),
            cgroup_disk_upperdir_dirs: meter.f64_gauge("sandbox_cgroup_disk_upperdir_dirs").build(),
            cgroup_disk_upperdir_symlinks: meter
                .f64_gauge("sandbox_cgroup_disk_upperdir_symlinks")
                .build(),
            cgroup_disk_upperdir_read_errors: meter
                .f64_gauge("sandbox_cgroup_disk_upperdir_read_errors")
                .build(),
            cgroup_disk_upperdir_scan_truncated: meter
                .f64_gauge("sandbox_cgroup_disk_upperdir_scan_truncated")
                .build(),
            cgroup_samples_enabled,
        }
    }

    fn record_optional_u64(gauge: &Gauge<f64>, value: Option<u64>, attrs: &[KeyValue]) {
        if let Some(value) = value {
            gauge.record(value as f64, attrs);
        }
    }

    fn record_optional_f64(gauge: &Gauge<f64>, value: Option<f64>, attrs: &[KeyValue]) {
        if let Some(value) = value {
            gauge.record(value, attrs);
        }
    }
}

impl RuntimeMetricsRecorder for OpenTelemetryRuntimeMetricsRecorder {
    fn record_runtime_latency(
        &self,
        operation: RuntimeOperationName,
        status: RuntimeMetricStatus,
        latency: Duration,
    ) {
        self.runtime_latency_ms.record(
            latency.as_secs_f64() * 1000.0,
            &[
                KeyValue::new("operation", operation.as_str()),
                KeyValue::new("status", status.as_str()),
            ],
        );
    }

    fn record_workspace_phase(
        &self,
        phase: WorkspacePhase,
        status: RuntimeMetricStatus,
        latency: Duration,
    ) {
        self.workspace_phase_latency_ms.record(
            latency.as_secs_f64() * 1000.0,
            &[
                KeyValue::new("workspace_phase", phase.as_str()),
                KeyValue::new("status", status.as_str()),
            ],
        );
    }

    fn record_cgroup_sample(
        &self,
        target_kind: CgroupMonitorTargetKind,
        sample: &CgroupMonitorSample,
    ) {
        if !self.cgroup_samples_enabled {
            return;
        }
        let status = if sample.state.read_error.is_some() {
            RuntimeMetricStatus::Error
        } else {
            RuntimeMetricStatus::Ok
        };
        let cpu_attrs = cgroup_resource_attrs(target_kind, status, "cpu");
        Self::record_optional_u64(
            &self.cgroup_cpu_usage_usec,
            sample.cpu.usage_usec,
            &cpu_attrs,
        );
        Self::record_optional_u64(
            &self.cgroup_cpu_delta_usage_usec,
            sample.cpu.delta_usage_usec,
            &cpu_attrs,
        );
        Self::record_optional_f64(
            &self.cgroup_cpu_percent,
            sample.cpu.percent_over_interval,
            &cpu_attrs,
        );

        let memory_attrs = cgroup_resource_attrs(target_kind, status, "memory");
        Self::record_optional_u64(
            &self.cgroup_memory_current_bytes,
            sample.memory.current_bytes,
            &memory_attrs,
        );
        Self::record_optional_u64(
            &self.cgroup_memory_peak_bytes,
            sample.memory.peak_bytes,
            &memory_attrs,
        );

        let pids_attrs = cgroup_resource_attrs(target_kind, status, "pids");
        Self::record_optional_u64(&self.cgroup_pids_current, sample.pids.current, &pids_attrs);
        Self::record_optional_u64(&self.cgroup_pids_peak, sample.pids.peak, &pids_attrs);
        self.cgroup_pids_sampled_count
            .record(sample.pids.sampled.len() as f64, &pids_attrs);

        record_pressure_resource(self, target_kind, status, "cpu", &sample.pressure.cpu);
        record_pressure_resource(self, target_kind, status, "memory", &sample.pressure.memory);
        record_pressure_resource(self, target_kind, status, "io", &sample.pressure.io);

        let disk_attrs = cgroup_resource_attrs(target_kind, status, "disk");
        self.cgroup_disk_upperdir_bytes
            .record(sample.disk.upperdir_bytes as f64, &disk_attrs);
        self.cgroup_disk_upperdir_files
            .record(sample.disk.upperdir_files as f64, &disk_attrs);
        self.cgroup_disk_upperdir_dirs
            .record(sample.disk.upperdir_dirs as f64, &disk_attrs);
        self.cgroup_disk_upperdir_symlinks
            .record(sample.disk.upperdir_symlinks as f64, &disk_attrs);
        self.cgroup_disk_upperdir_read_errors
            .record(sample.disk.upperdir_read_error_count as f64, &disk_attrs);
        self.cgroup_disk_upperdir_scan_truncated.record(
            u8::from(sample.disk.upperdir_scan_truncated) as f64,
            &disk_attrs,
        );
    }

    fn record_publish_rejection(&self, reason: PublishRejectionReason) {
        self.publish_rejections
            .add(1, &[KeyValue::new("bounded_reason", reason.as_str())]);
    }

    fn record_remount_failure(&self, reason: RemountFailureReason) {
        self.remount_failures
            .add(1, &[KeyValue::new("bounded_reason", reason.as_str())]);
    }

    fn record_command_cancellation(&self, reason: CommandCancellationReason) {
        self.command_cancellations
            .add(1, &[KeyValue::new("bounded_reason", reason.as_str())]);
    }

    fn record_cgroup_read_error(
        &self,
        target_kind: CgroupMonitorTargetKind,
        error_kind: CgroupReadErrorKind,
    ) {
        self.cgroup_read_errors.add(
            1,
            &[
                KeyValue::new("cgroup_target_kind", target_kind.as_str()),
                KeyValue::new("bounded_error_kind", error_kind.as_str()),
            ],
        );
    }
}

fn cgroup_resource_attrs(
    target_kind: CgroupMonitorTargetKind,
    status: RuntimeMetricStatus,
    resource_kind: &'static str,
) -> [KeyValue; 3] {
    [
        KeyValue::new("cgroup_target_kind", target_kind.as_str()),
        KeyValue::new("status", status.as_str()),
        KeyValue::new("resource_kind", resource_kind),
    ]
}

fn record_pressure_resource(
    recorder: &OpenTelemetryRuntimeMetricsRecorder,
    target_kind: CgroupMonitorTargetKind,
    status: RuntimeMetricStatus,
    resource_kind: &'static str,
    sample: &sandbox_runtime::PressureResourceSample,
) {
    let attrs = cgroup_resource_attrs(target_kind, status, resource_kind);
    OpenTelemetryRuntimeMetricsRecorder::record_optional_f64(
        &recorder.cgroup_pressure_some_avg10,
        sample.some_avg10,
        &attrs,
    );
    OpenTelemetryRuntimeMetricsRecorder::record_optional_f64(
        &recorder.cgroup_pressure_some_avg60,
        sample.some_avg60,
        &attrs,
    );
    OpenTelemetryRuntimeMetricsRecorder::record_optional_f64(
        &recorder.cgroup_pressure_some_avg300,
        sample.some_avg300,
        &attrs,
    );
    OpenTelemetryRuntimeMetricsRecorder::record_optional_u64(
        &recorder.cgroup_pressure_some_total_usec,
        sample.some_total_usec,
        &attrs,
    );
    OpenTelemetryRuntimeMetricsRecorder::record_optional_f64(
        &recorder.cgroup_pressure_full_avg10,
        sample.full_avg10,
        &attrs,
    );
    OpenTelemetryRuntimeMetricsRecorder::record_optional_f64(
        &recorder.cgroup_pressure_full_avg60,
        sample.full_avg60,
        &attrs,
    );
    OpenTelemetryRuntimeMetricsRecorder::record_optional_f64(
        &recorder.cgroup_pressure_full_avg300,
        sample.full_avg300,
        &attrs,
    );
    OpenTelemetryRuntimeMetricsRecorder::record_optional_u64(
        &recorder.cgroup_pressure_full_total_usec,
        sample.full_total_usec,
        &attrs,
    );
}
