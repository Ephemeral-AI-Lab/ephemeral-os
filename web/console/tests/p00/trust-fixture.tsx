import { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { MantineProvider } from "@mantine/core";
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
          <main className="min-h-screen bg-app p-4 text-ink">
            <header className="mb-4 border-b border-line pb-3">
              <p className="font-mono text-xs text-ink-faint">P00 · sanitized deterministic fixture</p>
              <h1 className="text-lg font-semibold">Trust-state evidence</h1>
            </header>
            <div className="grid gap-4 xl:grid-cols-2">
              <section className="min-h-80 overflow-hidden rounded-md border border-line bg-surface">
                <h2 className="border-b border-line px-3 py-2 text-sm font-semibold">Events — tail paused</h2>
                <div className="h-64">
                  <Routes>
                    <Route element={<SandboxOutlet />}>
                      <Route path="/events" element={<EventsView />} />
                    </Route>
                  </Routes>
                </div>
              </section>
              <section className="rounded-md border border-line bg-surface p-3">
                <h2 className="mb-2 text-sm font-semibold">Terminal publication result</h2>
                <RejectedCommand />
              </section>
              <section className="min-h-96 overflow-hidden rounded-md border border-line bg-surface xl:col-span-2">
                <h2 className="border-b border-line px-3 py-2 text-sm font-semibold">Files — conflict retains local draft</h2>
                <div className="h-80">
                  <Routes>
                    <Route element={<SandboxOutlet />}>
                      <Route
                        path="/events"
                        element={<FileView sandboxId={SANDBOX_ID} path="notes/operator.txt" session={null} blameOn={false} />}
                      />
                    </Route>
                  </Routes>
                </div>
              </section>
            </div>
          </main>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
