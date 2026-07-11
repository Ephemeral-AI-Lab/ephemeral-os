import { useMemo } from "react";
import { createRoot } from "react-dom/client";
import { Box, MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Outlet, Route, Routes } from "react-router";
import type { SnapshotResult } from "../../src/api/observability";
import type { LedgerEntry } from "../../src/pages/sandbox/terminal/ledger";
import { TerminalTab } from "../../src/pages/sandbox/terminal/TerminalTab";
import { ephemeralosTheme } from "../../src/theme";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "../../src/index.css";

const sandboxId = "terminal-fixture";
const large = new URL(window.location.href).searchParams.has("large");

const snapshot: SnapshotResult = {
  sandboxes: [
    {
      sandbox_id: sandboxId,
      lifecycle_state: "ready",
      availability: "available",
      sampled_at_unix_ms: 1_700_000_000_000,
      errors: [],
      daemon: { daemon_pid: 42, runtime_dir: "/fixture/runtime" },
      resources: { latest: null, history: [] },
      workspaces: [
        {
          workspace_id: "workspace-alpha",
          lifecycle_state: "running",
          network_profile: "shared",
          layers: { base_root_hash: "fixture-base", layer_count: 2 },
          namespace_fd_count: 2,
          resources: { latest: null, history: [] },
          active_namespace_executions: [
            {
              namespace_execution_id: large ? "large-command" : "running-command",
              operation: "exec_command",
              lifecycle_state: "running",
            },
          ],
        },
        {
          workspace_id: "workspace-beta",
          lifecycle_state: "running",
          network_profile: "isolated",
          layers: { base_root_hash: "fixture-base", layer_count: 4 },
          namespace_fd_count: 1,
          resources: { latest: null, history: [] },
          active_namespace_executions: [],
        },
      ],
      stack: { layer_count: 4, layers_bytes: 2_000_000, active_leases: 1 },
    },
  ],
};

function entry(partial: Partial<LedgerEntry> & Pick<LedgerEntry, "localId" | "cmd">): LedgerEntry {
  return {
    commandSessionId: null,
    workspaceSessionId: null,
    autoPublish: false,
    startedAt: 1_700_000_000_000,
    status: "ok",
    exitCode: 0,
    endedAt: 1_700_000_001_000,
    inlineOutput: null,
    publishRejected: false,
    publishRejectClass: null,
    ...partial,
  };
}

const entries: LedgerEntry[] = large
  ? [
      entry({
        localId: "large-local",
        commandSessionId: "large-command",
        cmd: "generate 10,000 diagnostic lines",
        workspaceSessionId: "workspace-alpha",
        status: "running",
        exitCode: null,
        endedAt: null,
      }),
    ]
  : [
      entry({
        localId: "running-local",
        commandSessionId: "running-command",
        cmd: "tail -f /var/log/fixture.log",
        workspaceSessionId: "workspace-alpha",
        status: "running",
        exitCode: null,
        endedAt: null,
      }),
      entry({
        localId: "rejected-local",
        commandSessionId: "rejected-command",
        cmd: "write protected output",
        workspaceSessionId: "workspace-alpha",
        publishRejected: true,
        publishRejectClass: "protected_path",
      }),
      entry({
        localId: "completed-local",
        cmd: "echo completed elsewhere",
        workspaceSessionId: "workspace-beta",
      }),
    ];

localStorage.setItem(`eos-console:ledger:${sandboxId}`, JSON.stringify(entries));

function TerminalContext() {
  return (
    <Outlet
      context={{
        sandboxId,
        record: null,
        snapshot,
        recordError: null,
      }}
    />
  );
}

function Fixture() {
  const queryClient = useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }),
    [],
  );
  const commandSessionId = large ? "large-command" : "running-command";

  return (
    <MantineProvider forceColorScheme="light" theme={ephemeralosTheme}>
      <Notifications limit={4} position="bottom-right" />
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/terminal#cmd-${commandSessionId}`]}>
          <Box component="main" h="100%">
            <Routes>
              <Route path="/terminal" element={<TerminalContext />}>
                <Route index element={<TerminalTab />} />
              </Route>
            </Routes>
          </Box>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
