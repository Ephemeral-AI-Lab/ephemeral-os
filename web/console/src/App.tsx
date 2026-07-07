import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";
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
import { PlaceholderTab } from "@/pages/sandbox/PlaceholderTab";

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
                  <Route path="preview" element={<PreviewTab />} />
                  <Route path="observability">
                    <Route
                      index
                      element={
                        <PlaceholderTab title="Observability · Resources" phase="Phase 8" />
                      }
                    />
                    <Route
                      path="resources"
                      element={
                        <PlaceholderTab title="Observability · Resources" phase="Phase 8" />
                      }
                    />
                    <Route
                      path="traces"
                      element={
                        <PlaceholderTab title="Observability · Traces" phase="Phase 8" />
                      }
                    />
                    <Route
                      path="traces/:traceId"
                      element={
                        <PlaceholderTab title="Observability · Trace" phase="Phase 8" />
                      }
                    />
                    <Route
                      path="events"
                      element={
                        <PlaceholderTab title="Observability · Events" phase="Phase 8" />
                      }
                    />
                    <Route
                      path="layerstack"
                      element={
                        <PlaceholderTab title="Observability · LayerStack" phase="Phase 8" />
                      }
                    />
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
