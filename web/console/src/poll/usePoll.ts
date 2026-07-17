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

export interface PollTracking {
  at: number;
  fingerprint: string;
}

interface PollOptions<T> {
  key: QueryKey;
  fn: (signal?: AbortSignal) => Promise<T>;
  mode?: PollMode | ((data: T | undefined) => PollMode);
  enabled?: boolean | ((data: T | undefined) => boolean);
  retry?: boolean;
  refetchOnWindowFocus?: boolean | "always";
}

export function pollInterval<T>(
  data: T | undefined,
  mode: PollMode,
  tracking: PollTracking,
  now = Date.now(),
  hidden = typeof document !== "undefined" && document.hidden,
): number | false {
  if (hidden) return false;
  const base = mode === "fast" ? FAST_POLL_MS : SLOW_POLL_MS;
  const fingerprint = data === undefined ? "" : JSON.stringify(data);
  if (fingerprint !== tracking.fingerprint) {
    tracking.fingerprint = fingerprint;
    tracking.at = now;
    return base;
  }
  if (now - tracking.at > IDLE_AFTER_MS) {
    return Math.min(base * IDLE_MULTIPLIER, MAX_IDLE_MS);
  }
  return base;
}

/**
 * The console's PollController: TanStack Query polling with a fast cadence
 * for visible running surfaces, a slow one for background panes, idle decay
 * with instant recovery when the payload changes, a pause while the tab is
 * hidden, and an immediate catch-up refetch on return (window focus).
 * Interaction nudges go through `useNudge`.
 */
export function usePoll<T>(options: PollOptions<T>) {
  const modeFor = (data: T | undefined): PollMode =>
    typeof options.mode === "function"
      ? options.mode(data)
      : (options.mode ?? "slow");
  const enabledFor = typeof options.enabled === "function" ? options.enabled : null;
  const changeRef = useRef<PollTracking>({ at: Date.now(), fingerprint: "" });
  return useQuery<T, Error, T, QueryKey>({
    queryKey: options.key,
    queryFn: ({ signal }) => options.fn(signal),
    enabled:
      enabledFor
        ? (query) => enabledFor(query.state.data as T | undefined)
        : typeof options.enabled === "boolean"
          ? options.enabled
          : true,
    retry: options.retry ?? false,
    placeholderData: keepPreviousData,
    refetchOnWindowFocus: options.refetchOnWindowFocus ?? "always",
    refetchIntervalInBackground: false,
    refetchInterval: (query) =>
      pollInterval(query.state.data, modeFor(query.state.data), changeRef.current),
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
