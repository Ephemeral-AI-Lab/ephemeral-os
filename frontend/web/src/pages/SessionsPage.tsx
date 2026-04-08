import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { fetchDbSessions, fetchSessionUsage } from '../lib/api'
import type { SessionSummary, SessionUsage } from '../lib/types'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(ts: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function SessionsPage() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [usageMap, setUsageMap] = useState<Record<string, SessionUsage>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const list = await fetchDbSessions(100)
      setSessions(list)
      // Fetch usage for each session in parallel
      const usageEntries = await Promise.all(
        list.map(async (s) => {
          const usage = await fetchSessionUsage(s.session_id)
          return [s.session_id, usage] as const
        }),
      )
      const map: Record<string, SessionUsage> = {}
      for (const [id, usage] of usageEntries) {
        if (usage) map[id] = usage
      }
      setUsageMap(map)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // -- totals ----------------------------------------------------------------

  const totalTokens = Object.values(usageMap).reduce((sum, u) => sum + u.total_tokens, 0)
  const totalCalls = Object.values(usageMap).reduce((sum, u) => sum + u.call_count, 0)

  // -- render ----------------------------------------------------------------

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-zinc-100">Sessions</h1>
        <button
          onClick={load}
          className="rounded bg-zinc-800 px-3 py-1 text-xs text-zinc-300 hover:bg-zinc-700"
        >
          Refresh
        </button>
      </div>

      {/* Global usage summary */}
      <div className="mb-6 grid grid-cols-3 gap-4">
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
          <div className="text-xs text-zinc-500">Total Sessions</div>
          <div className="mt-1 text-2xl font-semibold text-zinc-100">{sessions.length}</div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
          <div className="text-xs text-zinc-500">Total Tokens</div>
          <div className="mt-1 text-2xl font-semibold text-zinc-100">{formatTokens(totalTokens)}</div>
        </div>
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
          <div className="text-xs text-zinc-500">Total Tracked Runs</div>
          <div className="mt-1 text-2xl font-semibold text-zinc-100">{totalCalls}</div>
        </div>
      </div>

      {loading && <p className="text-sm text-zinc-500">Loading sessions...</p>}
      {error && <p className="text-sm text-red-400">{error}</p>}

      {!loading && sessions.length === 0 && (
        <p className="text-sm text-zinc-500">No sessions found. Start a conversation to create one.</p>
      )}

      {!loading && sessions.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-800 bg-zinc-900/80 text-left text-xs text-zinc-500">
              <tr>
                <th className="px-4 py-2">Summary</th>
                <th className="px-4 py-2 text-right">Messages</th>
                <th className="px-4 py-2 text-right">Prompt Tokens</th>
                <th className="px-4 py-2 text-right">Completion Tokens</th>
                <th className="px-4 py-2 text-right">Total Tokens</th>
                <th className="px-4 py-2 text-right">Tracked Runs</th>
                <th className="px-4 py-2">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {sessions.map((s) => {
                const usage = usageMap[s.session_id]
                return (
                  <tr
                    key={s.session_id}
                    onClick={() => navigate(`/sessions/${s.session_id}/runs`)}
                    className="cursor-pointer text-zinc-300 transition hover:bg-zinc-800/50"
                  >
                    <td className="max-w-xs truncate px-4 py-2.5 font-medium text-zinc-100">
                      {s.summary || s.session_id.slice(0, 8)}
                    </td>
                    <td className="px-4 py-2.5 text-right">{s.message_count}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                      {usage ? formatTokens(usage.prompt_tokens) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                      {usage ? formatTokens(usage.completion_tokens) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs font-medium text-zinc-100">
                      {usage ? formatTokens(usage.total_tokens) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-xs">
                      {usage ? usage.call_count : '—'}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-xs text-zinc-500">
                      {formatDate(s.created_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
