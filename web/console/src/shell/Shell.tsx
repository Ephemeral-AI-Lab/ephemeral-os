import { useState } from "react";
import {
  Anchor,
  AppShell,
  Box,
  Breadcrumbs,
  Burger,
  Button,
  Drawer,
  Group,
  Image,
  Stack,
  Text,
} from "@mantine/core";
import { Link, Outlet, useLocation } from "react-router";

const TAB_LABELS: Record<string, string> = {
  terminal: "Terminal",
  files: "Files",
  observability: "Observability",
  preview: "Preview",
};

const OBSERVABILITY_LABELS: Record<string, string> = {
  resources: "Resources",
  cgroup: "Processes",
  traces: "Traces",
  events: "Events",
  layerstack: "Layers",
};

type Crumb = { label: string; to?: string };
type NavigationItem = { label: string; to: string; active: boolean };

function decodePathSegment(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function crumbs(pathname: string): Crumb[] {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] !== "sandboxes" || !parts[1]) return [];

  const sandboxPath = `/sandboxes/${parts[1]}`;
  const result: Crumb[] = [
    { label: "Fleet", to: "/" },
    { label: decodePathSegment(parts[1]), to: sandboxPath },
  ];
  const tab = parts[2];
  if (!tab) return [...result, { label: "Terminal" }];
  if (TAB_LABELS[tab]) result.push({ label: TAB_LABELS[tab] });
  if (tab === "observability" && parts[3] && OBSERVABILITY_LABELS[parts[3]]) {
    result.push({ label: OBSERVABILITY_LABELS[parts[3]] });
  }
  return result;
}

function navigationItems(pathname: string): NavigationItem[] {
  const parts = pathname.split("/").filter(Boolean);
  const fleet: NavigationItem = { label: "Fleet", to: "/", active: pathname === "/" };
  if (parts[0] !== "sandboxes" || !parts[1]) return [fleet];

  const base = `/sandboxes/${parts[1]}`;
  const item = (label: string, suffix = "", nested = false): NavigationItem => {
    const to = suffix ? `${base}/${suffix}` : base;
    const active = nested
      ? pathname === to
      : suffix === "observability"
        ? pathname === to || pathname.startsWith(`${to}/`)
        : pathname === to;
    return { label: nested ? `↳ ${label}` : label, to, active };
  };

  return [
    fleet,
    item("Terminal", "terminal"),
    item("Files", "files"),
    item("Observability", "observability"),
    item("Resources", "observability/resources", true),
    item("Processes", "observability/cgroup", true),
    item("Traces", "observability/traces", true),
    item("Events", "observability/events", true),
    item("Layers", "observability/layerstack", true),
    item("Preview", "preview"),
  ];
}

export function Shell() {
  const location = useLocation();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const trail = crumbs(location.pathname);
  const items = navigationItems(location.pathname);
  const activeIndex = Math.max(0, items.reduce((active, item, index) => item.active ? index : active, -1));

  const focusActiveNavigation = () => {
    [...document.querySelectorAll<HTMLElement>("[data-shell-active-navigation]")]
      .find((element) => element.getClientRects().length > 0)
      ?.focus();
  };

  return (
    <AppShell data-console-shell header={{ height: 44 }} padding={0}>
      <Anchor href="#main-content" id="skip-link">
        Skip to main content
      </Anchor>
      <AppShell.Header>
        <Group h="100%" px="md" gap="sm" wrap="nowrap">
          <Burger
            aria-label={navigationOpen ? "Close navigation" : "Open navigation"}
            hiddenFrom="sm"
            onClick={() => setNavigationOpen((opened) => !opened)}
            opened={navigationOpen}
            size="sm"
          />
          <Anchor
            aria-label="EphemeralOS fleet"
            component={Link}
            to="/"
            underline="never"
          >
            <Group gap={6} wrap="nowrap">
              <Image alt="" h={18} src="/assets/images/logo.png" w={18} />
              <Text c="dark" fw={700} size="sm">
                EphemeralOS
              </Text>
            </Group>
          </Anchor>
          {trail.length > 0 ? (
            <Box data-console-breadcrumbs visibleFrom="sm">
              <Breadcrumbs separator="›">
                {trail.map((crumb) =>
                  crumb.to ? (
                    <Anchor component={Link} key={`${crumb.label}-${crumb.to}`} size="sm" to={crumb.to}>
                      {crumb.label}
                    </Anchor>
                  ) : (
                    <Text c="dimmed" key={crumb.label} size="sm">
                      {crumb.label}
                    </Text>
                  ),
                )}
              </Breadcrumbs>
            </Box>
          ) : null}
        </Group>
      </AppShell.Header>

      <Drawer
        onClose={() => setNavigationOpen(false)}
        onEnterTransitionEnd={focusActiveNavigation}
        opened={navigationOpen}
        position="left"
        size="xs"
        title="Navigation"
      >
        <nav aria-label="Primary navigation">
          <Stack gap="xs">
            {items.map((item, index) => (
              <Button
                component={Link}
                data-shell-active-navigation={index === activeIndex || undefined}
                justify="flex-start"
                key={item.to}
                onClick={() => setNavigationOpen(false)}
                to={item.to}
                variant={item.active ? "filled" : "subtle"}
              >
                {item.label}
              </Button>
            ))}
          </Stack>
        </nav>
      </Drawer>

      <AppShell.Main data-console-main id="main-content" tabIndex={-1}>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
