import { useState, useEffect, useCallback } from 'react'
import { fetchModels, registerModel, selectModel, deleteModel, fetchDbHealth } from '../lib/api'
import type { ModelRegistration, DbHealthStatus } from '../lib/types'

// ── ModelCard ────────────────────────────────────────────────────────────────

function ModelCard({
  model,
  isActive,
  onSelect,
  onDelete,
}: {
  model: ModelRegistration
  isActive: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={`rounded-lg border px-4 py-3 transition-colors ${
        isActive
          ? 'border-blue-700 bg-blue-950/30'
          : 'border-zinc-800 bg-zinc-900 hover:border-zinc-600'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-zinc-100">{model.label}</span>
            {isActive && (
              <span className="rounded-full bg-blue-900 border border-blue-700 px-2 py-0.5 text-xs text-blue-300">
                active
              </span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-2 text-xs text-zinc-500">
            <span className="font-mono">{model.model_id || model.key}</span>
            <span className="text-zinc-700">|</span>
            <span>{model.class_path}</span>
          </div>
          {Object.keys(model.kwargs).length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {Object.entries(model.kwargs).map(([k, v]) => (
                <span
                  key={k}
                  className="inline-flex items-center rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 text-xs text-zinc-400 font-mono"
                >
                  {k}: {String(v)}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 gap-1">
          {!isActive && (
            <button
              onClick={onSelect}
              className="rounded px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 hover:text-zinc-100 transition"
            >
              Activate
            </button>
          )}
          <button
            onClick={onDelete}
            className="rounded px-2.5 py-1.5 text-xs font-medium text-red-400 hover:bg-red-950 hover:text-red-300 transition"
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// ── RegisterModelForm ────────────────────────────────────────────────────────

function RegisterModelForm({ onRegistered }: { onRegistered: () => void }) {
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState('')
  const [label, setLabel] = useState('')
  const [classPath, setClassPath] = useState('providers.clients.anthropic_native.AnthropicClient')
  const [modelId, setModelId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [activate, setActivate] = useState(true)
  const [error, setError] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!key || !label || !modelId) {
      setError('Key, label, and model ID are required')
      return
    }
    setError('')
    const kwargs: Record<string, unknown> = { model: modelId, api_format: 'anthropic' }
    if (apiKey) kwargs.api_key = apiKey
    if (baseUrl) kwargs.base_url = baseUrl

    try {
      await registerModel({ key, label, class_path: classPath, kwargs, activate })
      setKey('')
      setLabel('')
      setModelId('')
      setApiKey('')
      setBaseUrl('')
      setOpen(false)
      onRegistered()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to register model')
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="rounded border border-dashed border-zinc-700 px-4 py-2.5 text-sm text-zinc-400 hover:border-zinc-500 hover:text-zinc-300 transition w-full"
      >
        + Register New Model
      </button>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-lg border border-zinc-700 bg-zinc-900 p-4 space-y-3">
      <h3 className="text-sm font-medium text-zinc-200">Register New Model</h3>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Key</label>
          <input
            type="text"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="my-model"
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Label</label>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="My Model"
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Provider</label>
          <select
            value={classPath}
            onChange={(e) => setClassPath(e.target.value)}
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
            disabled
          >
            <option value="providers.clients.anthropic_native.AnthropicClient">Anthropic Compatible</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Model ID</label>
          <input
            type="text"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            placeholder="e.g. deepseek-chat, gpt-4o, qwen3.5-flash"
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
          />
        </div>
      </div>
      <div>
        <label className="mb-1 block text-xs text-zinc-500">API Key</label>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="sk-... or env:ANTHROPIC_API_KEY"
          className="w-full rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
        />
        <p className="mt-0.5 text-xs text-zinc-600">Use "env:VAR_NAME" to reference environment variables</p>
      </div>
      <div>
        <label className="mb-1 block text-xs text-zinc-500">Base URL (optional)</label>
        <input
          type="text"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.minimax.io/anthropic"
          className="w-full rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
        />
      </div>
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="activate"
          checked={activate}
          onChange={(e) => setActivate(e.target.checked)}
          className="rounded border-zinc-600"
        />
        <label htmlFor="activate" className="text-xs text-zinc-400">Set as active model</label>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          className="rounded bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 transition"
        >
          Register
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200 transition"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}

// ── SettingsPage ─────────────────────────────────────────────────────────────

export default function SettingsPage() {
  // Model registry state
  const [models, setModels] = useState<ModelRegistration[]>([])
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [dbStatus, setDbStatus] = useState<DbHealthStatus | null>(null)

  const loadModels = useCallback(async () => {
    try {
      const health = await fetchDbHealth()
      setDbStatus(health)
      if (health.database === 'connected') {
        const data = await fetchModels()
        setModels(data.models)
        setActiveKey(data.active)
      }
    } catch {
      // DB not available
    }
  }, [])

  useEffect(() => {
    loadModels()
  }, [loadModels])

  async function handleSelectModel(key: string) {
    await selectModel(key)
    await loadModels()
  }

  async function handleDeleteModel(key: string) {
    if (!confirm(`Delete model "${key}"?`)) return
    await deleteModel(key)
    await loadModels()
  }

  return (
    <div className="mx-auto max-w-2xl p-6 space-y-8">
      {/* ── Model Registry (DB-backed) ──────────────────────────────────── */}
      {dbStatus?.database === 'connected' && (
        <section>
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-lg font-semibold text-zinc-100">Model Registry</h1>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-800 bg-emerald-950 px-2.5 py-0.5 text-xs text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              DB Connected
            </span>
          </div>

          <div className="space-y-2">
            {models.map((m) => (
              <ModelCard
                key={m.key}
                model={m}
                isActive={m.key === activeKey}
                onSelect={() => handleSelectModel(m.key)}
                onDelete={() => handleDeleteModel(m.key)}
              />
            ))}
            {models.length === 0 && (
              <div className="rounded-lg border border-zinc-800 px-4 py-6 text-center text-sm text-zinc-600">
                No models registered. Add one below or seed from registry.json.
              </div>
            )}
          </div>

          <div className="mt-3">
            <RegisterModelForm onRegistered={loadModels} />
          </div>
        </section>
      )}

    </div>
  )
}
