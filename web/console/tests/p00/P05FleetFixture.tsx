import { useMemo } from "react";
import { createRoot } from "react-dom/client";
import { Box, MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "../../src/index.css";
import { ephemeralosTheme } from "../../src/theme";
import { FleetBoard } from "../../src/pages/fleet/FleetBoard";

function Fixture() {
  const queryClient = useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }),
    [],
  );

  return (
    <MantineProvider forceColorScheme="light" theme={ephemeralosTheme}>
      <Notifications limit={4} position="bottom-right" />
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Box component="main" h="100%">
            <FleetBoard />
          </Box>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}

createRoot(document.getElementById("root")!).render(<Fixture />);
