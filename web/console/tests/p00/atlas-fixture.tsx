import { useMemo } from "react";
import { createRoot } from "react-dom/client";
import { MantineProvider } from "@mantine/core";
import "@mantine/core/styles.css";
import { Notifications } from "@mantine/notifications";
import "@mantine/notifications/styles.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Navigate, Route, Routes, useLocation } from "react-router";
import "./atlas.css";
import { ephemeralosTheme } from "../../src/theme";
import { LegacyLayerStackRedirect } from "../../src/App";
import { Shell } from "../../src/shell/Shell";
import { FleetBoard } from "../../src/pages/fleet/FleetBoard";
import { NotFound } from "../../src/pages/NotFound";
import { SandboxDetail } from "../../src/pages/sandbox/SandboxDetail";
import { TerminalTab } from "../../src/pages/sandbox/terminal/TerminalTab";
import { FilesTab } from "../../src/pages/sandbox/files/FilesTab";
import { PreviewTab } from "../../src/pages/sandbox/preview/PreviewTab";
import { ObservabilityTab } from "../../src/pages/sandbox/observability/ObservabilityTab";
import { ResourcesView } from "../../src/pages/sandbox/observability/ResourcesView";
import { CgroupView } from "../../src/pages/sandbox/observability/CgroupView";
import { TracesView } from "../../src/pages/sandbox/observability/TracesView";
import { EventsView } from "../../src/pages/sandbox/observability/EventsView";
import { LayerStackView } from "../../src/pages/sandbox/observability/LayerStackView";

const route = new URL(window.location.href).searchParams.get("route") ?? "/";

function RouteProbe() {
  const location = useLocation();
  return <output data-atlas-location hidden>{`${location.pathname}${location.search}${location.hash}`}</output>;
}

function AtlasApp() {
  const queryClient = useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }),
    [],
  );

  return (
    <MantineProvider forceColorScheme="light" theme={ephemeralosTheme}>
      <Notifications limit={4} position="bottom-right" />
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          <RouteProbe />
          <Routes>
            <Route element={<Shell />}>
              <Route index element={<FleetBoard />} />
              <Route path="sandboxes/:sandboxId" element={<SandboxDetail />}>
                <Route index element={<Navigate to="terminal" replace />} />
                <Route path="terminal" element={<TerminalTab />} />
                <Route path="files" element={<FilesTab />} />
                <Route path="layerstack" element={<LegacyLayerStackRedirect />} />
                <Route path="preview" element={<PreviewTab />} />
                <Route path="observability" element={<ObservabilityTab />}>
                  <Route index element={<Navigate to="resources" replace />} />
                  <Route path="resources" element={<ResourcesView />} />
                  <Route path="cgroup" element={<CgroupView />} />
                  <Route path="traces" element={<TracesView />} />
                  <Route path="traces/:traceId" element={<TracesView />} />
                  <Route path="events" element={<EventsView />} />
                  <Route path="layerstack" element={<LayerStackView />} />
                </Route>
              </Route>
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}

createRoot(document.getElementById("root")!).render(<AtlasApp />);
