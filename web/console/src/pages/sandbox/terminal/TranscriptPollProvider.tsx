import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";

const FAST_TAIL_MS = 400;
const SETTLED_TAIL_MS = 2_000;
const MAX_POLLS_PER_TICK = 4;

type Poll = () => Promise<void> | void;

interface TranscriptPollContextValue {
  register: (key: string, poll: Poll) => () => void;
}

const TranscriptPollContext = createContext<TranscriptPollContextValue | null>(null);

/**
 * One timer coordinates every expanded, running transcript in this Terminal
 * route. Each tick services at most four entries in round-robin order so a
 * large ledger cannot create an unbounded set of intervals or RPC bursts.
 */
export function TranscriptPollProvider({ children }: { children: ReactNode }) {
  const pollersRef = useRef(new Map<string, Poll>());
  const timerRef = useRef<number | null>(null);
  const pollingRef = useRef(false);
  const cursorRef = useRef(0);

  const runPoll = useCallback(async () => {
    if (pollingRef.current) return;
    const pollers = [...pollersRef.current.values()];
    if (pollers.length === 0) return;

    pollingRef.current = true;
    const count = Math.min(MAX_POLLS_PER_TICK, pollers.length);
    const start = cursorRef.current % pollers.length;
    const selected = Array.from({ length: count }, (_, index) => pollers[(start + index) % pollers.length]);
    cursorRef.current = (start + count) % pollers.length;
    try {
      await Promise.all(selected.map((poll) => poll()));
    } finally {
      pollingRef.current = false;
    }
  }, []);

  const syncTimer = useCallback((reset = false) => {
    if (reset && timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (pollersRef.current.size === 0) {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
      timerRef.current = null;
      return;
    }
    if (timerRef.current === null) {
      timerRef.current = window.setInterval(
        () => void runPoll(),
        document.hidden ? SETTLED_TAIL_MS : FAST_TAIL_MS,
      );
    }
  }, [runPoll]);

  const register = useCallback(
    (key: string, poll: Poll) => {
      pollersRef.current.set(key, poll);
      syncTimer();
      void runPoll();
      return () => {
        pollersRef.current.delete(key);
        syncTimer();
      };
    },
    [runPoll, syncTimer],
  );

  useEffect(() => {
    const onFocus = () => void runPoll();
    const onVisibilityChange = () => {
      syncTimer(true);
      if (!document.hidden) void runPoll();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, [runPoll, syncTimer]);

  const value = useMemo(() => ({ register }), [register]);
  return <TranscriptPollContext.Provider value={value}>{children}</TranscriptPollContext.Provider>;
}

export function useTranscriptPoller(key: string, enabled: boolean, poll: Poll) {
  const context = useContext(TranscriptPollContext);
  const pollRef = useRef(poll);
  pollRef.current = poll;

  useEffect(() => {
    if (!enabled || !context) return;
    return context.register(key, () => pollRef.current());
  }, [context, enabled, key]);
}
