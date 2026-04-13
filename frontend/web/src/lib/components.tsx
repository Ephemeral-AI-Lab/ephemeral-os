import type { ReactNode } from 'react'

// ---------------------------------------------------------------------------
// Shared UI primitives
// ---------------------------------------------------------------------------

export const STATUS_COLORS: Record<string, string> = {
  completed: 'bg-emerald-500/20 text-emerald-400',
  running: 'bg-blue-500/20 text-blue-400',
  failed: 'bg-red-500/20 text-red-400',
  pending: 'bg-zinc-500/20 text-zinc-400',
}

/** Red error box */
export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-300">
      {message}
    </div>
  )
}

/** Centered empty-state placeholder */
export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 px-4 py-8 text-center text-sm text-zinc-600">
      {message}
    </div>
  )
}

/** Colored status pill */
export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[status] ?? STATUS_COLORS.pending}`}
    >
      {status}
    </span>
  )
}

/** Uppercase section header */
export function SectionHeader({ children }: { children: ReactNode }) {
  return (
    <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide">
      {children}
    </h3>
  )
}
