import { useEffect, useRef, useState, useCallback, KeyboardEvent } from 'react'
import { useTranscript } from '@/lib/hooks'
import { useModal } from '@/lib/hooks'
import { useConnected } from '@/lib/hooks'
import type { TranscriptItem, PipelineConfig, PipelineRun } from '@/lib/types'
import { fetchPipelines, startPipelineRun, fetchPipelineRun } from '@/lib/api'

interface AgentSummary {
  name: string
  description: string
  source: string
  model: string | null
}

interface SandboxInfo {
  id: string
  name: string
  state: string
}

// ── Assistant text parser ─────────────────────────────────────────────────────

type AssistantSegment =
  | { kind: 'text'; content: string }
  | { kind: 'tool_call'; tool: string; args: string }
  | { kind: 'thinking'; content: string }

function parseAssistantText(text: string): AssistantSegment[] {
  const segments: AssistantSegment[] = []
  // Match [TOOL_CALL]...[/TOOL_CALL] and <think>...</think> blocks
  const pattern = /\[TOOL_CALL\]([\s\S]*?)\[\/TOOL_CALL\]|<think>([\s\S]*?)<\/think>/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text)) !== null) {
    // Push preceding text
    if (match.index > lastIndex) {
      const before = text.slice(lastIndex, match.index).trim()
      if (before) segments.push({ kind: 'text', content: before })
    }

    if (match[1] !== undefined) {
      // [TOOL_CALL] block — try to extract tool name and args
      const raw = match[1].trim()
      const toolMatch = raw.match(/\{?\s*tool\s*(?:=>|:)\s*"([^"]+)"/)
      const toolName = toolMatch ? toolMatch[1] : 'tool'
      const argsMatch = raw.match(/args\s*(?:=>|:)\s*(\{[\s\S]*\})/)
      const argsText = argsMatch ? argsMatch[1].trim() : raw
      segments.push({ kind: 'tool_call', tool: toolName, args: argsText })
    } else if (match[2] !== undefined) {
      // <think> block
      const content = match[2].trim()
      if (content) segments.push({ kind: 'thinking', content })
    }

    lastIndex = match.index + match[0].length
  }

  // Push trailing text
  if (lastIndex < text.length) {
    const after = text.slice(lastIndex).trim()
    if (after) segments.push({ kind: 'text', content: after })
  }

  return segments
}

function hasSpecialBlocks(text: string): boolean {
  return /\[TOOL_CALL\]|<think>/.test(text)
}

// ── Inline cards for parsed assistant segments ────────────────────────────────

function InlineToolCallCard({ tool, args }: { tool: string; args: string }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="my-2 rounded-lg border border-zinc-600 bg-zinc-900 text-sm">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-zinc-700/50 transition-colors rounded-lg"
        onClick={() => setExpanded(e => !e)}
      >
        <span className="font-mono text-xs bg-blue-900/60 text-blue-300 px-1.5 py-0.5 rounded">
          {tool}
        </span>
        <span className="text-zinc-500 text-xs">tool call</span>
        <span className="text-zinc-500 text-xs ml-auto shrink-0">
          {expanded ? '▲' : '▼'}
        </span>
      </button>
      {expanded && (
        <pre className="px-3 pb-3 text-xs font-mono text-zinc-400 whitespace-pre-wrap break-all border-t border-zinc-700 pt-2">
          {args || '(no args)'}
        </pre>
      )}
    </div>
  )
}

function InlineThinkingBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false)
  const preview = content.length > 80 ? content.slice(0, 80) + '…' : content
  return (
    <div className="my-2 rounded-lg border border-amber-900/50 bg-amber-950/30 text-sm">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-amber-900/20 transition-colors rounded-lg"
        onClick={() => setExpanded(e => !e)}
      >
        <span className="text-amber-400 text-xs">✦ Thinking</span>
        {!expanded && (
          <span className="text-zinc-500 text-xs truncate flex-1">{preview}</span>
        )}
        <span className="text-zinc-500 text-xs ml-auto shrink-0">
          {expanded ? '▲' : '▼'}
        </span>
      </button>
      {expanded && (
        <pre className="px-3 pb-3 text-xs font-mono text-zinc-400 whitespace-pre-wrap break-words border-t border-amber-900/40 pt-2">
          {content}
        </pre>
      )}
    </div>
  )
}

// ── MessageBubble ──────────────────────────────────────────────────────────────

