import { useEffect, useState } from 'react'
import type { PipelineConfig, PipelineRun, PipelineStepRecord, PipelineCheckpointSummary } from '../lib/types'
import {
  fetchPipelines,
  fetchPipelineTemplates,
  createPipeline,
  deletePipeline,
  startPipelineRun,
  fetchPipelineRuns,
  fetchPipelineRun,
  fetchPipelineCheckpoints,
  resumePipelineRun,
} from '../lib/api'

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: 'bg-zinc-600 text-zinc-300',
    running: 'bg-blue-600 text-blue-100',
    completed: 'bg-emerald-600 text-emerald-100',
    failed: 'bg-red-600 text-red-100',
    cancelled: 'bg-amber-600 text-amber-100',
    skipped: 'bg-zinc-700 text-zinc-400',
  }
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${colors[status] ?? 'bg-zinc-700 text-zinc-300'}`}>
      {status}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Step timeline
// ---------------------------------------------------------------------------

function StepTimeline({ steps, currentStep }: { steps: PipelineStepRecord[]; currentStep?: string | null }) {
  return (
    <div className="flex items-center gap-1">
      {steps.map((step) => {
        const isCurrent = step.name === currentStep
        const dotColor: Record<string, string> = {
          pending: 'bg-zinc-600',
          running: 'bg-blue-500 animate-pulse',
          completed: 'bg-emerald-500',
          failed: 'bg-red-500',
          skipped: 'bg-zinc-700',
        }
        return (
          <div key={step.name} className="flex flex-col items-center gap-1" title={`${step.name}: ${step.status}`}>
            <div className={`h-3 w-3 rounded-full ${dotColor[step.status] ?? 'bg-zinc-600'} ${isCurrent ? 'ring-2 ring-blue-400' : ''}`} />
            <span className="text-[10px] text-zinc-500 max-w-[60px] truncate">{step.name}</span>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Run detail panel
// ---------------------------------------------------------------------------

function RunDetail({ run }: { run: PipelineRun }) {
  const [checkpoints, setCheckpoints] = useState<PipelineCheckpointSummary[]>([])
  const [showContext, setShowContext] = useState(false)

  useEffect(() => {
    fetchPipelineCheckpoints(run.run_id).then(setCheckpoints)
  }, [run.run_id, run.status])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <StatusBadge status={run.status} />
        <span className="text-sm text-zinc-400">Attempt #{run.attempt_number ?? 1}</span>
        {run.started_at && (
          <span className="text-xs text-zinc-500">
            Started {new Date(run.started_at * 1000).toLocaleTimeString()}
          </span>
        )}
      </div>

      {run.goal && <p className="text-sm text-zinc-300">{run.goal}</p>}

      <StepTimeline steps={run.step_records} currentStep={run.current_step} />

      {/* Step details */}
      <div className="space-y-2">
        {run.step_records.map((step) => (
          <div key={step.name} className="rounded border border-zinc-800 bg-zinc-900/50 p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-zinc-200">{step.name}</span>
                <span className="text-xs text-zinc-500">({step.agent})</span>
              </div>
              <StatusBadge status={step.status} />
            </div>
            {step.error && <p className="mt-1 text-xs text-red-400">{step.error}</p>}
            {step.started_at && step.finished_at && (
              <p className="mt-1 text-xs text-zinc-500">
                Duration: {((step.finished_at - step.started_at) / 1).toFixed(1)}s
              </p>
            )}
          </div>
        ))}
      </div>

      {run.error && (
        <div className="rounded border border-red-800 bg-red-900/20 p-3">
          <p className="text-sm text-red-400">{run.error}</p>
        </div>
      )}

      {/* Checkpoints */}
      {checkpoints.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-medium text-zinc-300">Checkpoints (Resume Points)</h4>
          <div className="space-y-1">
            {checkpoints.map((cp) => (
              <div key={cp.checkpoint_id} className="flex items-center justify-between rounded bg-zinc-900/50 px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-zinc-400">After: {cp.step_name}</span>
                  <span className="text-xs text-zinc-600">
                    {new Date(cp.created_at * 1000).toLocaleTimeString()}
                  </span>
                </div>
                {(run.status === 'failed' || run.status === 'cancelled') && (
                  <button
                    onClick={() => resumePipelineRun(run.run_id, cp.checkpoint_id)}
                    className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-500"
                  >
                    Resume from here
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Context map toggle */}
      <button
        onClick={() => setShowContext(!showContext)}
        className="text-xs text-zinc-500 hover:text-zinc-300"
      >
        {showContext ? 'Hide' : 'Show'} Context Map
      </button>
      {showContext && (
        <pre className="max-h-64 overflow-auto rounded bg-zinc-950 p-3 text-xs text-zinc-400">
          {JSON.stringify(run.context_map, null, 2)}
        </pre>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function PipelinesPage() {
  const [pipelines, setPipelines] = useState<PipelineConfig[]>([])
  const [templates, setTemplates] = useState<PipelineConfig[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [runs, setRuns] = useState<PipelineRun[]>([])
  const [selectedRun, setSelectedRun] = useState<PipelineRun | null>(null)
  const [goal, setGoal] = useState('')
  const [polling, setPolling] = useState<ReturnType<typeof setInterval> | null>(null)

  // Load pipelines + templates on mount
  useEffect(() => {
    fetchPipelines().then(setPipelines)
    fetchPipelineTemplates().then(setTemplates)
  }, [])

  // Load runs when pipeline selected
  useEffect(() => {
    if (!selectedId) { setRuns([]); return }
    fetchPipelineRuns(selectedId).then(setRuns)
  }, [selectedId])

  // Poll active run
  useEffect(() => {
    if (!selectedRun || (selectedRun.status !== 'running' && selectedRun.status !== 'pending')) {
      if (polling) { clearInterval(polling); setPolling(null) }
      return
    }
    const id = setInterval(async () => {
      const updated = await fetchPipelineRun(selectedRun.run_id)
      if (updated) {
        setSelectedRun(updated)
        if (updated.status !== 'running' && updated.status !== 'pending') {
          if (selectedId) fetchPipelineRuns(selectedId).then(setRuns)
        }
      }
    }, 2000)
    setPolling(id)
    return () => clearInterval(id)
  }, [selectedRun?.run_id, selectedRun?.status])

  const handleCreateFromTemplate = async (template: PipelineConfig) => {
    const id = `${template.pipeline_id}-${Date.now().toString(36)}`
    const config = { ...template, pipeline_id: id }
    await createPipeline(config)
    const updated = await fetchPipelines()
    setPipelines(updated)
    setSelectedId(id)
  }

  const handleDelete = async (id: string) => {
    await deletePipeline(id)
    setPipelines(await fetchPipelines())
    if (selectedId === id) setSelectedId(null)
  }

  const handleRun = async () => {
    if (!selectedId || !goal.trim()) return
    await startPipelineRun(selectedId, goal)
    setGoal('')
    // Start polling for runs
    setTimeout(async () => {
      const r = await fetchPipelineRuns(selectedId)
      setRuns(r)
      if (r.length > 0) setSelectedRun(r[0])
    }, 1000)
  }

  const selected = pipelines.find((p) => p.pipeline_id === selectedId)

  return (
    <div className="flex h-full">
      {/* Left panel — pipeline list */}
      <div className="w-72 shrink-0 overflow-auto border-r border-zinc-800 p-4">
        <h2 className="mb-3 text-sm font-semibold text-zinc-100">Pipelines</h2>

        {pipelines.length === 0 && templates.length === 0 && (
          <p className="text-xs text-zinc-500">No pipelines yet.</p>
        )}

        {pipelines.map((p) => (
          <div
            key={p.pipeline_id}
            onClick={() => setSelectedId(p.pipeline_id)}
            className={`mb-2 cursor-pointer rounded border p-3 transition ${
              selectedId === p.pipeline_id
                ? 'border-blue-600 bg-zinc-800/50'
                : 'border-zinc-800 bg-zinc-900/50 hover:border-zinc-700'
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-zinc-200">{p.name}</span>
              <button
                onClick={(e) => { e.stopPropagation(); handleDelete(p.pipeline_id) }}
                className="text-xs text-zinc-600 hover:text-red-400"
              >
                x
              </button>
            </div>
            <p className="mt-1 text-xs text-zinc-500">{p.steps.length} steps</p>
            {p.tags && p.tags.length > 0 && (
              <div className="mt-1 flex gap-1">
                {p.tags.map((t) => (
                  <span key={t} className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">{t}</span>
                ))}
              </div>
            )}
          </div>
        ))}

        {/* Templates */}
        {templates.length > 0 && (
          <>
            <h3 className="mb-2 mt-4 text-xs font-medium text-zinc-500">Templates</h3>
            {templates.map((t) => (
              <button
                key={t.pipeline_id}
                onClick={() => handleCreateFromTemplate(t)}
                className="mb-2 w-full rounded border border-dashed border-zinc-700 p-3 text-left transition hover:border-zinc-500"
              >
                <span className="text-sm text-zinc-300">{t.name}</span>
                <p className="mt-1 text-xs text-zinc-500">{t.description}</p>
              </button>
            ))}
          </>
        )}
      </div>

      {/* Right panel — detail + runs */}
      <div className="flex-1 overflow-auto p-6">
        {!selected ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-zinc-500">Select a pipeline or create one from a template</p>
          </div>
        ) : (
          <div className="space-y-6">
            {/* Pipeline info */}
            <div>
              <h2 className="text-lg font-semibold text-zinc-100">{selected.name}</h2>
              {selected.description && <p className="mt-1 text-sm text-zinc-400">{selected.description}</p>}
            </div>

            {/* Steps overview */}
            <div>
              <h3 className="mb-2 text-sm font-medium text-zinc-300">Steps</h3>
              <div className="space-y-1">
                {selected.steps.map((step, i) => (
                  <div key={step.name} className="flex items-center gap-3 rounded bg-zinc-900/50 px-3 py-2">
                    <span className="text-xs text-zinc-600">{i + 1}</span>
                    <span className="text-sm text-zinc-200">{step.name}</span>
                    <span className="text-xs text-zinc-500">({step.agent})</span>
                    {step.posthook_agent && (
                      <span className="text-xs text-zinc-600">+ {step.posthook_agent}</span>
                    )}
                    {step.input_deps && step.input_deps.length > 0 && (
                      <span className="text-[10px] text-zinc-600">
                        deps: {step.input_deps.map((d) => d.step).join(', ')}
                      </span>
                    )}
                    {!step.enabled && <StatusBadge status="skipped" />}
                  </div>
                ))}
              </div>
            </div>

            {/* Run pipeline */}
            <div className="flex gap-2">
              <input
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleRun()}
                placeholder="Enter pipeline goal..."
                className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-blue-600 focus:outline-none"
              />
              <button
                onClick={handleRun}
                disabled={!goal.trim()}
                className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
              >
                Run
              </button>
            </div>

            {/* Runs */}
            {runs.length > 0 && (
              <div>
                <h3 className="mb-2 text-sm font-medium text-zinc-300">Runs</h3>
                <div className="space-y-2">
                  {runs.map((run) => (
                    <div
                      key={run.run_id}
                      onClick={() => setSelectedRun(run)}
                      className={`cursor-pointer rounded border p-3 transition ${
                        selectedRun?.run_id === run.run_id
                          ? 'border-blue-600 bg-zinc-800/50'
                          : 'border-zinc-800 hover:border-zinc-700'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-mono text-zinc-400">{run.run_id}</span>
                        <StatusBadge status={run.status} />
                      </div>
                      {run.step_records.length > 0 && (
                        <div className="mt-2">
                          <StepTimeline steps={run.step_records} currentStep={run.current_step} />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Selected run detail */}
            {selectedRun && (
              <div className="rounded border border-zinc-800 p-4">
                <h3 className="mb-3 text-sm font-medium text-zinc-300">
                  Run: {selectedRun.run_id}
                </h3>
                <RunDetail run={selectedRun} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
