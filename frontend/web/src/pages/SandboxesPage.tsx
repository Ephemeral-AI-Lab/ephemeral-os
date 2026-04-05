import { useCallback, useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SandboxInfo {
  id: string
  name: string
  state: string
  image: string | null
  labels: Record<string, string>
  created_at: string | null
  managed_by_app: boolean
  assigned_agents: string[]
}

interface HealthInfo {
  configured: boolean
  available: boolean
  api_url: string | null
  target: string | null
  detail: string | null
  default_image: string | null
}

interface SnapshotInfo {
  name: string
  state: string
  image_name: string | null
}

interface CreateSandboxRequest {
  name: string
  snapshot?: string
  image?: string
  env_vars?: Record<string, string>
  labels?: Record<string, string>
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const API = '/api/sandboxes'

async function fetchHealth(): Promise<HealthInfo> {
  const res = await fetch(`${API}/health`)
  return res.json()
}

async function fetchSandboxes(): Promise<SandboxInfo[]> {
  const res = await fetch(API)
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText)
  return res.json()
}

async function fetchSnapshots(): Promise<SnapshotInfo[]> {
  const res = await fetch(`${API}/available/snapshots`)
  if (!res.ok) return []
  return res.json()
}

async function createSandbox(req: CreateSandboxRequest): Promise<SandboxInfo> {
  const res = await fetch(API, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText)
  return res.json()
}

function parseKeyValueBlock(raw: string): Record<string, string> {
  const result: Record<string, string> = {}
  for (const line of raw.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const idx = trimmed.indexOf('=')
    if (idx <= 0) continue
    result[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim()
  }
  return result
}

async function startSandbox(id: string): Promise<void> {
  const res = await fetch(`${API}/${id}/start`, { method: 'POST' })
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText)
}

async function stopSandbox(id: string): Promise<void> {
  const res = await fetch(`${API}/${id}/stop`, { method: 'POST' })
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText)
}

async function deleteSandbox(id: string): Promise<void> {
  const res = await fetch(`${API}/${id}`, { method: 'DELETE' })
  if (res.status !== 204 && !res.ok) throw new Error('Delete failed')
}

async function execInSandbox(id: string, command: string): Promise<{ result: string; exit_code: number }> {
  const res = await fetch(`${API}/${id}/exec`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command, timeout: 30 }),
  })
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText)
  return res.json()
}

async function listFiles(id: string, path: string): Promise<Array<{ name: string; is_dir: boolean; size: number; path: string }>> {
  const res = await fetch(`${API}/${id}/files?path=${encodeURIComponent(path)}`)
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText)
  return res.json()
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

const STATE_COLORS: Record<string, string> = {
  started: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  stopped: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20',
  starting: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  stopping: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  pending_build: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  error: 'bg-red-500/10 text-red-400 border-red-500/20',
}

function StateBadge({ state }: { state: string }) {
  const color = STATE_COLORS[state] ?? STATE_COLORS.stopped
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${color}`}>
      {state}
    </span>
  )
}

function HealthBanner({ health }: { health: HealthInfo | null }) {
  if (!health) return null
  const ok = health.configured && health.available
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${ok ? 'border-emerald-800 bg-emerald-950 text-emerald-300' : 'border-amber-800 bg-amber-950 text-amber-300'}`}>
      <div className="flex items-center gap-2">
        <span>{ok ? 'Connected' : health.configured ? 'Unavailable' : 'Not configured'}</span>
        {health.api_url && <span className="text-xs opacity-60">{health.api_url}</span>}
        {health.target && <span className="text-xs opacity-60">target={health.target}</span>}
      </div>
      {health.detail && <p className="mt-1 text-xs opacity-60">{health.detail}</p>}
    </div>
  )
}