function ToolCard({
  item,
}: {
  item: TranscriptItem & { role: 'tool' | 'tool_result' }
}) {
  const [expanded, setExpanded] = useState(false)

  if (item.role === 'tool') {
    const inputStr = item.tool_input
      ? JSON.stringify(item.tool_input, null, 2)
      : ''
    const summary = inputStr.length > 100 ? inputStr.slice(0, 100) + '…' : inputStr

    return (
      <div className="my-2 rounded-lg border border-zinc-700 bg-zinc-800 text-sm">
        <button
          className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-zinc-700/50 transition-colors rounded-lg"
          onClick={() => setExpanded(e => !e)}
        >
          <span className="font-mono text-xs bg-zinc-700 text-zinc-300 px-1.5 py-0.5 rounded">
            {item.tool_name ?? 'tool'}
          </span>
          {!expanded && (
            <span className="text-zinc-400 font-mono text-xs truncate flex-1">
              {summary}
            </span>
          )}
          <span className="text-zinc-500 text-xs ml-auto shrink-0">
            {expanded ? '▲' : '▼'}
          </span>
        </button>
        {expanded && (
          <pre className="px-3 pb-3 text-xs font-mono text-zinc-300 whitespace-pre-wrap break-all border-t border-zinc-700 pt-2">
            {inputStr || '(no input)'}
          </pre>
        )}
      </div>
    )
  }

  // tool_result
  const preview = item.text.length > 200 ? item.text.slice(0, 200) + '…' : item.text
  const borderClass = item.is_error ? 'border-red-700' : 'border-zinc-700'

  return (
    <div className={`my-2 rounded-lg border ${borderClass} bg-zinc-800 text-sm`}>
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-zinc-700/50 transition-colors rounded-lg"
        onClick={() => setExpanded(e => !e)}
      >
        <span className={`font-mono text-xs px-1.5 py-0.5 rounded ${item.is_error ? 'bg-red-900 text-red-300' : 'bg-zinc-700 text-zinc-300'}`}>
          {item.tool_name ?? 'result'}
        </span>
        {item.is_error && (
          <span className="text-red-400 text-xs">error</span>
        )}
        {!expanded && (
          <span className="text-zinc-400 font-mono text-xs truncate flex-1">
            {preview}
          </span>
        )}
        <span className="text-zinc-500 text-xs ml-auto shrink-0">
          {expanded ? '▲' : '▼'}
        </span>
      </button>
      {expanded && (
        <pre className="px-3 pb-3 text-xs font-mono text-zinc-300 whitespace-pre-wrap break-all border-t border-zinc-700 pt-2">
          {item.text || '(empty)'}
        </pre>
      )}
    </div>
  )
}

function MessageBubble({ item }: { item: TranscriptItem }) {
  if (item.role === 'user') {
    return (
      <div className="flex justify-end mb-3">
        <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-blue-600 px-4 py-2.5 text-white text-sm whitespace-pre-wrap break-words">
          {item.text}
        </div>
      </div>
    )
  }

  if (item.role === 'assistant') {
    if (hasSpecialBlocks(item.text)) {
      const segments = parseAssistantText(item.text)
      return (
        <div className="flex justify-start mb-3">
          <div className="max-w-[85%] rounded-2xl rounded-tl-sm bg-zinc-800 px-4 py-2.5 text-zinc-100 text-sm">
            {segments.map((seg, i) =>
              seg.kind === 'text' ? (
                <div key={i} className="whitespace-pre-wrap break-words">{seg.content}</div>
              ) : seg.kind === 'tool_call' ? (
                <InlineToolCallCard key={i} tool={seg.tool} args={seg.args} />
              ) : (
                <InlineThinkingBlock key={i} content={seg.content} />
              )
            )}
          </div>
        </div>
      )
    }
    return (
      <div className="flex justify-start mb-3">
        <div className="max-w-[85%] rounded-2xl rounded-tl-sm bg-zinc-800 px-4 py-2.5 text-zinc-100 text-sm whitespace-pre-wrap break-words">
          {item.text}
        </div>
      </div>
    )
  }

  if (item.role === 'system') {
    return (
      <div className="flex justify-center mb-2">
        <div className="rounded-full bg-yellow-500/20 px-3 py-1 text-yellow-300/80 text-xs">
          {item.text}
        </div>
      </div>
    )
  }

  if (item.role === 'tool' || item.role === 'tool_result') {
    return (
      <div className="mb-1">
        <ToolCard item={item as TranscriptItem & { role: 'tool' | 'tool_result' }} />
      </div>
    )
  }

  if (item.role === 'thinking') {
    return (
      <div className="mb-1">
        <InlineThinkingBlock content={item.text} />
      </div>
    )
  }

  if (item.role === 'log') {
    return (
      <div className="mb-1 px-1 text-zinc-500 text-xs font-mono">
        {item.text}
      </div>
    )
  }

  return null
}

