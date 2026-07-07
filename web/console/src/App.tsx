import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { ToastProvider } from "@/components/ErrorToast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Shell } from "@/shell/Shell";
import { FleetBoard } from "@/pages/fleet/FleetBoard";
import { NotFound } from "@/pages/NotFound";
import { SandboxDetail } from "@/pages/sandbox/SandboxDetail";
import { OverviewTab } from "@/pages/sandbox/overview/OverviewTab";
import { TerminalTab } from "@/pages/sandbox/terminal/TerminalTab";
import { FilesTab } from "@/pages/sandbox/files/FilesTab";
import { PreviewTab } from "@/pages/sandbox/preview/PreviewTab";
import { ObservabilityTab } from "@/pages/sandbox/observability/ObservabilityTab";
import { ResourcesView } from "@/pages/sandbox/observability/ResourcesView";
import { TracesView } from "@/pages/sandbox/observability/TracesView";
import { EventsView } from "@/pages/sandbox/observability/EventsView";
import { LayerStackView } from "@/pages/sandbox/observability/LayerStackView";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false },
    mutations: { retry: false },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <TooltipProvider>
          <BrowserRouter>
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
          </BrowserRouter>
        </TooltipProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
