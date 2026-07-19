use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use xtask::operation_architecture::{
    load_feature_facts, load_semantic_facts, load_stale_facts, load_workspace_facts,
    validate_feature_facts, validate_feature_gates, validate_semantic_facts, validate_stale_facts,
    validate_workspace_facts, DependencyFact, Domain, DomainOperation, StaleFacts, TrackedSource,
};

const RETIRED_E2E_TREE: &str = concat!("cli-operation-e2e-", "live-test");

fn repository_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("xtask has repository parent")
        .to_path_buf()
}

fn copy_directory(source: &Path, destination: &Path) {
    fs::create_dir_all(destination).expect("create semantic fixture directory");
    for entry in fs::read_dir(source).expect("read semantic fixture source") {
        let entry = entry.expect("read semantic fixture entry");
        let target = destination.join(entry.file_name());
        if entry
            .file_type()
            .expect("read semantic fixture type")
            .is_dir()
        {
            copy_directory(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).expect("copy semantic fixture file");
        }
    }
}

fn semantic_fixture(root: &Path) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let fixture = std::env::temp_dir().join(format!(
        "operation-architecture-semantic-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = fs::remove_dir_all(&fixture);
    for directory in [
        "crates/sandbox-operations/catalog/src",
        "crates/sandbox-manager/src",
        "crates/sandbox-runtime/operation/src",
        "crates/sandbox-observability/query/src",
        "crates/sandbox-cli/src/projection",
    ] {
        copy_directory(&root.join(directory), &fixture.join(directory));
    }
    fixture
}

fn assert_clean(label: &str, violations: &[String]) {
    assert!(violations.is_empty(), "{label}: {violations:#?}");
}

fn manifest_override_fixture(override_declaration: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let root = std::env::temp_dir().join(format!(
        "operation-architecture-manifest-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(root.join("contract/src")).expect("create manifest fixture");
    fs::write(
        root.join("Cargo.toml"),
        "[workspace]\nmembers = [\"contract\"]\nresolver = \"2\"\n",
    )
    .expect("write workspace manifest");
    let package = "[package]\nname = \"sandbox-operation-contract\"\nversion = \"0.1.0\"\nedition = \"2021\"\n";
    let manifest = if override_declaration.starts_with('[') {
        format!("{package}\n{override_declaration}\n")
    } else {
        format!("{override_declaration}\n\n{package}")
    };
    fs::write(root.join("contract/Cargo.toml"), manifest).expect("write package manifest");
    fs::write(root.join("contract/src/lib.rs"), "").expect("write library source");
    fs::write(root.join("contract/src/#.rs"), "").expect("write quoted hash library source");
    root
}

#[test]
fn forbidden_dependency_edge_is_rejected_for_every_edge_kind() {
    let root = repository_root();
    let packages = load_workspace_facts(&root).expect("load cargo metadata");
    assert_clean("workspace fixture", &validate_workspace_facts(&packages));
    for (package_name, target_name) in [
        ("sandbox-operation-catalog", "integrity"),
        ("sandbox-cli", "projection_integrity"),
        ("sandbox-manager", "manager_router"),
        ("sandbox-manager", "manager_export"),
        ("sandbox-runtime", "operation_registry"),
        ("sandbox-observability-query", "query"),
    ] {
        let mut replaced = packages.clone();
        replaced
            .iter_mut()
            .find(|package| package.name == package_name)
            .expect("proof package")
            .test_targets
            .insert(
                target_name.to_owned(),
                format!("crates/{package_name}/tests/autotests_fake.rs"),
            );
        assert!(validate_workspace_facts(&replaced).iter().any(|violation| {
            violation.contains(&format!(
                "proof target {package_name}:{target_name} resolves to"
            )) && violation.contains("autotests_fake.rs")
        }));
    }
    let contract = packages
        .iter()
        .position(|package| package.name == "sandbox-operation-contract")
        .expect("contract package");

    for (kind, optional) in [("dev", false), ("build", false), ("normal", true)] {
        let mut mutated = packages.clone();
        mutated[contract].dependencies.push(DependencyFact {
            package: "sandbox-protocol".to_owned(),
            rename: None,
            manifest: "crates/sandbox-protocol/Cargo.toml".to_owned(),
            kind: kind.to_owned(),
            optional,
            uses_default_features: true,
            features: BTreeSet::new(),
        });
        let violations = validate_workspace_facts(&mutated);
        assert!(violations.iter().any(|violation| {
            violation.contains("forbidden dependency edge")
                && violation.contains(&format!("({kind}, optional={optional})"))
        }));
    }

    let mut overridden = packages.clone();
    overridden[contract].library_name_override = true;
    assert!(validate_workspace_facts(&overridden)
        .iter()
        .any(|violation| violation.contains("library target name override is forbidden")));

    let mut aliased = packages.clone();
    let catalog = aliased
        .iter_mut()
        .find(|package| package.name == "sandbox-operation-catalog")
        .expect("catalog package");
    catalog
        .dependencies
        .iter_mut()
        .find(|dependency| dependency.package == "sandbox-operation-contract")
        .expect("catalog contract dependency")
        .rename = Some("operation_contract_alias".to_owned());
    assert!(validate_workspace_facts(&aliased)
        .iter()
        .any(|violation| violation.contains("workspace dependency alias is forbidden")));

    for (package_name, dependency_name, alias) in [
        (
            "sandbox-provider-docker",
            "sandbox-manager",
            "manager_alias",
        ),
        (
            "sandbox-observability-query",
            "sandbox-runtime-layerstack",
            "layerstack_alias",
        ),
    ] {
        let mut renamed = packages.clone();
        renamed
            .iter_mut()
            .find(|package| package.name == package_name)
            .expect("package with boundary dependency")
            .dependencies
            .iter_mut()
            .find(|dependency| dependency.package == dependency_name)
            .expect("boundary dependency")
            .rename = Some(alias.to_owned());
        assert!(validate_workspace_facts(&renamed).iter().any(|violation| {
            violation.contains("workspace dependency alias is forbidden")
                && violation.contains(package_name)
                && violation.contains(alias)
        }));
    }

    let mut external_alias = packages.clone();
    external_alias
        .iter_mut()
        .find(|package| package.name == "sandbox-manager")
        .expect("manager package")
        .dependencies
        .push(DependencyFact {
            package: "rustix".to_owned(),
            rename: Some("spawn".to_owned()),
            manifest: String::new(),
            kind: "normal".to_owned(),
            optional: false,
            uses_default_features: true,
            features: BTreeSet::new(),
        });
    assert!(validate_workspace_facts(&external_alias)
        .iter()
        .any(|violation| violation.contains("adapter-capable dependency alias is forbidden")));

    for (kind, optional, rename) in [
        ("normal", false, None),
        ("dev", false, None),
        ("build", true, Some("process_wrapper")),
    ] {
        let mut unlisted_external = packages.clone();
        unlisted_external
            .iter_mut()
            .find(|package| package.name == "sandbox-manager")
            .expect("manager package")
            .dependencies
            .push(DependencyFact {
                package: "duct".to_owned(),
                rename: rename.map(str::to_owned),
                manifest: String::new(),
                kind: kind.to_owned(),
                optional,
                uses_default_features: true,
                features: BTreeSet::new(),
            });
        assert!(validate_workspace_facts(&unlisted_external)
            .iter()
            .any(|violation| {
                violation.contains("unapproved manager external dependency: duct")
                    && violation.contains(kind)
                    && violation.contains(&format!("optional={optional}"))
            }));
    }
    let mut missing_external = packages.clone();
    missing_external
        .iter_mut()
        .find(|package| package.name == "sandbox-manager")
        .expect("manager package")
        .dependencies
        .retain(|dependency| !dependency.manifest.is_empty() || dependency.package != "base64");
    assert!(validate_workspace_facts(&missing_external)
        .iter()
        .any(|violation| violation
            .contains("required manager external dependency is missing: base64")));

    for declaration in [
        "[ lib ]\nname = \"contract_alias\"",
        "[\"lib\"]\nname = \"contract_alias\"",
        "[\"li\\u0062\"]\nname = \"contract_alias\"",
        "lib = { name = \"contract_alias\" }",
        "lib = { \"na\\u006de\" = \"contract_alias\" }",
        "lib.name = \"contract_alias\"",
        "\"lib\".\"name\" = \"contract_alias\"",
        "'lib'.'name' = \"contract_alias\"",
        "lib = { path = \"src/#.rs\", name = \"sandbox_operation_contract\" }",
    ] {
        let fixture = manifest_override_fixture(declaration);
        let fixture_packages =
            load_workspace_facts(&fixture).expect("load manifest override fixture");
        assert!(
            fixture_packages[0].library_name_override,
            "missed {declaration}"
        );
        fs::remove_dir_all(fixture).expect("remove manifest fixture");
    }
}

#[test]
fn unmapped_package_is_rejected_by_default() {
    let root = repository_root();
    let mut packages = load_workspace_facts(&root).expect("load cargo metadata");
    assert_clean("workspace fixture", &validate_workspace_facts(&packages));
    let mut unmapped = packages[0].clone();
    unmapped.name = "phase8-unmapped".to_owned();
    unmapped.manifest = "crates/phase8-unmapped/Cargo.toml".to_owned();
    packages.push(unmapped);

    assert!(validate_workspace_facts(&packages).iter().any(|violation| {
        violation == "unmapped workspace package: phase8-unmapped at crates/phase8-unmapped/Cargo.toml"
    }));
}

#[test]
fn missing_and_extra_handlers_are_rejected() {
    let root = repository_root();
    let mut route_facts = load_semantic_facts(&root).expect("load semantic sources");
    let removed_route = route_facts.public_routes.remove(0);
    assert!(validate_semantic_facts(&route_facts)
        .iter()
        .any(|violation| violation
            == &format!(
                "public operation {}:{} has no route",
                route_facts
                    .public_operations
                    .iter()
                    .find(|operation| operation.operation == removed_route.operation)
                    .expect("removed route operation")
                    .domain,
                removed_route.operation
            )));

    let mut facts = load_semantic_facts(&root).expect("load semantic sources");
    assert_clean("semantic fixture", &validate_semantic_facts(&facts));
    let removed = facts.public_handlers.remove(0);
    let mut extra = removed;
    extra.operation = "phase8_extra_handler".to_owned();
    facts.public_handlers.push(extra);
    facts
        .unclassified_operation_declarations
        .push("lib.rs:PHASE8_SPEC".to_owned());

    let violations = validate_semantic_facts(&facts);
    assert!(violations
        .iter()
        .any(|violation| violation.starts_with("missing public handler for ")));
    assert!(violations
        .iter()
        .any(|violation| violation.starts_with("extra public handler for ")));
    assert!(violations.iter().any(|violation| {
        violation == "operation spec declaration is outside a domain module: lib.rs:PHASE8_SPEC"
    }));

    let fixture = semantic_fixture(&root);
    let aggregate = fixture.join("crates/sandbox-operations/catalog/src/manager.rs");
    let original = fs::read_to_string(&aggregate).expect("read manager aggregate fixture");
    fs::write(
        &aggregate,
        format!("const OPERATIONS_DECOY: &[()] = &[];\n{original}"),
    )
    .expect("write prefix-decoy fixture");
    load_semantic_facts(&fixture).expect("prefix decoy is ignored");
    fs::write(
        &aggregate,
        format!("{original}\nconst OPERATIONS: &[()] = &[];\n"),
    )
    .expect("write duplicate aggregate fixture");
    let error = load_semantic_facts(&fixture).expect_err("duplicate aggregate must fail");
    assert!(error
        .to_string()
        .contains("multiple const OPERATIONS aggregates"));
    fs::remove_dir_all(fixture).expect("remove semantic fixture");

    for (relative, source, expected) in [
        (
            "crates/sandbox-operations/catalog/src/runtime/phase8_extra.rs",
            "pub const OPERATIONS: &[&crate::routed::RoutedOperation] = &[];",
            "multiple runtime catalog OPERATIONS aggregates",
        ),
        (
            "crates/sandbox-operations/catalog/src/manager/phase8_extra.rs",
            "pub const OPERATIONS: &[&crate::routed::RoutedOperation] = &[];",
            "multiple manager catalog OPERATIONS aggregates",
        ),
        (
            "crates/sandbox-operations/catalog/src/runtime/phase8_extra.rs",
            "use sandbox_operation_contract::OperationSpec as Spec; pub const PHASE8_SPEC: Spec = Spec { name: \"phase8\" };",
            "unparsed OperationSpec construction",
        ),
        (
            "crates/sandbox-operations/catalog/src/runtime/phase8_extra.rs",
            "pub const PHASE8_SPEC: sandbox_operation_contract::OperationSpec = sandbox_operation_contract::OperationSpec { name: \"phase8\" };",
            "unparsed OperationSpec construction",
        ),
        (
            "crates/sandbox-operations/catalog/src/runtime/phase8_extra.rs",
            "use crate::routed::RoutedOperation as Route; pub const PHASE8_ROUTE: Route = Route { spec: &PHASE8_SPEC };",
            "unparsed RoutedOperation construction",
        ),
        (
            "crates/sandbox-manager/src/operations/registry/phase8_extra.rs",
            "use crate::operations::dispatch::ManagerOperationEntry as Entry; const PHASE8_ENTRY: Entry = Entry::new(&CREATE_SANDBOX_SPEC, OperationScopeKind::System, dispatch);",
            "unwired manager handler construction",
        ),
        (
            "crates/sandbox-manager/src/operations/registry/phase8_extra.rs",
            "use crate::operations::dispatch::ManagerOperationEntry as Entry; const PHASE8_ENTRY: Entry = Entry { scope_kind: OperationScopeKind::System, spec: &CREATE_SANDBOX_SPEC, dispatch };",
            "manager executable entry construction census",
        ),
        (
            "crates/sandbox-runtime/operation/src/operations/registry/phase8_extra.rs",
            "use crate::operations::dispatch::OperationEntry as Entry; const PHASE8_ENTRY: Entry = Entry::public(&EXEC_COMMAND_SPEC, dispatch);",
            "unwired runtime handler construction",
        ),
        (
            "crates/sandbox-observability/query/src/phase8_extra.rs",
            "use crate::registry::OperationEntry as Entry; const PHASE8_ENTRY: Entry = Entry::new(&SNAPSHOT_SPEC, dispatch);",
            "unwired observability handler construction",
        ),
        (
            "crates/sandbox-observability/query/src/phase8_extra.rs",
            "use crate::registry::OperationEntry as Entry; const PHASE8_ENTRY: Entry = Entry { scope_kind: OperationScopeKind::Sandbox, spec: &SNAPSHOT_SPEC, handler: dispatch };",
            "observability executable entry construction census",
        ),
        (
            "crates/sandbox-cli/src/projection/phase8_extra.rs",
            "use super::OperationProjection as Projection; const PHASE8: Projection = Projection { name: \"phase8\" };",
            "unwired CLI projection construction",
        ),
        (
            "crates/sandbox-cli/src/projection/phase8_extra.rs",
            "const PHASE8: crate::projection::OperationProjection = crate::projection::OperationProjection { name: \"phase8\" };",
            "unwired CLI projection construction",
        ),
    ] {
        let fixture = semantic_fixture(&root);
        let path = fixture.join(relative);
        fs::create_dir_all(path.parent().expect("semantic mutation parent"))
            .expect("create semantic mutation parent");
        fs::write(path, source).expect("write semantic mutation");
        let error = load_semantic_facts(&fixture).expect_err(expected);
        assert!(
            format!("{error:#}").contains(expected),
            "{expected}: {error:#}"
        );
        fs::remove_dir_all(fixture).expect("remove semantic mutation fixture");
    }

    let fixture = semantic_fixture(&root);
    let path = fixture.join("crates/sandbox-runtime/operation/src/operations/dispatch.rs");
    let source = fs::read_to_string(&path).expect("read runtime dispatch fixture");
    let original = concat!(
        "fn operation_entries() -> impl Iterator<Item = &'static OperationEntry> {\n",
        "    registry::public_operation_entries()\n",
        "        .chain(registry::internal_operation_entries())\n",
        "        .chain(registry::http_only_operation_entries())\n",
        "}\n",
    );
    let backdoor = concat!(
        "const BACKDOOR: OperationEntry = OperationEntry {\n",
        "    scope_kind: OperationScopeKind::Sandbox,\n",
        "    name: \"phase8_backdoor\",\n",
        "    spec: None,\n",
        "    dispatch: backdoor,\n",
        "};\n\n",
        "fn operation_entries() -> impl Iterator<Item = &'static OperationEntry> {\n",
        "    std::iter::once(&BACKDOOR)\n",
        "        .chain(registry::public_operation_entries())\n",
        "        .chain(registry::internal_operation_entries())\n",
        "        .chain(registry::http_only_operation_entries())\n",
        "}\n",
    );
    assert!(
        source.contains(original),
        "runtime dispatch fixture changed"
    );
    fs::write(&path, source.replace(original, backdoor)).expect("write runtime backdoor fixture");
    let error = load_semantic_facts(&fixture).expect_err("runtime dispatch backdoor must fail");
    assert!(
        error
            .to_string()
            .contains("runtime executable entry construction census"),
        "runtime dispatch backdoor: {error:#}"
    );
    fs::remove_dir_all(fixture).expect("remove runtime backdoor fixture");
}

#[test]
fn public_internal_route_overlap_is_rejected() {
    let root = repository_root();
    let mut facts = load_semantic_facts(&root).expect("load semantic sources");
    assert_clean("semantic fixture", &validate_semantic_facts(&facts));
    let route = facts.public_routes[0].clone();
    let expected = format!(
        "public/internal route overlap at {}:{}",
        route.scope, route.operation
    );
    facts.internal_routes.push(route);

    assert!(validate_semantic_facts(&facts)
        .iter()
        .any(|violation| violation == &expected));
}

#[test]
fn missing_projection_entry_is_rejected() {
    let root = repository_root();
    let mut facts = load_semantic_facts(&root).expect("load semantic sources");
    assert_clean("semantic fixture", &validate_semantic_facts(&facts));
    let removed = facts.projections.remove(0);
    let expected = format!(
        "missing CLI projection entry {}:{}",
        removed.domain, removed.operation
    );
    facts.projections.push(DomainOperation {
        domain: Domain::Runtime,
        operation: "phase8_extra_projection".to_owned(),
    });

    let violations = validate_semantic_facts(&facts);
    assert!(violations.iter().any(|violation| violation == &expected));
    assert!(violations.iter().any(|violation| {
        violation == "extra CLI projection entry runtime:phase8_extra_projection"
    }));
}

#[test]
fn out_of_closure_catalog_feature_is_rejected() {
    let root = repository_root();
    let mut facts = load_feature_facts(&root).expect("load cargo feature trees");
    assert_clean("feature fixture", &validate_feature_facts(&facts));
    facts
        .resolved
        .get_mut(&Domain::Manager)
        .expect("manager feature closure")
        .insert("runtime".to_owned());

    assert!(validate_feature_facts(&facts).iter().any(|violation| {
        violation == "out-of-closure catalog feature for manager CLI: runtime"
    }));

    let feature_gates = load_stale_facts(&root).expect("load feature-gate sources");
    assert_clean(
        "feature-gate fixture",
        &validate_feature_gates(&feature_gates),
    );

    let assert_rejected = |path: &str, before: &str, after: &str, expected: &str| {
        let mut mutated = feature_gates.clone();
        let source = mutated
            .files
            .iter_mut()
            .find(|file| file.path == path)
            .expect("feature-gate source");
        assert_eq!(
            source.content.matches(before).count(),
            1,
            "mutation source must be unique: {path}: {before}"
        );
        source.content = source.content.replacen(before, after, 1);
        let violations = validate_feature_gates(&mutated);
        assert!(
            violations
                .iter()
                .any(|violation| violation.contains(path) && violation.contains(expected)),
            "{path}: {expected}: {violations:#?}"
        );
    };
    let assert_added_rejected = |path: &str, addition: &str, prepend: bool, expected: &str| {
        let mut mutated = feature_gates.clone();
        let source = mutated
            .files
            .iter_mut()
            .find(|file| file.path == path)
            .expect("feature-gate source");
        if prepend {
            source.content.insert_str(0, addition);
        } else {
            source.content.push_str(addition);
        }
        let violations = validate_feature_gates(&mutated);
        assert!(
            violations
                .iter()
                .any(|violation| violation.contains(path) && violation.contains(expected)),
            "{path}: {expected}: {violations:#?}"
        );
    };

    let catalog_lib = "crates/sandbox-operations/catalog/src/lib.rs";
    let gated_sources = [
        catalog_lib,
        "crates/sandbox-operations/catalog/src/routes.rs",
        "crates/sandbox-cli/src/lib.rs",
        "crates/sandbox-cli/src/projection/mod.rs",
    ];
    assert_rejected(
        catalog_lib,
        "#[cfg(feature = \"runtime\")]\npub mod runtime;",
        "",
        "module runtime exactly once, found 0",
    );
    assert_rejected(
        catalog_lib,
        "#[cfg(feature = \"runtime\")]\npub mod runtime;",
        "#[cfg(feature = \"manager\")]\npub mod runtime;",
        "module runtime must have exactly cfg(feature = \"runtime\")",
    );
    assert_rejected(
        catalog_lib,
        "#[cfg(feature = \"runtime\")]\npub mod runtime;",
        "#[cfg(any(feature = \"runtime\", feature = \"manager\"))]\npub mod runtime;",
        "module runtime must have exactly cfg(feature = \"runtime\")",
    );
    assert_rejected(
        catalog_lib,
        "#[cfg(feature = \"runtime\")]\npub mod runtime;",
        "#[cfg_attr(all(), cfg(feature = \"runtime\"))]\npub mod runtime;",
        "module runtime must have exactly cfg(feature = \"runtime\")",
    );
    assert_rejected(
        catalog_lib,
        "#[cfg(feature = \"runtime\")]\npub mod runtime;",
        "#[cfg(feature = \"runtime\")]\npub mod runtime {}",
        "module runtime has an alternative declaration shape",
    );
    assert_rejected(
        catalog_lib,
        "#[cfg(feature = \"manager\")]\npub mod manager;",
        "#[cfg(feature = \"manager\")]\n#[path = \"manager/wrapper.rs\"]\npub mod manager;",
        "forbidden outer path attribute",
    );
    for module in ["internal", "routed", "routes"] {
        let before = format!("pub mod {module};");
        let after = format!("#[cfg(feature = \"manager\")]\npub mod {module};");
        assert_rejected(
            catalog_lib,
            &before,
            &after,
            &format!("module {module} must be unconditional"),
        );
    }

    let mut duplicate = feature_gates.clone();
    duplicate
        .files
        .iter_mut()
        .find(|file| file.path == catalog_lib)
        .expect("catalog library source")
        .content
        .push_str("\n#[cfg(feature = \"runtime\")]\npub mod runtime;\n");
    let duplicate_violations = validate_feature_gates(&duplicate);
    assert!(duplicate_violations.iter().any(|violation| {
        violation.contains(catalog_lib)
            && violation.contains("module runtime exactly once, found 2")
    }));

    let catalog_routes = "crates/sandbox-operations/catalog/src/routes.rs";
    let disguised_runtime = concat!(
        "\n#[path = \"runtime.rs\"]\n",
        "pub mod runtime_without_feature;\n",
        "pub use runtime_without_feature as runtime;\n"
    );
    assert_added_rejected(
        catalog_lib,
        disguised_runtime,
        false,
        "unexpected top-level module runtime_without_feature",
    );
    assert_added_rejected(
        catalog_lib,
        disguised_runtime,
        false,
        "forbidden public top-level re-export",
    );
    for path in &gated_sources[1..] {
        assert_added_rejected(
            path,
            "\npub mod feature_gate_leak;\n",
            false,
            "unexpected top-level module feature_gate_leak",
        );
    }
    for path in gated_sources {
        assert_added_rejected(
            path,
            "\npub use self::runtime as leaked_runtime_routes;\n",
            false,
            "forbidden public top-level re-export",
        );
        assert_added_rejected(
            path,
            "#![cfg(feature = \"manager\")]\n",
            true,
            "forbidden inner cfg or cfg_attr attribute",
        );
        assert_added_rejected(
            path,
            "#![cfg_attr(feature = \"manager\", cfg(feature = \"runtime\"))]\n",
            true,
            "forbidden inner cfg or cfg_attr attribute",
        );
    }
    assert_added_rejected(
        catalog_routes,
        "\npub fn leaked_runtime_routes() {}\n",
        false,
        "unexpected public top-level function leaked_runtime_routes",
    );
    assert_added_rejected(
        catalog_lib,
        "\n#[cfg(feature = \"manager\")]\ninclude!(\"manager_alias.rs\");\n",
        false,
        "forbidden top-level include invocation",
    );
    assert_rejected(
        catalog_lib,
        "#[cfg(feature = \"runtime\")]\npub mod runtime;",
        "#[cfg(feature = \"runtime\")]\npub mod runtime {\n#![cfg(feature = \"runtime\")]\n}",
        "forbidden inner cfg or cfg_attr attribute",
    );
    for (feature, alternative, function) in [
        ("manager", "runtime", "manager_routes"),
        ("runtime", "observability", "runtime_routes"),
        ("observability", "manager", "observability_routes"),
    ] {
        let before =
            format!("#[cfg(feature = \"{feature}\")]\n#[must_use]\npub const fn {function}");
        let after =
            format!("#[cfg(feature = \"{alternative}\")]\n#[must_use]\npub const fn {function}");
        assert_rejected(
            catalog_routes,
            &before,
            &after,
            &format!("function {function} must have exactly cfg(feature = \"{feature}\")"),
        );
    }
    assert_rejected(
        catalog_routes,
        "#[cfg(all(feature = \"manager\", feature = \"runtime\", feature = \"observability\"))]",
        "#[cfg(any(feature = \"manager\", feature = \"runtime\", feature = \"observability\"))]",
        "function public_routes must require exactly all manager, runtime, and observability features",
    );
    assert_rejected(
        catalog_routes,
        "#[cfg(all(feature = \"manager\", feature = \"runtime\", feature = \"observability\"))]",
        "#[cfg(feature = \"manager\")]\n#[cfg(feature = \"runtime\")]\n#[cfg(feature = \"observability\")]",
        "function public_routes must require exactly all manager, runtime, and observability features",
    );

    for path in [
        "crates/sandbox-cli/src/lib.rs",
        "crates/sandbox-cli/src/projection/mod.rs",
    ] {
        for (feature, alternative) in [
            ("manager", "runtime"),
            ("runtime", "manager"),
            ("observability", "runtime"),
        ] {
            let before = format!("#[cfg(feature = \"{feature}\")]\npub mod {feature};");
            let after = format!(
                "#[cfg(any(feature = \"{feature}\", feature = \"{alternative}\"))]\npub mod {feature};"
            );
            assert_rejected(
                path,
                &before,
                &after,
                &format!("module {feature} must have exactly cfg(feature = \"{feature}\")"),
            );
        }
    }

    let mut decoys = feature_gates.clone();
    decoys
        .files
        .iter_mut()
        .find(|file| file.path == catalog_lib)
        .expect("catalog library source")
        .content
        .push_str(
            r####"
const FEATURE_GATE_DECOY: &str = "#[cfg(feature = \"manager\")] pub mod runtime;";
const RAW_FEATURE_GATE_DECOY: &str = r##"#[cfg(any(feature = "runtime", feature = "manager"))] pub mod runtime;"##;
const INNER_FEATURE_GATE_DECOY: &str = "#![cfg(feature = \"manager\")]";
const RAW_INNER_FEATURE_GATE_DECOY: &str = r##"#![cfg_attr(all(), cfg(feature = "runtime"))]"##;
const PATH_ATTRIBUTE_DECOY: &str = "#[path = \"manager/wrapper.rs\"]";
const RAW_INCLUDE_DECOY: &str = r##"#[cfg(feature = "manager")] include!("manager_alias.rs");"##;
// #[cfg(feature = "manager")] pub mod runtime;
/* #[cfg(any(feature = "runtime", feature = "manager"))] pub mod runtime; */
// #![cfg(feature = "manager")]
/* #![cfg_attr(all(), cfg(feature = "runtime"))] */
// #[path = "manager/wrapper.rs"]
/* include!("manager_alias.rs"); */
"####,
        );
    assert_clean("feature-gate decoys", &validate_feature_gates(&decoys));
}

#[test]
fn maintained_stale_reference_and_generated_path_are_rejected() {
    let root = repository_root();
    let mut facts = load_stale_facts(&root).expect("load tracked repository sources");

    let canonical_violations = |facts: &_| {
        validate_stale_facts(&root, facts)
            .into_iter()
            .filter(|violation| violation.starts_with("canonical literal"))
            .collect::<Vec<_>>()
    };
    let baseline_canonical = canonical_violations(&facts);
    let mut comments = facts.clone();
    comments.files.push(TrackedSource {
        path: "crates/sandbox-config/src/phase8_canonical_comments.rs".to_owned(),
        content: "// \"file_list\"\n/* \"sandbox_daemon_ready\" */".to_owned(),
        executable: false,
    });
    assert_eq!(canonical_violations(&comments), baseline_canonical);
    let mut raw_literal = facts.clone();
    raw_literal.files.push(TrackedSource {
        path: "crates/sandbox-config/src/phase8_canonical_raw.rs".to_owned(),
        content: "const DUP: &str = r#\"file_list\"#;".to_owned(),
        executable: false,
    });
    assert!(canonical_violations(&raw_literal).iter().any(|violation| {
        violation == "canonical literal \"file_list\" must occur once in production source, found 2"
    }));
    let mut escaped_literals = facts.clone();
    escaped_literals.files.push(TrackedSource {
        path: "crates/sandbox-config/src/phase8_canonical_escaped.rs".to_owned(),
        content: "const HEX: &str = \"\\x66ile_list\"; const UNICODE: &str = \"file\\u{5f}list\";"
            .to_owned(),
        executable: false,
    });
    assert!(canonical_violations(&escaped_literals)
        .iter()
        .any(|violation| {
            violation
                == "canonical literal \"file_list\" must occur once in production source, found 3"
        }));
    let mut concatenated_literal = facts.clone();
    concatenated_literal.files.push(TrackedSource {
        path: "crates/sandbox-config/src/phase8_canonical_concat.rs".to_owned(),
        content: "const DUP: &str = concat!(\"file\", \"_list\");".to_owned(),
        executable: false,
    });
    assert!(canonical_violations(&concatenated_literal)
        .iter()
        .any(|violation| {
            violation
                == "canonical literal \"file_list\" must occur once in production source, found 2"
        }));

    static STALE_TREE_NEXT: AtomicU64 = AtomicU64::new(0);
    let stale_tree_root = std::env::temp_dir().join(format!(
        "operation-architecture-stale-tree-{}-{}",
        std::process::id(),
        STALE_TREE_NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = fs::remove_dir_all(&stale_tree_root);
    let retired_tree = ["crates/sandbox-cli/src", "core"].join("/");
    fs::create_dir_all(stale_tree_root.join(&retired_tree))
        .expect("create empty stale tree fixture");
    let expected_violation = format!("forbidden legacy tree exists: {retired_tree}");
    assert!(
        validate_stale_facts(&stale_tree_root, &StaleFacts::default())
            .iter()
            .any(|violation| violation == &expected_violation)
    );
    fs::remove_dir_all(&stale_tree_root).expect("remove empty stale tree fixture");

    let maintained = "config/phase8-maintained.toml";
    facts.files.push(TrackedSource {
        path: maintained.to_owned(),
        content: RETIRED_E2E_TREE.to_owned(),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "config/phase8-protocol-ownership.toml".to_owned(),
        content: format!(
            "{} remains in {}",
            concat!("operation ", "vocabulary"),
            concat!("sandbox-", "protocol")
        ),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "dist/phase8-generated".to_owned(),
        content: String::new(),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: format!("{RETIRED_E2E_TREE}/phase8_fixture.py"),
        content: String::new(),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "phase8-near-historical.sh".to_owned(),
        content: format!(
            "#!/bin/sh\n# FROZEN HISTORICAL ARTIFACT (operation-layout exempt).\n{RETIRED_E2E_TREE}"
        ),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "phase8-exact-historical.sh".to_owned(),
        content: format!(
            "#!/bin/sh\n# FROZEN HISTORICAL ARTIFACT (operation-layout exempt, 2026-07-11).\n{RETIRED_E2E_TREE}"
        ),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "phase8-near-historical.html".to_owned(),
        content: format!(
            "<p><strong>Historical rendered artifact (operation-layout exempt, 2026-07-11):</strong>\n{RETIRED_E2E_TREE}"
        ),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "docs/obsidian/ephemeral-os/implementation_plan/operation-migration/spec.md.bak"
            .to_owned(),
        content: RETIRED_E2E_TREE.to_owned(),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "crates/sandbox-manager/src/phase8_grouped_import.rs".to_owned(),
        content: "use std::{process::{Command as Spawn}, net::{TcpListener as Listener}};"
            .to_owned(),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "crates/sandbox-provider-docker/src/phase8_manager_escape.rs".to_owned(),
        content: "use sandbox_manager::operations::SandboxManagerRouter;".to_owned(),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "crates/sandbox-observability/query/src/phase8_runtime_escape.rs".to_owned(),
        content: "use sandbox_runtime_layerstack::LayerStack;".to_owned(),
        executable: false,
    });
    facts.files.push(TrackedSource {
        path: "crates/sandbox-provider-docker/src/phase8_alias_noise.rs".to_owned(),
        content: "const NOTE: &str = \"use sandbox_manager as manager; manager::operations::SandboxManagerRouter; use sandbox_operation_contract::OperationSpec as Spec; Spec { name: x }\"; // use sandbox_manager as manager; manager::operations::SandboxManagerRouter; OperationSpec as Spec; Spec { name: x }".to_owned(),
        executable: false,
    });
    for (path, content) in [
        (
            "docs/phase8-bounded-exact.md",
            format!("# Record\n\n## Historical\n\n> **Historical decision record (operation-layout exempt, 2026-07-11):**\n{RETIRED_E2E_TREE}\n\n## Current\n\nCurrent guidance."),
        ),
        (
            "docs/phase8-bounded-resumes.md",
            format!("# Record\n\n## Historical\n\n> **Historical decision record (operation-layout exempt, 2026-07-11):**\nArchived.\n\n## Current\n\n{RETIRED_E2E_TREE}"),
        ),
        (
            "docs/phase8-bounded-near.md",
            format!("# Record\n\n## Historical\n\n> **Historical-ish decision record (operation-layout exempt, 2026-07-11):**\n{RETIRED_E2E_TREE}"),
        ),
        (
            "docs/phase8-mixed-current.md",
            format!("> **Ownership-layout note (operation-layout exempt, 2026-07-11):** Historical context.\n\n## Current\n\n{RETIRED_E2E_TREE}"),
        ),
        (
            "docs/phase8-completed-record.md",
            format!("> **Completed implementation record (operation-layout exempt, 2026-07-11):** Archived.\n\n{RETIRED_E2E_TREE}"),
        ),
        (
            "docs/phase8-wrapped-record.md",
            format!("> **Completed pre-migration implementation record (operation-layout exempt,\n> 2026-07-11):** Archived.\n\n{RETIRED_E2E_TREE}"),
        ),
        (
            "docs/phase8-experiment-specification.md",
            format!("> **Historical experiment specification (operation-layout exempt, 2026-07-11):** Archived.\n\n{RETIRED_E2E_TREE}"),
        ),
        (
            "docs/phase8-handoff.md",
            format!("> **Historical handoff (operation-layout exempt, 2026-07-11):** Archived.\n\n{RETIRED_E2E_TREE}"),
        ),
        (
            "docs/phase8-review-record.md",
            format!("> **Historical review record (operation-layout exempt, 2026-07-11):** Archived.\n\n{RETIRED_E2E_TREE}"),
        ),
        (
            "docs/phase8-landed-design-record.md",
            format!("> **Landed design record (operation-layout exempt, 2026-07-11):** Archived.\n\n{RETIRED_E2E_TREE}"),
        ),
        (
            "crates/sandbox-manager/tests/phase8_nonfirst.rs",
            "use std::{fmt, process::Child};".to_owned(),
        ),
        (
            "crates/sandbox-manager/examples/phase8_braced.rs",
            "use {std::process::Command as Spawn};".to_owned(),
        ),
        (
            "crates/sandbox-manager/build.rs",
            "use std as platform; fn main() { let _ = platform::net::TcpStream::connect; }"
                .to_owned(),
        ),
        (
            "crates/sandbox-manager/benches/phase8_external.rs",
            "use rustix::process::Pid; use nix::sys::socket::socket; use libc::fork; use socket2::Socket;"
                .to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_nix_sys_alias.rs",
            "use nix::sys as platform; use platform::socket::socket;".to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_nix_unistd_alias.rs",
            "use nix::{unistd as platform}; fn process() { let _ = platform::fork; }"
                .to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_std_alias_chain.rs",
            "use std as platform; use platform as platform_api; fn spawn() { let _ = platform_api::process::Command::new(\"true\"); }"
                .to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_tokio_alias_chain.rs",
            "use tokio as runtime; use runtime as runtime_api; fn connect() { let _ = runtime_api::net::TcpStream::connect; }"
                .to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_external_alias_chain.rs",
            "use socket2 as sockets; use sockets as socket_api; fn socket() { let _ = socket_api::Socket::new; }"
                .to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_nix_root_module_alias_chain.rs",
            "use nix as n; use n::sys as platform; use platform as platform_api; fn socket() { let _ = platform_api::socket::socket; }"
                .to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_adapter_alias_noise.rs",
            "const NOTE: &str = \"use std as platform; use platform as platform_api; platform_api::process::Command\"; // use nix as n; use n::sys as platform; platform::socket::socket"
                .to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_address_value_types.rs",
            "use std::net::IpAddr; use std::{net::{Ipv4Addr as V4, Ipv6Addr, SocketAddr as TcpStream, SocketAddrV4, SocketAddrV6 as V6}}; use std as platform; use platform as platform_api; fn addresses(value: IpAddr) { let _ = (value, V4::LOCALHOST, Ipv6Addr::LOCALHOST, TcpStream::from(([127, 0, 0, 1], 1)), SocketAddrV4::new(V4::LOCALHOST, 1), V6::new(Ipv6Addr::LOCALHOST, 1, 0, 0), platform_api::net::Ipv4Addr::UNSPECIFIED); }".to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_bare_net_alias_chain.rs",
            "use std as platform; use platform as platform_api; use platform_api::net as addresses; use addresses as address_api; fn address() { let _ = address_api::IpAddr::V4; }".to_owned(),
        ),
        (
            "crates/sandbox-manager/src/phase8_std_socket_alias_chain.rs",
            "use std as platform; use platform as platform_api; use platform_api::net::{SocketAddr as Address, TcpStream as Stream}; fn socket(address: Address) { let _ = (address, Stream::connect); }".to_owned(),
        ),
        (
            "crates/sandbox-provider-docker/tests/phase8_allowed_alias.rs",
            "use sandbox_manager::{SandboxRuntime as Runtime, self as manager}; use manager::SandboxId;"
                .to_owned(),
        ),
        (
            "crates/sandbox-provider-docker/tests/phase8_allowed_root_alias.rs",
            "use sandbox_manager as manager; use manager::SandboxId;".to_owned(),
        ),
        (
            "crates/sandbox-provider-docker/examples/phase8_forbidden_alias.rs",
            "extern crate sandbox_manager as manager; use manager::operations::SandboxManagerRouter;"
                .to_owned(),
        ),
        (
            "crates/sandbox-provider-docker/examples/phase8_forbidden_alias_chain.rs",
            "use sandbox_manager as manager; use manager as manager_api; use manager_api::operations::SandboxManagerRouter;"
                .to_owned(),
        ),
        (
            "crates/sandbox-provider-docker/examples/phase8_forbidden_root_reexport.rs",
            "pub use sandbox_manager as manager;".to_owned(),
        ),
        (
            "crates/sandbox-provider-docker/tests/phase8_protocol.rs",
            "use sandbox_protocol::ProtocolLimits;".to_owned(),
        ),
        (
            "crates/sandbox-observability/query/src/phase8_allowed_alias.rs",
            "use sandbox_runtime_layerstack::{service::StackObservation as Obs, self as layerstack}; use layerstack::LayerRef;"
                .to_owned(),
        ),
        (
            "crates/sandbox-observability/query/src/phase8_forbidden_alias.rs",
            "use sandbox_runtime_layerstack as layerstack; use layerstack::handler::LayerHandler;"
                .to_owned(),
        ),
        (
            "crates/sandbox-observability/query/src/phase8_forbidden_alias_chain.rs",
            "use sandbox_runtime_layerstack as layerstack; use layerstack as layerstack_api; use layerstack_api::handler::LayerHandler;"
                .to_owned(),
        ),
        (
            "crates/sandbox-observability/query/src/phase8_forbidden_root_reexport.rs",
            "pub use sandbox_runtime_layerstack as layerstack;".to_owned(),
        ),
        (
            "crates/phase8-semantic-alias/src/lib.rs",
            "use sandbox_operation_contract::OperationSpec as Spec; const X: Spec = Spec { name: \"x\" };"
                .to_owned(),
        ),
        (
            "crates/phase8-semantic-qualified/src/lib.rs",
            "use sandbox_operation_contract::OperationSpec; const X: OperationSpec = sandbox_operation_contract::OperationSpec { name: \"x\" };"
                .to_owned(),
        ),
        (
            "crates/phase8-semantic-type-alias/src/lib.rs",
            "type Spec = sandbox_operation_contract::OperationSpec; const X: Spec = Spec { name: \"x\" };"
                .to_owned(),
        ),
        (
            "crates/phase8-semantic-type-context/src/lib.rs",
            "fn existing() -> OperationSpec { EXISTING_SPEC } impl SomeTrait for OperationSpec {}"
                .to_owned(),
        ),
        (
            "crates/phase8-projection-alias/src/lib.rs",
            "use sandbox_cli::projection::OperationProjection as Projection; fn value() { let _ = Projection { name: \"x\" }; }"
                .to_owned(),
        ),
        (
            "crates/phase8-projection-type-alias/src/lib.rs",
            "type Projection = sandbox_cli::projection::OperationProjection; fn value() { let _ = Projection { name: \"x\" }; }"
                .to_owned(),
        ),
    ] {
        facts.files.push(TrackedSource {
            path: path.to_owned(),
            content,
            executable: false,
        });
    }
    let mut grouped_readiness = facts.clone();
    grouped_readiness
        .files
        .iter_mut()
        .find(|file| file.path == "crates/sandbox-provider-docker/src/readiness.rs")
        .expect("provider readiness source")
        .content = "use sandbox_protocol::daemon_readiness_request_line as request_line; fn request() { let _ = request_line; }".to_owned();
    assert!(!validate_stale_facts(&root, &grouped_readiness)
        .iter()
        .any(|violation| violation.contains("provider readiness protocol API")));

    facts
        .files
        .iter_mut()
        .find(|file| file.path == "crates/sandbox-provider-docker/src/readiness.rs")
        .expect("provider readiness source")
        .content = "use sandbox_protocol::{daemon_readiness_request_line as request_line, ProtocolLimits}; fn request() { let _ = (request_line, ProtocolLimits::default); }".to_owned();
    facts.files.push(TrackedSource {
        path: "crates/sandbox-operations/contract/src/phase8_fixture.rs".to_owned(),
        content:
            "pub struct OperationSpecDocument {}\npub struct OperationSpec {\n    pub cli: bool,\n}"
                .to_owned(),
        executable: false,
    });

    let violations = validate_stale_facts(&root, &facts);
    assert!(violations.iter().any(|violation| {
        violation
            == &format!(
                "stale path reference {RETIRED_E2E_TREE:?} remains in config/phase8-maintained.toml"
            )
    }));
    assert!(violations.iter().any(|violation| {
        violation
            == "stale protocol operation-vocabulary ownership remains in config/phase8-protocol-ownership.toml"
    }));
    assert!(violations
        .iter()
        .any(|violation| violation == "tracked generated or stale path dist/phase8-generated"));
    assert!(violations.iter().any(|violation| {
        violation == &format!("stale tracked path {RETIRED_E2E_TREE}/phase8_fixture.py")
    }));
    assert!(violations.iter().any(|violation| {
        violation
            == &format!(
                "stale path reference {RETIRED_E2E_TREE:?} remains in phase8-near-historical.sh"
            )
    }));
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("phase8-exact-historical.sh")));
    assert!(violations.iter().any(|violation| {
        violation.contains("stale path reference")
            && violation.contains("phase8-near-historical.html")
    }));
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("docs/observability-rework/cli-observability.html")));
    assert!(violations.iter().any(|violation| {
        violation.contains("stale path reference") && violation.contains("spec.md.bak")
    }));
    assert!(violations.iter().any(|violation| {
        violation.contains("manager owns forbidden process API")
            && violation.contains("phase8_grouped_import.rs")
    }));
    assert!(violations.iter().any(|violation| {
        violation
            .contains("provider imports forbidden manager API operations::SandboxManagerRouter")
            && violation.contains("phase8_manager_escape.rs")
    }));
    assert!(violations.iter().any(|violation| {
        violation.contains("observability application imports forbidden layerstack API LayerStack")
            && violation.contains("phase8_runtime_escape.rs")
    }));
    assert!(violations.iter().any(|violation| {
        violation.contains("provider readiness protocol API set")
            && violation.contains("ProtocolLimits")
    }));
    assert!(violations.iter().any(|violation| {
        violation
            == "contract OperationSpec retains CLI field cli in crates/sandbox-operations/contract/src/phase8_fixture.rs"
    }));
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("phase8-bounded-exact.md")));
    for path in [
        "phase8-bounded-resumes.md",
        "phase8-bounded-near.md",
        "phase8-mixed-current.md",
    ] {
        assert!(violations.iter().any(|violation| {
            violation.contains("stale path reference") && violation.contains(path)
        }));
    }
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("phase8-completed-record.md")));
    for path in [
        "phase8-wrapped-record.md",
        "phase8-experiment-specification.md",
        "phase8-handoff.md",
        "phase8-review-record.md",
        "phase8-landed-design-record.md",
    ] {
        assert!(!violations.iter().any(|violation| violation.contains(path)));
    }
    for path in [
        "phase8_nonfirst.rs",
        "phase8_braced.rs",
        "crates/sandbox-manager/build.rs",
        "phase8_external.rs",
        "phase8_nix_sys_alias.rs",
        "phase8_nix_unistd_alias.rs",
        "phase8_std_alias_chain.rs",
        "phase8_tokio_alias_chain.rs",
        "phase8_external_alias_chain.rs",
        "phase8_nix_root_module_alias_chain.rs",
        "phase8_bare_net_alias_chain.rs",
        "phase8_std_socket_alias_chain.rs",
    ] {
        assert!(violations.iter().any(|violation| {
            violation.contains("manager owns forbidden process API") && violation.contains(path)
        }));
    }
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("phase8_allowed_alias.rs")));
    assert!(!violations.iter().any(|violation| {
        violation.contains("crates/sandbox-observability/query/tests/query.rs")
    }));
    assert!(violations.iter().any(|violation| {
        violation.contains("provider imports forbidden manager API")
            && violation.contains("phase8_forbidden_alias.rs")
    }));
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("phase8_alias_noise.rs")));
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("phase8_adapter_alias_noise.rs")));
    let address_value_violations = violations
        .iter()
        .filter(|violation| violation.contains("phase8_address_value_types.rs"))
        .collect::<Vec<_>>();
    assert!(
        address_value_violations.is_empty(),
        "{address_value_violations:#?}"
    );
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("phase8_allowed_root_alias.rs")));
    assert!(violations.iter().any(|violation| {
        violation.contains("provider protocol usage escaped readiness.rs")
            && violation.contains("phase8_protocol.rs")
    }));
    assert!(violations.iter().any(|violation| {
        violation.contains("observability application imports forbidden layerstack API")
            && violation.contains("phase8_forbidden_alias.rs")
    }));
    for path in [
        "phase8_forbidden_alias_chain.rs",
        "phase8_forbidden_root_reexport.rs",
    ] {
        assert!(violations.iter().any(|violation| {
            violation.contains("observability application imports") && violation.contains(path)
        }));
    }
    for path in [
        "crates/sandbox-provider-docker/examples/phase8_forbidden_alias_chain.rs",
        "crates/sandbox-provider-docker/examples/phase8_forbidden_root_reexport.rs",
    ] {
        assert!(violations.iter().any(|violation| {
            violation.contains("provider imports") && violation.contains(path)
        }));
    }
    assert!(violations.iter().any(|violation| {
        violation.contains("semantic public operation declaration escaped")
            && violation.contains("phase8-semantic-alias")
    }));
    for path in ["phase8-semantic-qualified", "phase8-semantic-type-alias"] {
        assert!(violations.iter().any(|violation| {
            violation.contains("semantic public operation declaration escaped")
                && violation.contains(path)
        }));
    }
    assert!(!violations
        .iter()
        .any(|violation| violation.contains("phase8-semantic-type-context")));
    assert!(violations.iter().any(|violation| {
        violation.contains("CLI OperationProjection definition or value escaped")
            && violation.contains("phase8-projection-alias")
    }));
    assert!(violations.iter().any(|violation| {
        violation.contains("CLI OperationProjection definition or value escaped")
            && violation.contains("phase8-projection-type-alias")
    }));
}