function SandboxCard({
  sandbox,
  onRefresh,
}: {
  sandbox: SandboxInfo
  onRefresh: () => void
}) {
  const [loading, setLoading] = useState('')
  const [shellCmd, setShellCmd] = useState('')
  const [shellOutput, setShellOutput] = useState<string | null>(null)
  const [files, setFiles] = useState<Array<{ name: string; is_dir: boolean; size: number; path: string }> | null>(null)
  const [filePath, setFilePath] = useState('/home/daytona')
  const [expanded, setExpanded] = useState(false)

  const act = async (action: string, fn: () => Promise<void>) => {
    setLoading(action)
    try {
      await fn()
      onRefresh()
    } catch (e) {
      alert(`${action} failed: ${e instanceof Error ? e.message : e}`)
    } finally {
      setLoading('')
    }
  }

  const runCommand = async () => {
    if (!shellCmd.trim()) return
    setLoading('exec')
    try {
      const resp = await execInSandbox(sandbox.id, shellCmd)
      setShellOutput(`[exit ${resp.exit_code}] ${resp.result}`)
    } catch (e) {
      setShellOutput(`Error: ${e instanceof Error ? e.message : e}`)
    } finally {
      setLoading('')
    }
  }

  const browseFiles = async (path: string) => {
    setFilePath(path)
    try {
      const result = await listFiles(sandbox.id, path)
      setFiles(result)
    } catch (e) {
      setFiles(null)
    }
  }

  const isStarted = sandbox.state === 'started'

  return (
    <div className="rounded-lg bg-zinc-900 border border-zinc-800 p-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className="text-sm font-medium text-zinc-100 cursor-pointer hover:text-cyan-400"
              onClick={() => setExpanded(!expanded)}
            >
              {sandbox.name}
            </span>
            <StateBadge state={sandbox.state} />
          </div>
          <p className="mt-0.5 text-xs text-zinc-600 font-mono truncate">{sandbox.id}</p>
        </div>
        <div className="flex gap-1.5 shrink-0">
          {!isStarted && (
            <button
              className="rounded px-2.5 py-1 text-xs font-medium bg-emerald-950 text-emerald-400 border border-emerald-800 hover:bg-emerald-900 disabled:opacity-50"
              disabled={!!loading}
              onClick={() => act('start', () => startSandbox(sandbox.id))}
            >
              {loading === 'start' ? '...' : 'Start'}
            </button>
          )}
          {isStarted && (
            <button
              className="rounded px-2.5 py-1 text-xs font-medium bg-amber-950 text-amber-400 border border-amber-800 hover:bg-amber-900 disabled:opacity-50"
              disabled={!!loading}
              onClick={() => act('stop', () => stopSandbox(sandbox.id))}
            >
              {loading === 'stop' ? '...' : 'Stop'}
            </button>
          )}
          <button
            className="rounded px-2.5 py-1 text-xs font-medium bg-red-950 text-red-400 border border-red-800 hover:bg-red-900 disabled:opacity-50"
            disabled={!!loading}
            onClick={() => {
              if (confirm(`Delete sandbox "${sandbox.name}"?`)) {
                act('delete', () => deleteSandbox(sandbox.id))
              }
            }}
          >
            {loading === 'delete' ? '...' : 'Delete'}
          </button>
        </div>
      </div>

      {/* Metadata */}
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-zinc-500">
        {sandbox.image && <span className="font-mono">{sandbox.image}</span>}
        {sandbox.managed_by_app && (
          <span className="text-cyan-500 border border-cyan-800 rounded px-1">Managed</span>
        )}
        {sandbox.assigned_agents.length > 0 && (
          <span>Agents: {sandbox.assigned_agents.join(', ')}</span>
        )}
        {sandbox.created_at && <span>{new Date(sandbox.created_at).toLocaleDateString()}</span>}
      </div>

      {/* Labels */}
      {Object.keys(sandbox.labels).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {Object.entries(sandbox.labels).map(([k, v]) => (
            <span key={k} className="inline-flex items-center rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 text-xs text-zinc-400 font-mono">
              {k}={v}
            </span>
          ))}
        </div>
      )}

      {/* Expanded: Shell + Files */}
      {expanded && isStarted && (
        <div className="mt-3 space-y-3 border-t border-zinc-800 pt-3">
          {/* Shell */}
          <div>
            <label className="text-xs font-medium text-zinc-400 uppercase tracking-wide">Shell</label>
            <div className="mt-1 flex gap-2">
              <input
                type="text"
                value={shellCmd}
                onChange={(e) => setShellCmd(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && runCommand()}
                placeholder="Enter command..."
                className="flex-1 rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs text-zinc-100 font-mono placeholder:text-zinc-600 focus:border-cyan-600 focus:outline-none"
              />
              <button
                className="rounded px-3 py-1.5 text-xs font-medium bg-cyan-950 text-cyan-400 border border-cyan-800 hover:bg-cyan-900 disabled:opacity-50"
                disabled={loading === 'exec'}
                onClick={runCommand}
              >
                Run
              </button>
            </div>
            {shellOutput !== null && (
              <pre className="mt-2 max-h-40 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2 text-xs text-zinc-300 font-mono whitespace-pre-wrap">
                {shellOutput}
              </pre>
            )}
          </div>

          {/* File Browser */}
          <div>
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-zinc-400 uppercase tracking-wide">Files</label>
              <button
                className="text-xs text-cyan-500 hover:text-cyan-400"
                onClick={() => browseFiles(filePath)}
              >
                Browse {filePath}
              </button>
            </div>
            {files !== null && (
              <div className="mt-1 max-h-48 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2">
                {filePath !== '/' && (
                  <div
                    className="flex items-center gap-1.5 px-1 py-0.5 text-xs text-cyan-500 cursor-pointer hover:text-cyan-400"
                    onClick={() => browseFiles(filePath.split('/').slice(0, -1).join('/') || '/')}
                  >
                    ..
                  </div>
                )}
                {files.map((f) => (
                  <div
                    key={f.name}
                    className={`flex items-center justify-between px-1 py-0.5 text-xs ${f.is_dir ? 'text-cyan-400 cursor-pointer hover:text-cyan-300' : 'text-zinc-400'}`}
                    onClick={f.is_dir ? () => browseFiles(f.path) : undefined}
                  >
                    <span className="font-mono truncate">
                      {f.is_dir ? '\uD83D\uDCC1 ' : '\uD83D\uDCC4 '}
                      {f.name}
                    </span>
                    {!f.is_dir && <span className="text-zinc-600 shrink-0 ml-2">{formatSize(f.size)}</span>}
                  </div>
                ))}
                {files.length === 0 && <p className="text-xs text-zinc-600 px-1">(empty)</p>}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SandboxesPage() {
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [sandboxes, setSandboxes] = useState<SandboxInfo[]>([])
  const [snapshots, setSnapshots] = useState<SnapshotInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [selectedSnapshot, setSelectedSnapshot] = useState('')
  const [envVarsText, setEnvVarsText] = useState('')
  const [labelsText, setLabelsText] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [h, sbs, snaps] = await Promise.all([fetchHealth(), fetchSandboxes(), fetchSnapshots()])
      setHealth(h)
      setSandboxes(sbs)
      setSnapshots(snaps.filter(s => {
        const state = s.state.toLowerCase()
        return state === 'active' || state.endsWith('.active')
      }))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 5000)
    return () => clearInterval(interval)
  }, [refresh])

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true)
    try {
      const req: CreateSandboxRequest = { name: newName.trim() }
      if (selectedSnapshot) req.snapshot = selectedSnapshot
      const envVars = parseKeyValueBlock(envVarsText)
      if (Object.keys(envVars).length > 0) req.env_vars = envVars
      const labels = parseKeyValueBlock(labelsText)
      if (Object.keys(labels).length > 0) req.labels = labels
      await createSandbox(req)
      setNewName('')
      setSelectedSnapshot('')
      setEnvVarsText('')
      setLabelsText('')
      await refresh()
    } catch (e) {
      alert(`Create failed: ${e instanceof Error ? e.message : e}`)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="min-h-full bg-zinc-950 text-zinc-100 p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Sandboxes</h1>
        <button
          className="rounded px-3 py-1.5 text-xs font-medium text-zinc-400 border border-zinc-700 hover:text-zinc-200 hover:border-zinc-500"
          onClick={refresh}
        >
          Refresh
        </button>
      </div>

      <HealthBanner health={health} />

      {error && (
        <div className="rounded-lg border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* Create */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4 space-y-3">
        <h2 className="text-sm font-medium text-zinc-300">Create Sandbox</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-zinc-500 uppercase tracking-wide mb-1">Name</label>
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              placeholder="research-python-node"
              className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-cyan-600 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-500 uppercase tracking-wide mb-1">Snapshot</label>
            <select
              value={selectedSnapshot}
              onChange={(e) => setSelectedSnapshot(e.target.value)}
              className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-cyan-600 focus:outline-none"
            >
              <option value="">Default</option>
              {snapshots.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}{s.image_name ? ` — ${s.image_name}` : ''}
                </option>
              ))}
            </select>
            {snapshots.length === 0 && !loading && (
              <p className="mt-1 text-xs text-zinc-600">No snapshots available</p>
            )}
          </div>
        </div>

        <button
          type="button"
          className="text-xs text-zinc-500 hover:text-zinc-300"
          onClick={() => setShowAdvanced(!showAdvanced)}
        >
          {showAdvanced ? 'Hide' : 'Show'} advanced options
        </button>

        {showAdvanced && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-500 uppercase tracking-wide mb-1">Env Vars</label>
              <textarea
                value={envVarsText}
                onChange={(e) => setEnvVarsText(e.target.value)}
                rows={4}
                placeholder={'NODE_ENV=development\nPYTHONUNBUFFERED=1'}
                className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-xs text-zinc-100 font-mono placeholder:text-zinc-600 focus:border-cyan-600 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-zinc-500 uppercase tracking-wide mb-1">Labels</label>
              <textarea
                value={labelsText}
                onChange={(e) => setLabelsText(e.target.value)}
                rows={4}
                placeholder={'team=research\npurpose=agent-sandbox'}
                className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-xs text-zinc-100 font-mono placeholder:text-zinc-600 focus:border-cyan-600 focus:outline-none"
              />
            </div>
          </div>
        )}

        <div className="flex justify-end">
          <button
            className="rounded px-4 py-2 text-sm font-medium bg-cyan-900 text-cyan-300 border border-cyan-700 hover:bg-cyan-800 disabled:opacity-50"
            disabled={creating || !newName.trim()}
            onClick={handleCreate}
          >
            {creating ? 'Creating...' : 'Create Sandbox'}
          </button>
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div className="text-center text-sm text-zinc-500 py-8">Loading...</div>
      ) : sandboxes.length === 0 ? (
        <div className="rounded-lg border border-zinc-800 px-4 py-8 text-center text-sm text-zinc-600">
          No sandboxes found
        </div>
      ) : (
        <div className="space-y-3">
          {sandboxes.map((sb) => (
            <SandboxCard key={sb.id} sandbox={sb} onRefresh={refresh} />
          ))}
        </div>
      )}
    </div>
  )
}
