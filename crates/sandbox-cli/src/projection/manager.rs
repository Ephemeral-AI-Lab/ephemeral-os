use sandbox_operation_contract::OperationDomain;

use super::{ArgumentProjection, CatalogProjection, OperationProjection};

const CREATE_SANDBOX_ARGUMENTS: &[ArgumentProjection] = &[
    ArgumentProjection::flag("image", "--image"),
    ArgumentProjection::flag_with_additional(
        "workspace_root",
        "--workspace-bind-root",
        &["--workspace-root"],
    ),
    ArgumentProjection::flag("count", "--count"),
];

const SANDBOX_ID_ARGUMENT: &[ArgumentProjection] =
    &[ArgumentProjection::flag("sandbox_id", "--sandbox-id")];

const EXPORT_CHANGES_ARGUMENTS: &[ArgumentProjection] = &[
    ArgumentProjection::flag("sandbox_id", "--sandbox-id"),
    ArgumentProjection::flag("dest", "--dest"),
    ArgumentProjection::flag("format", "--format"),
];

const OPERATIONS: &[OperationProjection] = &[
    OperationProjection {
        name: "create_sandbox",
        path: &["manager", "create_sandbox"],
        usage: "sandbox-manager-cli create_sandbox --image IMAGE --workspace-bind-root PATH [--count N]",
        examples: &[
            "sandbox-manager-cli create_sandbox --image ubuntu:24.04 --workspace-bind-root /testbed",
            "sandbox-manager-cli create_sandbox --image ubuntu:24.04 --workspace-bind-root /testbed --count 5",
        ],
        arguments: CREATE_SANDBOX_ARGUMENTS,
    },
    OperationProjection {
        name: "destroy_sandbox",
        path: &["manager", "destroy_sandbox"],
        usage: "sandbox-manager-cli destroy_sandbox --sandbox-id ID",
        examples: &["sandbox-manager-cli destroy_sandbox --sandbox-id sbox-1"],
        arguments: SANDBOX_ID_ARGUMENT,
    },
    OperationProjection {
        name: "list_sandboxes",
        path: &["manager", "list_sandboxes"],
        usage: "sandbox-manager-cli list_sandboxes",
        examples: &["sandbox-manager-cli list_sandboxes"],
        arguments: &[],
    },
    OperationProjection {
        name: "inspect_sandbox",
        path: &["manager", "inspect_sandbox"],
        usage: "sandbox-manager-cli inspect_sandbox --sandbox-id ID",
        examples: &["sandbox-manager-cli inspect_sandbox --sandbox-id sbox-1"],
        arguments: SANDBOX_ID_ARGUMENT,
    },
    OperationProjection {
        name: "squash_layerstacks",
        path: &["manager", "squash_layerstacks"],
        usage: "sandbox-manager-cli squash_layerstacks --sandbox-id ID",
        examples: &["sandbox-manager-cli squash_layerstacks --sandbox-id sbox-1"],
        arguments: SANDBOX_ID_ARGUMENT,
    },
    OperationProjection {
        name: "export_changes",
        path: &["manager", "export_changes"],
        usage: "sandbox-manager-cli export_changes --sandbox-id ID --dest PATH [--format dir|tar|tar-zst]",
        examples: &[
            "sandbox-manager-cli export_changes --sandbox-id sbox-1 --dest /home/me/myproject",
            "sandbox-manager-cli export_changes --sandbox-id sbox-1 --dest /tmp/delta.tar.zst --format tar-zst",
        ],
        arguments: EXPORT_CHANGES_ARGUMENTS,
    },
];

#[must_use]
pub const fn catalog_projection() -> CatalogProjection {
    CatalogProjection {
        operation_execution_space: OperationDomain::Manager,
        operations: OPERATIONS,
    }
}
