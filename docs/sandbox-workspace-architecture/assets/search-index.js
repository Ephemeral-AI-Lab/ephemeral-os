window.SANDBOX_WORKSPACE_DOC_SEARCH = [
  {'id': 'overview', 'title': '1. Overview', 'href': 'overview.html', 'text': 'Many agents and plugins inspect or mutate one repository while sharing a single durable workspace history.'},
  {'id': 'layerstack', 'title': '2. LayerStack', 'href': 'layerstack.html', 'text': 'Stable snapshots, immutable layer directories, projection rendering, publish flow, leases, and squash behavior.'},
  {'id': 'overlay', 'title': '3. Overlay', 'href': 'overlay.html', 'text': 'How LayerStack paths become a normal filesystem view with private upper/work directories.'},
  {'id': 'occ', 'title': '4. OCC', 'href': 'occ.html', 'text': 'Typed changes, routing, base-hash validation, commit queue, staging, transaction, and publish semantics.'},
  {'id': 'workspaces', 'title': '5. Workspace Modes', 'href': 'workspaces.html', 'text': 'main_workspace, ephemeral_workspace, isolated_workspace, fast paths, and enter/exit lifecycle details.'},
  {'id': 'daemon', 'title': '6. Daemon and Background Tasks', 'href': 'daemon.html', 'text': 'Resident daemon control plane, request lifecycle, operation table, transport, workspace routing, caches, background heartbeats, cancellation, TTL, isolated gating, and diagnostics.'},
  {'id': 'provider', 'title': '7. Provider', 'href': 'provider.html', 'text': 'Provider-neutral sandbox lifecycle, Docker/Daytona adapters, daemon transport, and runtime bootstrap boundary.'},
  {'id': 'plugins', 'title': '8. Plugin in ephemeral_workspace', 'href': 'plugins.html', 'text': 'Plugin runtime ensure, operation registry, projection APIs, write overlays, Pyright refresh, and LSP apply.'},
  {'id': 'space-model', 'title': '9. Space Model', 'href': 'space-model.html', 'text': 'O(1) lowerdir repository bytes and O(n * per-operation changed bytes) writable scratch for concurrent work.'},
  {'id': 'workflow-cookbook', 'title': '10. Workflow Cookbook', 'href': 'workflow-cookbook.html', 'text': 'End-to-end traces for direct reads, ephemeral shell, OCC publish, isolated lifecycle, plugins, and Pyright refresh.'}
];
