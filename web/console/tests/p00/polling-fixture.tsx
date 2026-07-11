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

  return (
    <>
      <output
        data-testid={`poll-${mode}`}
        style={{ display: "inline-block", minWidth: "7ch" }}
      >
        {query.data?.revision ?? "loading"}
      </output>
      <button
        aria-label={`Refetch ${mode} poll`}
        data-testid={`poll-${mode}-refetch`}
        onClick={() => void query.refetch({ cancelRefetch: true })}
        type="button"
      >
        Refetch
      </button>
    </>
  );
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
        <p><PollProbe mode="fast" /></p>
        <p><PollProbe mode="slow" /></p>
      </main>
    </QueryClientProvider>
  );
}

export default App;

createRoot(document.getElementById("root")!).render(<App />);
