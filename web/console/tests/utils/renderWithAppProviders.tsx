import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { AppProviders } from "@/AppProviders";

export function renderWithAppProviders(
  ui: ReactElement,
  { initialEntries = ["/"], ...options }: RenderOptions & { initialEntries?: string[] } = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <AppProviders>
        <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
      </AppProviders>
    );
  }

  return render(ui, { wrapper: Wrapper, ...options });
}
