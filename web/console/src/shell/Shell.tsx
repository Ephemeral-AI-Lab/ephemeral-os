import { Link, Outlet, useLocation } from "react-router";
import { Boxes } from "lucide-react";

const TAB_LABELS: Record<string, string> = {
  terminal: "Terminal",
  files: "Files",
  observability: "Observability",
  preview: "Preview",
};

function crumbs(pathname: string): { label: string; to?: string }[] {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] !== "sandboxes" || !parts[1]) return [];
  const sandboxId = decodeURIComponent(parts[1]);
  const result: { label: string; to?: string }[] = [
    { label: sandboxId, to: `/sandboxes/${parts[1]}` },
  ];
  const tab = parts[2];
  if (tab && TAB_LABELS[tab]) {
    result.push({ label: TAB_LABELS[tab] });
  } else if (!tab) {
    result.push({ label: "Overview" });
  }
  return result;
}

export function Shell() {
  const location = useLocation();
  const trail = crumbs(location.pathname);

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-line bg-surface px-4">
        <Link
          to="/"
          className="flex items-center gap-1.5 font-semibold text-ink hover:text-accent"
        >
          <Boxes size={16} className="text-accent" />
          EphemeralOS
        </Link>
        {trail.length > 0 ? (
          <nav className="flex items-center gap-2 text-[13px] text-ink-mid">
            <span className="text-ink-faint">/</span>
            {trail.map((crumb, index) => (
              <span key={index} className="flex items-center gap-2">
                {index > 0 ? <span className="text-ink-faint">/</span> : null}
                {crumb.to ? (
                  <Link
                    to={crumb.to}
                    className="font-mono text-ink-mid hover:text-accent"
                  >
                    {crumb.label}
                  </Link>
                ) : (
                  <span>{crumb.label}</span>
                )}
              </span>
            ))}
          </nav>
        ) : null}
      </header>
      <main className="flex min-h-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  );
}
