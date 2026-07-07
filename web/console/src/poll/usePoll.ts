import { useRef } from "react";
import {
  keepPreviousData,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";

export const FAST_POLL_MS = 400;
export const SLOW_POLL_MS = 2000;
const IDLE_AFTER_MS = 15_000;
const IDLE_MULTIPLIER = 4;
const MAX_IDLE_MS = 8000;

export type PollMode = "fast" | "slow";

interface PollOptions<T> {
  key: QueryKey;
  fn: () => Promise<T>;
  mode?: PollMode;
  enabled?: boolean;
  retry?: boolean;
}

/**
 * The console's PollController: TanStack Query polling with a fast cadence
 * for visible running surfaces, a slow one for background panes, idle decay
 * with instant recovery when the payload changes, a pause while the tab is
 * hidden, and an immediate catch-up refetch on return (window focus).
 * Interaction nudges go through `useNudge`.
 */
export function usePoll<T>(options: PollOptions<T>) {
  const base = options.mode === "fast" ? FAST_POLL_MS : SLOW_POLL_MS;
  const changeRef = useRef({ at: Date.now(), fingerprint: "" });
  return useQuery({
    queryKey: options.key,
    queryFn: options.fn,
    enabled: options.enabled ?? true,
    retry: options.retry ?? false,
    placeholderData: keepPreviousData,
    refetchOnWindowFocus: "always",
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      if (typeof document !== "undefined" && document.hidden) return false;
      const data = query.state.data;
      const fingerprint = data === undefined ? "" : JSON.stringify(data);
      const tracked = changeRef.current;
      if (fingerprint !== tracked.fingerprint) {
        tracked.fingerprint = fingerprint;
        tracked.at = Date.now();
        return base;
      }
      if (Date.now() - tracked.at > IDLE_AFTER_MS) {
        return Math.min(base * IDLE_MULTIPLIER, MAX_IDLE_MS);
      }
      return base;
    },
  });
}

/**
 * An interaction nudge: fire an immediate refetch of the given query keys
 * (used right after exec_command / write_command_stdin so the reaction
 * renders without waiting for the next tick).
 */
export function useNudge() {
  const queryClient = useQueryClient();
  return (key: QueryKey) => {
    void queryClient.invalidateQueries({ queryKey: key });
  };
}
