import { useTools } from '@/lib/hooks'
import type { ToolSnapshot } from '@/lib/types'

function ToolCard({ tool }: { tool: ToolSnapshot }) {
  return (
    <div className="rounded-lg bg-zinc-900 border border-zinc-800 px-4 py-3">
      <span className="text-sm font-medium text-zinc-100 font-mono">{tool.name}</span>
      <p className="mt-1 text-xs text-zinc-500">{tool.description}</p>
    </div>
  )
}

export default function ToolsPage() {
  const tools = useTools()

  return (
    <div className="min-h-full bg-zinc-950 text-zinc-100 p-6">
      <h2 className="text-sm font-semibold text-zinc-400 uppercase tracking-wide mb-3">
        Tools
      </h2>
      {tools.length === 0 ? (
        <div className="rounded-lg border border-zinc-800 px-4 py-6 text-center text-sm text-zinc-600">
          No tools registered
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {tools.map(tool => (
            <ToolCard key={tool.name} tool={tool} />
          ))}
        </div>
      )}
    </div>
  )
}
