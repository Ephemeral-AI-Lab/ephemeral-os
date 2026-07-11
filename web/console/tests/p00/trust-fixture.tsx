import { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Box, Grid, MantineProvider, Paper, Text, Title } from "@mantine/core";
import "@mantine/core/styles.css";
import { Notifications } from "@mantine/notifications";
import "@mantine/notifications/styles.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Outlet, Route, Routes } from "react-router";
import "./trust.css";
import { ephemeralosTheme } from "../../src/theme";
import { FileView } from "../../src/pages/sandbox/files/FileView";
import { EventsView } from "../../src/pages/sandbox/observability/EventsView";
import { CommandCard } from "../../src/pages/sandbox/terminal/CommandCard";
import type { LedgerEntry } from "../../src/pages/sandbox/terminal/ledger";

const SANDBOX_ID = "fixture-sandbox";

function SandboxOutlet() {
  return <Outlet context={{ sandboxId: SANDBOX_ID, record: null, snapshot: null, recordError: null }} />;
}

function RejectedCommand() {
  const [entry, setEntry] = useState<LedgerEntry>({
    localId: "fixture-rejected-command",
    commandSessionId: null,
    cmd: "publish --workspace release-candidate",
    workspaceSessionId: "workspace-fixture",
    autoPublish: false,
    startedAt: 1_700_000_000_000,
    status: "error",
    exitCode: 1,
    endedAt: 1_700_000_002_000,
    inlineOutput: "publication policy rejected this workspace change",
    publishRejected: true,
    publishRejectClass: "policy_denied",
  });

  return (
    <CommandCard
      sandboxId={SANDBOX_ID}
      entry={entry}
      expanded={false}
      onToggle={() => undefined}
      onUpdate={(patch) => setEntry((current) => ({ ...current, ...patch }))}
      previewScopes={[]}
    />
  );
}

function App() {
  const client = useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
    [],
  );

  return (
    <MantineProvider forceColorScheme="light" theme={ephemeralosTheme}>
      <Notifications limit={4} position="bottom-right" />
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/events"]}>
          <Box component="main" mih="100vh" bg="warm.0" p="md">
            <Box component="header" mb="md" pb="sm" style={{ borderBottom: "1px solid var(--mantine-color-neutral-3)" }}>
              <Text ff="monospace" size="xs" c="dimmed">P00 · sanitized deterministic fixture</Text>
              <Title order={1} size="h4">Trust-state evidence</Title>
            </Box>
            <Grid gutter="md">
              <Grid.Col span={{ base: 12, xl: 6 }}>
                <Paper withBorder mih={320} style={{ overflow: "hidden" }}>
                <Title order={2} size="sm" px="md" py="sm" style={{ borderBottom: "1px solid var(--mantine-color-neutral-3)" }}>Events — tail paused</Title>
                <Box h={256}>
                  <Routes>
                    <Route element={<SandboxOutlet />}>
                      <Route path="/events" element={<EventsView />} />
                    </Route>
                  </Routes>
                </Box>
                </Paper>
              </Grid.Col>
              <Grid.Col span={{ base: 12, xl: 6 }}>
                <Paper withBorder p="md">
                <Title order={2} size="sm" mb="sm">Terminal publication result</Title>
                <RejectedCommand />
                </Paper>
              </Grid.Col>
              <Grid.Col span={12}>
                <Paper withBorder mih={384} style={{ overflow: "hidden" }}>
                <Title order={2} size="sm" px="md" py="sm" style={{ borderBottom: "1px solid var(--mantine-color-neutral-3)" }}>Files — conflict retains local draft</Title>
                <Box h={320}>
                  <Routes>
                    <Route element={<SandboxOutlet />}>
                      <Route
                        path="/events"
                        element={<FileView sandboxId={SANDBOX_ID} path="notes/operator.txt" session={null} blameOn={false} />}
                      />
                    </Route>
                  </Routes>
                </Box>
                </Paper>
              </Grid.Col>
            </Grid>
          </Box>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
