import { useCallback, useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router'
import { fetchDbSession, fetchSessionRuns, fetchSessionUsage } from '../lib/api'
import type { AgentRunSummary, SessionDetail, SessionUsage } from '../lib/types'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

function durationStr(start: string | null, end: string | null): string {
  if (!start) return '—'
  const s = new Date(start).getTime()
  const e = end ? new Date(end).getTime() : Date.now()
  const ms = e - s
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60_000).toFixed(1)}m`
}

const STATUS_COLORS: Record<string, string> = {
  completed: 'bg-emerald-500/20 text-emerald-400',
  running: 'bg-blue-500/20 text-blue-400',
  failed: 'bg-red-500/20 text-red-400',
  pending: 'bg-zinc-500/20 text-zinc-400',
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AgentRunsPage() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [runs, setRuns] = useState<AgentRunSummary[]>([])
  const [usage, setUsage] = useState<SessionUsage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!sessionId) return
    setLoading(true)
    setError(null)
    try {
      const [sess, runList, usg] = await Promise.all([
        fetchDbSession(sessionId),
        fetchSessionRuns(sessionId),
        fetchSessionUsage(sessionId),
      ])
      setSession(sess)
      setRuns(runList)
      setUsage(usg)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => { load() }, [load])

  if (!sessionId) return <p className="p-6 text-sm text-zinc-500">No session selected.</p>

  return (
    <div className="p-6">
      {/* Back nav + header */}
      <div className="mb-4">
        <button
          onClick={() => navigate('/sessions')}
          className="text-xs text-zinc-500 hover:text-zinc-300"
        >
          &larr; All Sessions
        </button>
      </div>

      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">
            {session?.summary || `Session ${sessionId.slice(0, 8)}`}
          </h1>
          <div className="mt-1 flex items-center gap-3 text-xs text-zinc-500">
            {session && (
              <>
                <span>{session.model}</span>
                <span className="text-zinc-700">|</span>
                <span>{session.message_count} messages</span>
                <span className="text-zinc-700">|</span>
                <span className="font-mono">{sessionId.slice(0, 12)}</span>
              </>
            )}
          </div>
        </div>
        <button
          onClick={load}
          className="rounded bg-zinc-800 px-3 py-1 text-xs text-zinc-300 hover:bg-zinc-700"
        >
          Refresh
        </button>
      </div>

      {/* Usage summary cards */}
      {usage && (
        <div className="mb-6 grid grid-cols-4 gap-4">
          <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
            <div className="text-xs text-zinc-500">Prompt Tokens</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">
              {formatTokens(usage.prompt_tokens)}
            </div>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
            <div className="text-xs text-zinc-500">Completion Tokens</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">
              {formatTokens(usage.completion_tokens)}
            </div>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
            <div className="text-xs text-zinc-500">Total Tokens</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">
              {formatTokens(usage.total_tokens)}
            </div>
          </div>
          <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
            <div className="text-xs text-zinc-500">API Calls</div>
            <div className="mt-1 text-xl font-semibold text-zinc-100">{usage.call_count}</div>
          </div>
        </div>
      )}

      {loading && <p className="text-sm text-zinc-500">Loading agent runs...</p>}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {!loading && runs.length === 0 && (
        <p className="text-sm text-zinc-500">No agent runs recorded for this session.</p>
      )}

      {!loading && runs.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-800 bg-zinc-900/80 text-left text-xs text-zinc-500">
              <tr>
                <th className="px-4 py-2">Agent</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Input</th>
                <th className="px-4 py-2 text-right">Events</th>
                <th className="px-4 py-2 text-right">Duration</th>
                <th className="px-4 py-2">Started</th>
                <th className="px-4 py-2">Error</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {runs.map((r) => (
                <tr key={r.id} className="text-zinc-300 transition hover:bg-zinc-800/50">
                  <td className="px-4 py-2.5 font-medium text-zinc-100">{r.agent_name}</td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[r.status] ?? STATUS_COLORS.pending}`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="max-w-xs truncate px-4 py-2.5 text-xs text-zinc-400">
                    {r.input_query || '—'}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs">{r.event_count}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs">
                    {durationStr(r.started_at, r.finished_at)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-xs text-zinc-500">
                    {formatTime(r.started_at)}
                  </td>
                  <td className="max-w-[200px] truncate px-4 py-2.5 text-xs text-red-400">
                    {r.error || ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
