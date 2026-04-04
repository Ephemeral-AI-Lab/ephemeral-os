import { useState, useEffect } from 'react'
import { useAppState } from '../lib/hooks'
import { updateConfig } from '../lib/api'
import type { ConfigUpdate } from '../lib/types'

export default function SettingsPage() {
  const state = useAppState()
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [apiFormat, setApiFormat] = useState('anthropic')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (state) {
      setModel(state.model)
      setBaseUrl(state.base_url || '')
      setApiFormat(state.provider === 'openai-compatible' ? 'openai' : 'anthropic')
    }
  }, [state])

  async function handleSave() {
    const config: ConfigUpdate = {}
    if (model && model !== state?.model) config.model = model
    if (baseUrl !== (state?.base_url || '')) config.base_url = baseUrl || undefined
    if (apiKey) config.api_key = apiKey
    const currentFormat = state?.provider === 'openai-compatible' ? 'openai' : 'anthropic'
    if (apiFormat !== currentFormat) config.api_format = apiFormat

    if (Object.keys(config).length === 0) return

    try {
      setError('')
      const result = await updateConfig(config)
      if (result.changed) {
        setApiKey('')
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update config')
    }
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <h1 className="mb-6 text-lg font-semibold text-zinc-100">Model Configuration</h1>

      <div className="space-y-5">
        {/* API Format */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">API Format</label>
          <select
            value={apiFormat}
            onChange={(e) => setApiFormat(e.target.value)}
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 focus:border-zinc-500 focus:outline-none"
          >
            <option value="anthropic">Anthropic</option>
            <option value="openai">OpenAI Compatible</option>
          </select>
          <p className="mt-1 text-xs text-zinc-500">
            Use "OpenAI Compatible" for DashScope, GitHub Models, vLLM, Ollama, etc.
          </p>
        </div>

        {/* Model */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Model</label>
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="claude-sonnet-4-20250514"
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-zinc-500">
            Model ID or alias (e.g. claude-sonnet-4-20250514, gpt-4o, qwen-max)
          </p>
        </div>

        {/* Base URL */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">Base URL</label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://api.anthropic.com (leave empty for default)"
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-zinc-500">
            Custom API endpoint. Leave empty for the provider's default.
          </p>
        </div>

        {/* API Key */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-zinc-300">API Key</label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="Enter new API key (leave empty to keep current)"
            className="w-full rounded border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-zinc-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-zinc-500">
            Only sent when changed. Stored in ~/.ephemeralos/settings.json.
          </p>
        </div>

        {/* Current status */}
        {state && (
          <div className="rounded border border-zinc-800 bg-zinc-900/50 p-4">
            <h2 className="mb-2 text-sm font-medium text-zinc-400">Current Status</h2>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <span className="text-zinc-500">Model:</span>
              <span className="text-zinc-200">{state.model}</span>
              <span className="text-zinc-500">Provider:</span>
              <span className="text-zinc-200">{state.provider}</span>
              <span className="text-zinc-500">Auth:</span>
              <span className={state.auth_status === 'configured' ? 'text-emerald-400' : 'text-red-400'}>
                {state.auth_status}
              </span>
              {state.base_url && (
                <>
                  <span className="text-zinc-500">Base URL:</span>
                  <span className="text-zinc-200 break-all">{state.base_url}</span>
                </>
              )}
            </div>
          </div>
        )}

        {/* Save */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            className="rounded bg-zinc-100 px-4 py-2 text-sm font-medium text-zinc-900 hover:bg-zinc-200 transition"
          >
            Apply Changes
          </button>
          {saved && <span className="text-sm text-emerald-400">Configuration updated</span>}
          {error && <span className="text-sm text-red-400">{error}</span>}
        </div>
      </div>
    </div>
  )
}
