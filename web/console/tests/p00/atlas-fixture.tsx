import { useMemo } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Navigate, Route, Routes } from "react-router";
import "./atlas.css";
import { ToastProvider } from "../../src/components/ErrorToast";
import { TooltipProvider } from "../../src/components/ui/tooltip";
import { Shell } from "../../src/shell/Shell";
import { FleetBoard } from "../../src/pages/fleet/FleetBoard";
import { NotFound } from "../../src/pages/NotFound";
import { SandboxDetail } from "../../src/pages/sandbox/SandboxDetail";
import { OverviewTab } from "../../src/pages/sandbox/overview/OverviewTab";
import { TerminalTab } from "../../src/pages/sandbox/terminal/TerminalTab";
import { FilesTab } from "../../src/pages/sandbox/files/FilesTab";
import { PreviewTab } from "../../src/pages/sandbox/preview/PreviewTab";
import { ObservabilityTab } from "../../src/pages/sandbox/observability/ObservabilityTab";
import { ResourcesView } from "../../src/pages/sandbox/observability/ResourcesView";
import { TracesView } from "../../src/pages/sandbox/observability/TracesView";
import { EventsView } from "../../src/pages/sandbox/observability/EventsView";
import { LayerStackView } from "../../src/pages/sandbox/observability/LayerStackView";

const route = new URL(window.location.href).searchParams.get("route") ?? "/";

function AtlasApp() {
  const queryClient = useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }),
    [],
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={[route]}>
            <Routes>
              <Route element={<Shell />}>
                <Route index element={<FleetBoard />} />
                <Route path="sandboxes/:sandboxId" element={<SandboxDetail />}>
                  <Route index element={<OverviewTab />} />
                  <Route path="terminal" element={<TerminalTab />} />
                  <Route path="files" element={<FilesTab />} />
                  <Route path="layerstack" element={<LayerStackView />} />
                  <Route path="preview" element={<PreviewTab />} />
                  <Route path="observability" element={<ObservabilityTab />}>
                    <Route index element={<Navigate to="resources" replace />} />
                    <Route path="resources" element={<ResourcesView />} />
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
        </TooltipProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root")!).render(<AtlasApp />);
