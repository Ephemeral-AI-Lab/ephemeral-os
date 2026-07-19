use sandbox_operation_contract::OperationDomain;

use super::{ArgumentProjection, CatalogProjection, OperationProjection};

const SNAPSHOT_ARGUMENTS: &[ArgumentProjection] =
    &[ArgumentProjection::flag("sandbox_id", "--sandbox-id")];

const TRACE_ARGUMENTS: &[ArgumentProjection] = &[
    ArgumentProjection::flag("sandbox_id", "--sandbox-id"),
    ArgumentProjection::flag("trace_id", "--trace-id"),
];

const EVENTS_ARGUMENTS: &[ArgumentProjection] = &[
    ArgumentProjection::flag("sandbox_id", "--sandbox-id"),
    ArgumentProjection::flag("name", "--name"),
    ArgumentProjection::flag("since_ms", "--since-ms"),
    ArgumentProjection::flag("last_n", "--last-n"),
];

const RESOURCES_ARGUMENTS: &[ArgumentProjection] = &[
    ArgumentProjection::flag("sandbox_id", "--sandbox-id"),
    ArgumentProjection::flag("window_ms", "--window-ms"),
];

const DAEMON_ARGUMENTS: &[ArgumentProjection] =
    &[ArgumentProjection::flag("sandbox_id", "--sandbox-id")];

const TOPOLOGY_ARGUMENTS: &[ArgumentProjection] =
    &[ArgumentProjection::flag("sandbox_id", "--sandbox-id")];

const CGROUP_ARGUMENTS: &[ArgumentProjection] = &[
    ArgumentProjection::flag("sandbox_id", "--sandbox-id"),
    ArgumentProjection::flag("scope", "--scope"),
    ArgumentProjection::flag("window_ms", "--window-ms"),
];

const LAYERSTACK_ARGUMENTS: &[ArgumentProjection] = &[
    ArgumentProjection::flag("sandbox_id", "--sandbox-id"),
    ArgumentProjection::flag("workspace_id", "--workspace-id"),
    ArgumentProjection::flag("window_ms", "--window-ms"),
];

const OPERATIONS: &[OperationProjection] = &[
    OperationProjection {
        name: "snapshot",
        path: &["observability", "snapshot"],
        usage: "sandbox-observability-cli snapshot [--sandbox-id ID]",
        examples: &[
            "sandbox-observability-cli snapshot",
            "sandbox-observability-cli snapshot --sandbox-id eos-abc",
        ],
        arguments: SNAPSHOT_ARGUMENTS,
    },
    OperationProjection {
        name: "trace",
        path: &["observability", "trace"],
        usage: "sandbox-observability-cli trace --sandbox-id ID [--trace-id TRACE|last]",
        examples: &[
            "sandbox-observability-cli trace --sandbox-id eos-abc --trace-id req-7f3",
            "sandbox-observability-cli trace --sandbox-id eos-abc --trace-id last",
        ],
        arguments: TRACE_ARGUMENTS,
    },
    OperationProjection {
        name: "events",
        path: &["observability", "events"],
        usage: "sandbox-observability-cli events --sandbox-id ID [--name NAME] [--since-ms MS] [--last-n N]",
        examples: &[
            "sandbox-observability-cli events --sandbox-id eos-abc",
            "sandbox-observability-cli events --sandbox-id eos-abc --name lease.acquired",
            "sandbox-observability-cli events --sandbox-id eos-abc --last-n 20",
        ],
        arguments: EVENTS_ARGUMENTS,
    },
    OperationProjection {
        name: "resources",
        path: &["observability", "resources"],
        usage: "sandbox-observability-cli resources [--sandbox-id ID] [--window-ms MS]",
        examples: &[
            "sandbox-observability-cli resources",
            "sandbox-observability-cli resources --sandbox-id eos-abc --window-ms 60000",
        ],
        arguments: RESOURCES_ARGUMENTS,
    },
    OperationProjection {
        name: "daemon",
        path: &["observability", "daemon"],
        usage: "sandbox-observability-cli daemon --sandbox-id ID",
        examples: &["sandbox-observability-cli daemon --sandbox-id eos-abc"],
        arguments: DAEMON_ARGUMENTS,
    },
    OperationProjection {
        name: "topology",
        path: &["observability", "topology"],
        usage: "sandbox-observability-cli topology --sandbox-id ID",
        examples: &["sandbox-observability-cli topology --sandbox-id eos-abc"],
        arguments: TOPOLOGY_ARGUMENTS,
    },
    OperationProjection {
        name: "cgroup",
        path: &["observability", "cgroup"],
        usage: "sandbox-observability-cli cgroup --sandbox-id ID [--scope SCOPE] [--window-ms MS]",
        examples: &[
            "sandbox-observability-cli cgroup --sandbox-id eos-abc",
            "sandbox-observability-cli cgroup --sandbox-id eos-abc --scope ws-1 --window-ms 60000",
        ],
        arguments: CGROUP_ARGUMENTS,
    },
    OperationProjection {
        name: "layerstack",
        path: &["observability", "layerstack"],
        usage: "sandbox-observability-cli layerstack --sandbox-id ID [--workspace-id WS] [--window-ms MS]",
        examples: &[
            "sandbox-observability-cli layerstack --sandbox-id eos-abc",
            "sandbox-observability-cli layerstack --sandbox-id eos-abc --workspace-id ws-7",
        ],
        arguments: LAYERSTACK_ARGUMENTS,
    },
];

#[must_use]
pub const fn catalog_projection() -> CatalogProjection {
    CatalogProjection {
        operation_execution_space: OperationDomain::Observability,
        operations: OPERATIONS,
    }
}
