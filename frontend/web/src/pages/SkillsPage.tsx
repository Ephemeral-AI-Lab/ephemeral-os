import { useCallback, useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SkillSummary {
  name: string
  description: string
}

interface SkillDetail extends SkillSummary {
  content: string
  version: number
  created_at: string | null
  updated_at: string | null
}

interface FileTreeEntry {
  name: string
  type: 'file' | 'directory'
  path: string
  size?: number
  children?: FileTreeEntry[]
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

const API = '/api/skills'

async function fetchSkills(): Promise<SkillSummary[]> {
  const res = await fetch(API)
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

async function fetchSkill(name: string): Promise<SkillDetail> {
  const res = await fetch(`${API}/${encodeURIComponent(name)}`)
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

async function deleteSkill(name: string): Promise<void> {
  const res = await fetch(`${API}/${encodeURIComponent(name)}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
}

async function fetchSkillFiles(name: string): Promise<FileTreeEntry[]> {
  const res = await fetch(`${API}/${encodeURIComponent(name)}/files`)
  if (!res.ok) return []
  const data = await res.json()
  return data.tree ?? []
}

async function fetchSkillFileContent(name: string, filePath: string): Promise<string> {
  const res = await fetch(`${API}/${encodeURIComponent(name)}/files/${filePath}`)
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.text()
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function TreeNode({
  entry,
  depth = 0,
  onFileClick,
}: {
  entry: FileTreeEntry
  depth?: number
  onFileClick: (path: string) => void
}) {
  const [open, setOpen] = useState(depth < 2)
  const isDir = entry.type === 'directory'

  return (
    <div>
      <button
        onClick={() => isDir ? setOpen(o => !o) : onFileClick(entry.path)}
        className={`flex items-center gap-2 w-full text-left py-1 px-2 rounded text-xs transition-colors ${
          isDir ? 'hover:bg-zinc-800 cursor-pointer' : 'hover:bg-zinc-800/50 cursor-pointer'
        }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {isDir ? (
          <span className="text-zinc-500 w-3 text-center shrink-0">{open ? '▾' : '▸'}</span>
        ) : (
          <span className="w-3" />
        )}
        <span className={`font-mono truncate ${isDir ? 'font-semibold text-zinc-200' : 'text-zinc-400'}`}>
          {entry.name}
        </span>
        {!isDir && entry.size != null && (
          <span className="ml-auto text-[10px] text-zinc-600 font-mono shrink-0">
            {formatSize(entry.size)}
          </span>
        )}
      </button>
      {isDir && open && entry.children && entry.children.map(child => (
        <TreeNode key={child.path} entry={child} depth={depth + 1} onFileClick={onFileClick} />
      ))}
    </div>
  )
}

function SkillCard({
  skill,
  onSelect,
}: {
  skill: SkillSummary
  onSelect: () => void
}) {
  return (
    <div
      className="rounded-lg bg-zinc-900 border border-zinc-800 px-4 py-3 hover:border-zinc-600 transition-colors cursor-pointer"
      onClick={onSelect}
    >
      <div className="min-w-0">
        <span className="text-sm font-medium text-zinc-100 hover:text-emerald-400 transition-colors font-mono">
          {skill.name}
        </span>
        <p className="mt-1 text-xs text-zinc-500 line-clamp-2">{skill.description}</p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Skill Detail View
// ---------------------------------------------------------------------------

function SkillDetailView({
  skill,
  onBack,
  onDelete,
}: {
  skill: SkillDetail
  onBack: () => void
  onDelete: () => void
}) {
  const [fileTree, setFileTree] = useState<FileTreeEntry[]>([])
  const [selectedFile, setSelectedFile] = useState<{ path: string; content: string } | null>(null)
  const [fileLoading, setFileLoading] = useState(false)

  useEffect(() => {
    fetchSkillFiles(skill.name).then(setFileTree)
  }, [skill.name])

  const handleFileClick = async (path: string) => {
    setFileLoading(true)
    try {
      const content = await fetchSkillFileContent(skill.name, path)
      setSelectedFile({ path, content })
    } catch {
      setSelectedFile({ path, content: 'Failed to load file' })
    } finally {
      setFileLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button className="text-xs text-zinc-500 hover:text-zinc-300" onClick={onBack}>
            &larr; Back
          </button>
          <h2 className="text-lg font-semibold text-zinc-100 font-mono">{skill.name}</h2>
          {skill.version && (
            <span className="text-xs text-zinc-600">v{skill.version}</span>
          )}
        </div>
        <button
          className="rounded px-2 py-1 text-xs text-red-500 border border-red-800/50 hover:text-red-300 hover:border-red-700"
          onClick={onDelete}
        >
          Delete
        </button>
      </div>

      <p className="text-sm text-zinc-400">{skill.description}</p>

      {skill.created_at && (
        <div className="text-xs text-zinc-600">
          Created: {new Date(skill.created_at).toLocaleDateString()}
          {skill.updated_at && <> · Updated: {new Date(skill.updated_at).toLocaleDateString()}</>}
        </div>
      )}

      <section>
        <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">Content</h3>
        <pre className="max-h-[40vh] overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-4 text-xs text-zinc-300 font-mono whitespace-pre-wrap leading-relaxed">
          {skill.content}
        </pre>
      </section>

      {/* File Tree */}
      {fileTree.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">Files</h3>
          <div className="rounded-lg border border-zinc-800 bg-zinc-950 py-2 max-h-60 overflow-y-auto">
            {fileTree.map(entry => (
              <TreeNode key={entry.path} entry={entry} onFileClick={handleFileClick} />
            ))}
          </div>
        </section>
      )}

      {/* File Viewer */}
      {selectedFile && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide">
              {selectedFile.path}
            </h3>
            <button
              className="text-xs text-zinc-600 hover:text-zinc-300"
              onClick={() => setSelectedFile(null)}
            >
              Close
            </button>
          </div>
          {fileLoading ? (
            <div className="text-center text-sm text-zinc-500 py-4">Loading...</div>
          ) : (
            <pre className="max-h-[40vh] overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-4 text-xs text-zinc-300 font-mono whitespace-pre-wrap leading-relaxed">
              {selectedFile.content}
            </pre>
          )}
        </section>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [selectedSkill, setSelectedSkill] = useState<SkillDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<'list' | 'detail'>('list')

  const refresh = useCallback(async () => {
    try {
      const data = await fetchSkills()
      setSkills(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const selectSkill = async (name: string) => {
    try {
      const detail = await fetchSkill(name)
      setSelectedSkill(detail)
      setView('detail')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete skill "${name}"?`)) return
    try {
      await deleteSkill(name)
      await refresh()
      setSelectedSkill(null)
      setView('list')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="min-h-full bg-zinc-950 text-zinc-100 p-6 space-y-6">
      {/* List View */}
      {view === 'list' && (
        <>
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-lg font-semibold">Skills</h1>
              <p className="text-xs text-zinc-500 mt-0.5">
                {loading ? '...' : `${skills.length} skill${skills.length !== 1 ? 's' : ''} loaded`}
              </p>
            </div>
            <button
              className="rounded px-3 py-1.5 text-xs font-medium text-zinc-400 border border-zinc-700 hover:text-zinc-200 hover:border-zinc-500"
              onClick={refresh}
            >
              Refresh
            </button>
          </div>

          {error && (
            <div className="rounded-lg border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-300">
              {error}
            </div>
          )}

          {loading ? (
            <div className="text-center text-sm text-zinc-500 py-8">Loading...</div>
          ) : skills.length === 0 ? (
            <div className="rounded-lg border border-zinc-800 px-4 py-8 text-center text-sm text-zinc-600">
              No skills found
            </div>
          ) : (
            <div className="space-y-2">
              {skills.map(skill => (
                <SkillCard
                  key={skill.name}
                  skill={skill}
                  onSelect={() => selectSkill(skill.name)}
                />
              ))}
            </div>
          )}
        </>
      )}

      {/* Detail View */}
      {view === 'detail' && selectedSkill && (
        <SkillDetailView
          skill={selectedSkill}
          onBack={() => setView('list')}
          onDelete={() => handleDelete(selectedSkill.name)}
        />
      )}
    </div>
  )
}
