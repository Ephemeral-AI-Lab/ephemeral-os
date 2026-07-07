import * as ToastPrimitive from "@radix-ui/react-toast";
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AlertCircle, X } from "lucide-react";
import { RpcError } from "@/api/rpc";

interface ToastEntry {
  id: number;
  kind: string;
  message: string;
  details: string | null;
}

interface ToastApi {
  showError: (error: unknown) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/**
 * Renders the protocol's `{kind, message, details}` error shape uniformly,
 * bottom-right, for both protocol errors and transport failures.
 */
export function useErrorToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useErrorToast requires ToastProvider");
  return api;
}

let nextToastId = 1;

function toEntry(error: unknown): ToastEntry {
  const id = nextToastId++;
  if (error instanceof RpcError) {
    const details = JSON.stringify(error.details ?? {});
    return {
      id,
      kind: error.transport ? `${error.kind} (transport)` : error.kind,
      message: error.message,
      details: details === "{}" ? null : details,
    };
  }
  if (error instanceof Error) {
    return { id, kind: "client_error", message: error.message, details: null };
  }
  return { id, kind: "client_error", message: String(error), details: null };
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<ToastEntry[]>([]);

  const showError = useCallback((error: unknown) => {
    setEntries((current) => [...current.slice(-3), toEntry(error)]);
  }, []);

  const dismiss = useCallback((id: number) => {
    setEntries((current) => current.filter((entry) => entry.id !== id));
  }, []);

  const api = useMemo(() => ({ showError }), [showError]);

  return (
    <ToastContext.Provider value={api}>
      <ToastPrimitive.Provider swipeDirection="right" duration={8000}>
        {children}
        {entries.map((entry) => (
          <ToastPrimitive.Root
            key={entry.id}
            onOpenChange={(open) => {
              if (!open) dismiss(entry.id);
            }}
            className="flex items-start gap-2 rounded-md border border-danger/40 bg-surface p-3"
          >
            <AlertCircle size={15} className="mt-0.5 shrink-0 text-danger" />
            <div className="min-w-0">
              <ToastPrimitive.Title className="font-mono text-xs font-semibold text-danger">
                {entry.kind}
              </ToastPrimitive.Title>
              <ToastPrimitive.Description className="mt-0.5 break-words text-xs text-ink">
                {entry.message}
              </ToastPrimitive.Description>
              {entry.details ? (
                <div className="mt-1 max-h-24 overflow-auto break-all font-mono text-[11px] text-ink-mid">
                  {entry.details}
                </div>
              ) : null}
            </div>
            <ToastPrimitive.Close
              aria-label="Dismiss"
              className="ml-auto shrink-0 rounded p-0.5 text-ink-mid hover:bg-surface-hover"
            >
              <X size={13} />
            </ToastPrimitive.Close>
          </ToastPrimitive.Root>
        ))}
        <ToastPrimitive.Viewport className="fixed bottom-4 right-4 z-[60] flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2" />
      </ToastPrimitive.Provider>
    </ToastContext.Provider>
  );
}
