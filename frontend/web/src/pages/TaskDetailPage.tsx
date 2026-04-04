import { Link, useParams } from 'react-router'
import { useTasks } from '@/lib/hooks'
import type { TaskSnapshot } from '@/lib/types'

function StatusBadge({ status }: { status: string }) {
  const base = 'inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold'
  switch (status) {
    case 'running':
      return (
        <span className={`${base} bg-green-900/50 text-green-300`}>
          <span className="mr-1.5 h-2 w-2 rounded-full bg-green-400 animate-pulse inline-block" />
          running
        </span>
      )
    case 'completed':
      return <span className={`${base} bg-blue-900/50 text-blue-300`}>completed</span>
    case 'failed':
      return <span className={`${base} bg-red-900/50 text-red-300`}>failed</span>
    case 'pending':
      return <span className={`${base} bg-yellow-900/50 text-yellow-300`}>pending</span>
    case 'killed':
      return <span className={`${base} bg-zinc-800 text-zinc-400`}>killed</span>
    default:
      return <span className={`${base} bg-zinc-800 text-zinc-400`}>{status}</span>
  }
}

function TypeBadge({ type }: { type: string }) {
  const prefix: Record<string, string> = {
    local_bash: '$',
    local_agent: 'AI',
    remote_agent: 'cloud',
    in_process_teammate: 'team',
  }
  const label = prefix[type] ?? '?'
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono bg-zinc-800 text-zinc-300 border border-zinc-700">
      <span className="text-zinc-500">{label}</span>
      <span>{type}</span>
    </span>
  )
}

function MetadataTable({ metadata }: { metadata: Record<string, string> }) {
  const entries = Object.entries(metadata)
  if (entries.length === 0) return null
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-800 bg-zinc-900">
            <th className="px-4 py-2 text-left font-medium text-zinc-400 w-1/3">Key</th>
            <th className="px-4 py-2 text-left font-medium text-zinc-400">Value</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([key, value]) => (
            <tr key={key} className="border-b border-zinc-800/50 last:border-0">
              <td className="px-4 py-2 font-mono text-zinc-400">{key}</td>
              <td className="px-4 py-2 text-zinc-200">{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ProgressBar({ value }: { value: number }) {
  const clamped = Math.min(100, Math.max(0, value))
  return (
    <div className="mt-1">
      <div className="flex justify-between text-xs text-zinc-400 mb-1">
        <span>Progress</span>
        <span>{clamped}%</span>
      </div>
      <div className="h-2 w-full rounded-full bg-zinc-800">
        <div
          className="h-2 rounded-full bg-blue-500 transition-all"
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  )
}

function TaskDetail({ task }: { task: TaskSnapshot }) {
  const progressRaw = task.metadata['progress']
  const progressValue = progressRaw != null ? parseFloat(progressRaw) : null
  const hasProgress = progressValue != null && !isNaN(progressValue)

  return (
    <div className="space-y-6">
      {/* Status and type row */}
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={task.status} />
        <TypeBadge type={task.type} />
      </div>

      {/* Progress bar */}
      {hasProgress && <ProgressBar value={progressValue!} />}

      {/* Description */}
      <section>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-2">Description</h2>
        <p className="text-zinc-200 leading-relaxed whitespace-pre-wrap">{task.description}</p>
      </section>

      {/* Metadata */}
      {Object.keys(task.metadata).length > 0 && (
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-2">Metadata</h2>
          <MetadataTable metadata={task.metadata} />
        </section>
      )}

      {/* Future: output log viewer */}
      <section className="rounded-lg border border-dashed border-zinc-800 px-4 py-3 text-zinc-600 text-sm">
        Output log viewer — coming soon
      </section>
    </div>
  )
}

export default function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const tasks = useTasks()
  const task = tasks.find(t => t.id === taskId)

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* Header */}
      <header className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between">
        <Link
          to="/dashboard"
          className="flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-100 transition-colors"
        >
          <span aria-hidden>←</span>
          Back to Dashboard
        </Link>
        <span className="text-sm text-zinc-500 font-mono">Task: {taskId}</span>
      </header>

      {/* Body */}
      <main className="mx-auto max-w-3xl px-6 py-8">
        {task == null ? (
          <div className="text-center py-16">
            <p className="text-zinc-500 text-lg">Task not found</p>
            <p className="text-zinc-600 text-sm mt-1">No task matches ID: {taskId}</p>
          </div>
        ) : (
          <TaskDetail task={task} />
        )}
      </main>
    </div>
  )
}
