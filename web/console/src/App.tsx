import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router";
import { AppProviders } from "@/AppProviders";
import { Shell } from "@/shell/Shell";
import { FleetBoard } from "@/pages/fleet/FleetBoard";
import { NotFound } from "@/pages/NotFound";
import { SandboxDetail } from "@/pages/sandbox/SandboxDetail";
import { TerminalTab } from "@/pages/sandbox/terminal/TerminalTab";
import { FilesTab } from "@/pages/sandbox/files/FilesTab";
import { PreviewTab } from "@/pages/sandbox/preview/PreviewTab";
import { ObservabilityTab } from "@/pages/sandbox/observability/ObservabilityTab";
import { ResourcesView } from "@/pages/sandbox/observability/ResourcesView";
import { CgroupView } from "@/pages/sandbox/observability/CgroupView";
import { TracesView } from "@/pages/sandbox/observability/TracesView";
import { EventsView } from "@/pages/sandbox/observability/EventsView";
import { LayerStackView } from "@/pages/sandbox/observability/LayerStackView";

export function LegacyLayerStackRedirect() {
  const { sandboxId = "" } = useParams();
  const location = useLocation();

  return (
    <Navigate
      replace
      to={{
        pathname: `/sandboxes/${encodeURIComponent(sandboxId)}/observability/layerstack`,
        search: location.search,
        hash: location.hash,
      }}
    />
  );
}

export default function App() {
  return (
    <AppProviders>
      <BrowserRouter>
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
      </BrowserRouter>
    </AppProviders>
  );
}
