import { NavLink, Outlet } from "react-router";

const VIEWS = [
  { path: "resources", label: "Resources" },
  { path: "traces", label: "Traces" },
  { path: "events", label: "Events" },
  { path: "layerstack", label: "LayerStack" },
];

/**
 * Observability sub-navigation, mirroring the observability catalog's four
 * per-sandbox views. Sub-tabs are real routed links; bare `/observability`
 * redirects into `resources` via the index route.
 */
export function ObservabilityTab() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <nav className="flex gap-1 border-b border-line bg-surface px-4 pt-1.5">
        {VIEWS.map((view) => (
          <NavLink
            key={view.path}
            to={view.path}
            className={({ isActive }) =>
              `border-b-2 px-3 pb-1.5 pt-0.5 text-xs ${
                isActive
                  ? "border-accent font-medium text-accent"
                  : "border-transparent text-ink-mid hover:text-ink"
              }`
            }
          >
            {view.label}
          </NavLink>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  );
}
