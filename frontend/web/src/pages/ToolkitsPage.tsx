import { useState } from 'react'
import { useToolkits } from '@/lib/hooks'
import type { ToolkitSnapshot } from '@/lib/types'

// ── ToolkitCard ──────────────────────────────────────────────────────────────

function ToolkitCard({ toolkit }: { toolkit: ToolkitSnapshot }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div
      className="rounded-lg bg-zinc-900 border border-zinc-800 px-4 py-3 hover:border-zinc-600 transition-colors cursor-pointer"
      onClick={() => setExpanded(!expanded)}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-zinc-100">{toolkit.name}</span>
        <span className="inline-flex items-center rounded-full border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-xs font-medium text-zinc-400">
          {toolkit.tools.length} tool{toolkit.tools.length !== 1 ? 's' : ''}
        </span>
      </div>
      <p className="mt-1 text-xs text-zinc-500">{toolkit.description}</p>
      {expanded && (
        <div className="mt-2 flex flex-wrap gap-1">
          {toolkit.tools.map(tool => (
            <span
              key={tool}
              className="inline-flex items-center rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 text-xs text-zinc-400 font-mono"
            >
              {tool}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ── ToolkitsPage ─────────────────────────────────────────────────────────────

export default function ToolkitsPage() {
  const toolkits = useToolkits()

  return (
    <div className="min-h-full bg-zinc-950 text-zinc-100 p-6">
      <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wide mb-3">
        Toolkits
      </h2>
      {toolkits.length === 0 ? (
        <div className="rounded-lg border border-zinc-800 px-4 py-6 text-center text-sm text-zinc-600">
          No toolkits registered
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {toolkits.map(tk => (
            <ToolkitCard key={tk.name} toolkit={tk} />
          ))}
        </div>
      )}
    </div>
  )
}