// ── StreamingIndicator ─────────────────────────────────────────────────────────

function StreamingThinkingIndicator({ text }: { text: string }) {
  if (!text) return null
  const preview = text.length > 120 ? '…' + text.slice(-120) : text
  return (
    <div className="mb-1">
      <div className="rounded-lg border border-amber-900/50 bg-amber-950/30 px-3 py-2 text-sm">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-amber-400 text-xs">✦ Thinking</span>
          <span className="inline-block w-2 h-3 bg-amber-400/60 ml-0.5 animate-pulse" />
        </div>
        <pre className="text-xs font-mono text-zinc-400 whitespace-pre-wrap break-words">{preview}</pre>
      </div>
    </div>
  )
}

function StreamingIndicator({ text }: { text: string }) {
  if (!text) return null
  return (
    <div className="flex justify-start mb-3">
      <div className="max-w-[85%] rounded-2xl rounded-tl-sm bg-zinc-800 px-4 py-2.5 text-zinc-100 text-sm whitespace-pre-wrap break-words">
        {text}
        <span className="inline-block w-2 h-4 bg-zinc-300 ml-0.5 align-text-bottom animate-pulse" />
      </div>
    </div>
  )
}

// ── PromptInput ────────────────────────────────────────────────────────────────

function PromptInput({
  onSubmit,
  disabled,
  busy,
  agents,
  sandboxes,
  pipelines,
  selectedRunWith,
  selectedSandbox,
  onRunWithChange,
  onSandboxChange,
}: {
  onSubmit: (line: string, options?: { agent_name?: string; sandbox_id?: string }) => void
  disabled: boolean
  busy: boolean
  agents: AgentSummary[]
  sandboxes: SandboxInfo[]
  pipelines: PipelineConfig[]
  selectedRunWith: string
  selectedSandbox: string
  onRunWithChange: (v: string) => void
  onSandboxChange: (v: string) => void
}) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Parse selectedRunWith: "agent:<name>" or "pipeline:<id>" or ""
  const selectedAgent = selectedRunWith.startsWith('agent:') ? selectedRunWith.slice(6) : ''

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  // Auto-resize textarea up to ~6 lines
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const lineHeight = 24
    const maxHeight = lineHeight * 6 + 16 // 6 lines + padding
    el.style.height = Math.min(el.scrollHeight, maxHeight) + 'px'
  }, [value])

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleSubmit = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    const opts: { agent_name?: string; sandbox_id?: string } = {}
    if (selectedAgent) opts.agent_name = selectedAgent
    if (selectedSandbox) opts.sandbox_id = selectedSandbox
    onSubmit(trimmed, Object.keys(opts).length > 0 ? opts : undefined)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const hasSelectors = agents.length > 0 || sandboxes.length > 0 || pipelines.length > 0

  return (
    <div className="border-t border-zinc-800 bg-zinc-950 px-4 py-3">
      <div className="max-w-4xl mx-auto">
        {/* Run with & Sandbox selectors */}
        {hasSelectors && (
          <div className="flex items-center gap-3 mb-2">
            {(agents.length > 0 || pipelines.length > 0) && (
              <div className="flex items-center gap-1.5">
                <label className="text-xs text-zinc-500">Run with</label>
                <select
                  value={selectedRunWith}
                  onChange={e => onRunWithChange(e.target.value)}
                  className="rounded-lg bg-zinc-800 border border-zinc-700 px-2 py-1 text-xs text-zinc-300 outline-none focus:ring-1 focus:ring-blue-500 max-w-[220px]"
                >
                  <option value="">Default Agent</option>
                  {agents.length > 0 && (
                    <optgroup label="Agents">
                      {agents.map(a => (
                        <option key={a.name} value={`agent:${a.name}`}>
                          {a.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {pipelines.length > 0 && (
                    <optgroup label="Pipelines">
                      {pipelines.map(p => (
                        <option key={p.pipeline_id} value={`pipeline:${p.pipeline_id}`}>
                          {p.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </select>
              </div>
            )}
            {sandboxes.length > 0 && (
              <div className="flex items-center gap-1.5">
                <label className="text-xs text-zinc-500">Sandbox</label>
                <select
                  value={selectedSandbox}
                  onChange={e => onSandboxChange(e.target.value)}
                  className="rounded-lg bg-zinc-800 border border-zinc-700 px-2 py-1 text-xs text-zinc-300 outline-none focus:ring-1 focus:ring-blue-500 max-w-[180px]"
                >
                  <option value="">None</option>
                  {sandboxes.map(s => (
                    <option key={s.id} value={s.id} disabled={s.state !== 'started'}>
                      {s.name} {s.state !== 'started' ? `(${s.state})` : ''}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {(selectedRunWith || selectedSandbox) && (
              <button
                onClick={() => { onRunWithChange(''); onSandboxChange('') }}
                className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        )}

        {/* Input row */}
        <div className="flex items-end gap-3">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder={disabled ? 'Connecting…' : 'Send a message… (Shift+Enter for newline)'}
            rows={1}
            className="flex-1 resize-none rounded-xl bg-zinc-800 px-4 py-2.5 text-sm text-zinc-100 placeholder-zinc-500 outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed min-h-[42px] leading-6"
          />
          <button
            onClick={handleSubmit}
            disabled={disabled || !value.trim()}
            className="shrink-0 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors min-h-[42px] flex items-center gap-2"
          >
            {busy ? (
              <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
            ) : (
              <span>Send</span>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── PermissionModal ────────────────────────────────────────────────────────────

function PermissionModal({
  toolName,
  reason,
  requestId,
  onRespond,
}: {
  toolName?: string
  reason?: string
  requestId: string
  onRespond: (id: string, allowed: boolean) => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-2xl bg-zinc-900 border border-zinc-700 p-6 shadow-2xl">
        <h2 className="text-base font-semibold text-zinc-100 mb-1">Permission Request</h2>
        {toolName && (
          <span className="inline-block font-mono text-xs bg-zinc-700 text-zinc-300 px-2 py-0.5 rounded mb-3">
            {toolName}
          </span>
        )}
        {reason && (
          <p className="text-sm text-zinc-300 mb-5 whitespace-pre-wrap">{reason}</p>
        )}
        <div className="flex gap-3 justify-end">
          <button
            onClick={() => onRespond(requestId, false)}
            className="px-4 py-2 rounded-lg bg-red-900/60 text-red-300 text-sm font-medium hover:bg-red-800 transition-colors"
          >
            Deny
          </button>
          <button
            onClick={() => onRespond(requestId, true)}
            className="px-4 py-2 rounded-lg bg-green-800/60 text-green-300 text-sm font-medium hover:bg-green-700 transition-colors"
          >
            Allow
          </button>
        </div>
      </div>
    </div>
  )
}

// ── QuestionModal ──────────────────────────────────────────────────────────────

function QuestionModal({
  question,
  requestId,
  onRespond,
}: {
  question?: string
  requestId: string
  onRespond: (id: string, answer: string) => void
}) {
  const [answer, setAnswer] = useState('')

  const handleSubmit = () => {
    if (!answer.trim()) return
    onRespond(requestId, answer.trim())
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-2xl bg-zinc-900 border border-zinc-700 p-6 shadow-2xl">
        <h2 className="text-base font-semibold text-zinc-100 mb-3">Question</h2>
        {question && (
          <p className="text-sm text-zinc-300 mb-4 whitespace-pre-wrap">{question}</p>
        )}
        <input
          type="text"
          value={answer}
          onChange={e => setAnswer(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSubmit()}
          autoFocus
          placeholder="Your answer…"
          className="w-full rounded-lg bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 outline-none focus:ring-1 focus:ring-blue-500 mb-4"
        />
        <div className="flex justify-end">
          <button
            onClick={handleSubmit}
            disabled={!answer.trim()}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-500 disabled:opacity-40 transition-colors"
          >
            Submit
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Pipeline run — rendered like normal agent messages ─────────────────────────

function PipelineRunInline({ run }: { run: PipelineRun }) {
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set())

  const toggleStep = (name: string) => {
    setExpandedSteps(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const dotColor: Record<string, string> = {
    pending: 'bg-zinc-600',
    running: 'bg-blue-500 animate-pulse',
    completed: 'bg-emerald-500',
    failed: 'bg-red-500',
    skipped: 'bg-zinc-700',
  }

  return (
    <>
      {/* Pipeline header — system-style message */}
      <div className="flex justify-start mb-4">
        <div className="max-w-[85%] rounded-2xl rounded-tl-md bg-zinc-800/80 px-4 py-2.5 text-sm text-zinc-300 border border-zinc-700">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-semibold text-blue-400">Pipeline</span>
            <span className="text-xs text-zinc-500">{run.step_records.length} steps</span>
          </div>
          {/* Step progress dots */}
          <div className="flex items-center gap-3">
            {run.step_records.map((step) => (
              <div key={step.name} className="flex items-center gap-1" title={`${step.name}: ${step.status}`}>
                <div className={`h-2 w-2 rounded-full ${dotColor[step.status] ?? 'bg-zinc-600'}`} />
                <span className="text-[11px] text-zinc-500">{step.name}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Each completed/running step rendered as an assistant message */}
      {run.step_records
        .filter(s => s.status !== 'pending' && s.status !== 'skipped')
        .map((step) => {
          const output = run.context_map[step.name]
          const responseText = step.metrics?.response_text ? String(step.metrics.response_text) : null
          const isRunning = step.status === 'running'
          const isFailed = step.status === 'failed'
          const isExpanded = expandedSteps.has(step.name)

          return (
            <div key={step.name} className="flex justify-start mb-4">
              <div className="max-w-[85%] rounded-2xl rounded-tl-md bg-zinc-800 px-4 py-3 text-sm text-zinc-200">
                {/* Step header */}
                <div className="flex items-center gap-2 mb-1.5">
                  <div className={`h-2 w-2 rounded-full ${dotColor[step.status]}`} />
                  <span className="text-xs font-semibold text-zinc-400">{step.name}</span>
                  <span className="text-[10px] text-zinc-600">({step.agent})</span>
                  {step.started_at && step.finished_at && (
                    <span className="text-[10px] text-zinc-600">
                      {(step.finished_at - step.started_at).toFixed(1)}s
                    </span>
                  )}
                </div>

                {/* Running indicator */}
                {isRunning && (
                  <div className="flex items-center gap-2 text-zinc-400">
                    <span className="inline-block w-3 h-3 border-2 border-zinc-600 border-t-blue-400 rounded-full animate-spin" />
                    <span className="text-xs">Running...</span>
                  </div>
                )}

                {/* Failed error */}
                {isFailed && step.error && (
                  <p className="text-xs text-red-400">{step.error}</p>
                )}

                {/* Agent response text */}
                {responseText && (
                  <div className="mt-1 text-sm text-zinc-200 whitespace-pre-wrap">
                    {responseText.length > 500 && !isExpanded
                      ? responseText.slice(0, 500) + '...'
                      : responseText}
                    {responseText.length > 500 && (
                      <button
                        onClick={() => toggleStep(step.name)}
                        className="ml-1 text-xs text-blue-400 hover:text-blue-300"
                      >
                        {isExpanded ? 'Show less' : 'Show more'}
                      </button>
                    )}
                  </div>
                )}

                {/* Session link + structured output toggle */}
                <div className="mt-2 flex items-center gap-3">
                  {step.work_session_id && (
                    <a
                      href={`/sessions/${step.work_session_id}/runs`}
                      className="text-[10px] text-zinc-600 hover:text-zinc-400 font-mono"
                    >
                      {step.work_session_id}
                    </a>
                  )}
                  {output && (
                    <button
                      onClick={() => toggleStep(step.name + '-json')}
                      className="text-[10px] text-zinc-600 hover:text-zinc-400"
                    >
                      {expandedSteps.has(step.name + '-json') ? 'Hide JSON' : 'Show JSON'}
                    </button>
                  )}
                </div>
                {output && expandedSteps.has(step.name + '-json') && (
                  <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-zinc-950 p-3 text-xs text-zinc-400">
                    {JSON.stringify(output, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          )
        })}

      {/* Pipeline completion / error summary */}
      {(run.status === 'completed' || run.status === 'failed') && (
        <div className="flex justify-start mb-4">
          <div className={`max-w-[85%] rounded-2xl rounded-tl-md px-4 py-2.5 text-sm border ${
            run.status === 'completed'
              ? 'bg-emerald-900/20 border-emerald-800 text-emerald-300'
              : 'bg-red-900/20 border-red-800 text-red-300'
          }`}>
            {run.status === 'completed'
              ? `Pipeline completed (${run.completed_steps.length} steps)`
              : `Pipeline failed: ${run.error}`}
          </div>
        </div>
      )}
    </>
  )
}

// ── ConversationPage ───────────────────────────────────────────────────────────

export default function ConversationPage() {
  const { items, streamingText, streamingThinking, busy, submitLine } = useTranscript()
  const { modal, respondPermission, respondQuestion } = useModal()
  const connected = useConnected()

  const sentinelRef = useRef<HTMLDivElement>(null)
  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [sandboxes, setSandboxes] = useState<SandboxInfo[]>([])
  const [selectedRunWith, setSelectedRunWith] = useState('')
  const [selectedSandbox, setSelectedSandbox] = useState('')
  const [pipelines, setPipelines] = useState<PipelineConfig[]>([])
  const [activePipelineRun, setActivePipelineRun] = useState<PipelineRun | null>(null)
  const [pipelineGoalText, setPipelineGoalText] = useState<string | null>(null)

  // Fetch agents, sandboxes, and pipelines when connected
  useEffect(() => {
    if (!connected) return
    fetch('/api/agents')
      .then(r => r.ok ? r.json() : [])
      .then(data => setAgents(Array.isArray(data) ? data : []))
      .catch(() => {})
    fetch('/api/sandboxes')
      .then(r => r.ok ? r.json() : [])
      .then(data => setSandboxes(Array.isArray(data) ? data : []))
      .catch(() => {})
    fetchPipelines().then(setPipelines).catch(() => {})
  }, [connected])

  // Poll active pipeline run
  useEffect(() => {
    if (!activePipelineRun) return
    if (activePipelineRun.status !== 'running' && activePipelineRun.status !== 'pending') return
    const id = setInterval(async () => {
      const updated = await fetchPipelineRun(activePipelineRun.run_id)
      if (updated) setActivePipelineRun(updated)
    }, 2000)
    return () => clearInterval(id)
  }, [activePipelineRun?.run_id, activePipelineRun?.status])

  // Auto-scroll to bottom when items or streaming text change
  useEffect(() => {
    sentinelRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [items, streamingText, streamingThinking])

  const handleSubmit = useCallback(async (line: string, options?: { agent_name?: string; sandbox_id?: string }) => {
    if (selectedRunWith.startsWith('pipeline:')) {
      const pipelineId = selectedRunWith.slice(9)
      // Show the user's message in the conversation
      setPipelineGoalText(line)
      // Start pipeline execution — returns run_id immediately
      const { run_id } = await startPipelineRun(pipelineId, line)
      // Fetch the pre-created run and start polling
      const run = await fetchPipelineRun(run_id)
      if (run) setActivePipelineRun(run)
      return
    }
    submitLine(line, options)
  }, [submitLine, selectedRunWith])

  const isEmpty = items.length === 0 && !streamingText && !streamingThinking && !pipelineGoalText && !activePipelineRun

  return (
    <div className="flex flex-col h-full bg-zinc-950">
      {/* Conversation area */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="max-w-4xl mx-auto">
          {isEmpty ? (
            <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
              <p className="text-base">Send a message to get started</p>
            </div>
          ) : (
            <>
              {items.map((item, i) => (
                <MessageBubble key={i} item={item} />
              ))}
              <StreamingThinkingIndicator text={streamingThinking} />
              <StreamingIndicator text={streamingText} />
              {pipelineGoalText && (
                <MessageBubble item={{ role: 'user', text: pipelineGoalText }} />
              )}
              {activePipelineRun && <PipelineRunInline run={activePipelineRun} />}
            </>
          )}
          <div ref={sentinelRef} />
        </div>
      </div>

      {/* Modals */}
      {modal?.kind === 'permission' && (
        <PermissionModal
          toolName={modal.tool_name}
          reason={modal.reason}
          requestId={modal.request_id}
          onRespond={respondPermission}
        />
      )}
      {modal?.kind === 'question' && (
        <QuestionModal
          question={modal.question}
          requestId={modal.request_id}
          onRespond={respondQuestion}
        />
      )}

      {/* Input */}
      <PromptInput
        onSubmit={handleSubmit}
        disabled={!connected || busy}
        busy={busy}
        agents={agents}
        sandboxes={sandboxes}
        pipelines={pipelines}
        selectedRunWith={selectedRunWith}
        selectedSandbox={selectedSandbox}
        onRunWithChange={setSelectedRunWith}
        onSandboxChange={setSelectedSandbox}
      />
    </div>
  )
}
