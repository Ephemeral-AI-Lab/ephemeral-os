import { useMemo } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { usePoll, type PollMode } from "../../src/poll/usePoll";

interface PollResponse {
  revision: number;
}

function PollProbe({ mode }: { mode: PollMode }) {
  const query = usePoll<PollResponse>({
    key: ["p00", "polling", mode],
    fn: async () => {
      const response = await fetch(`/p00-polling-data?mode=${mode}`);
      if (!response.ok) throw new Error(`poll fixture failed: ${response.status}`);
      return response.json() as Promise<PollResponse>;
    },
    mode,
  });

  return <output data-testid={`poll-${mode}`}>{query.data?.revision ?? "loading"}</output>;
}

function App() {
  const client = useMemo(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false } },
      }),
    [],
  );

  return (
    <QueryClientProvider client={client}>
      <main>
        <h1>P00 polling fixture</h1>
        <PollProbe mode="fast" />
        <PollProbe mode="slow" />
      </main>
    </QueryClientProvider>
  );
}

export default App;

createRoot(document.getElementById("root")!).render(<App />);
