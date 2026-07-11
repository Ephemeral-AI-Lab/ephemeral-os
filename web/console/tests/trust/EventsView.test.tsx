import { fireEvent, screen } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchEvents: vi.fn(),
  usePoll: vi.fn(),
}));

vi.mock("@/api/observability", () => ({
  fetchEvents: mocks.fetchEvents,
}));

vi.mock("@/poll/usePoll", () => ({
  usePoll: mocks.usePoll,
}));

vi.mock("@/pages/sandbox/SandboxContext", () => ({
  useSandbox: () => ({ sandboxId: "sandbox-a" }),
}));

vi.mock("@/pages/sandbox/observability/TracesView", () => ({
  TraceCell: ({ traceId }: { traceId: string }) => <span>{traceId}</span>,
}));

import { EventsView } from "@/pages/sandbox/observability/EventsView";
import { renderWithAppProviders } from "../utils/renderWithAppProviders";

describe("EventsView polling contract", () => {
  let pollOptions: {
    fn: () => Promise<unknown>;
    mode?: string;
    enabled?: boolean;
  };

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-11T00:00:00.000Z"));
    mocks.fetchEvents.mockResolvedValue({ events: [] });
    mocks.usePoll.mockImplementation((options) => {
      pollOptions = options;
      return { data: { events: [] } };
    });
  });

  it("converts the selected duration into the API's absolute unix-ms threshold", async () => {
    renderWithAppProviders(
      <Routes>
        <Route path="/events" element={<EventsView />} />
      </Routes>,
      { initialEntries: ["/events?since=300000"] },
    );

    await pollOptions.fn();

    expect(mocks.fetchEvents).toHaveBeenCalledWith("sandbox-a", {
      name: undefined,
      sinceMs: Date.now() - 300_000,
      lastN: 200,
    });
  });

  it("stops polling when the operator pauses the live tail", () => {
    renderWithAppProviders(
      <Routes>
        <Route path="/events" element={<EventsView />} />
      </Routes>,
      { initialEntries: ["/events"] },
    );

    fireEvent.click(screen.getByRole("button", { name: "tail" }));

    expect(pollOptions.mode).toBe("slow");
    expect(pollOptions.enabled).toBe(false);
  });
});
