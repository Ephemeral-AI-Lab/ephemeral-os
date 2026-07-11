import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ToastProvider } from "@/components/ErrorToast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ephemeralosTheme } from "@/theme";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false },
    mutations: { retry: false },
  },
});

/**
 * The sole application Mantine boundary. Legacy feedback providers remain here
 * until their consumers move in P03 and are removed in P09.
 */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <MantineProvider forceColorScheme="light" theme={ephemeralosTheme}>
      <Notifications position="bottom-right" />
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <TooltipProvider>{children}</TooltipProvider>
        </ToastProvider>
      </QueryClientProvider>
    </MantineProvider>
  );
}
