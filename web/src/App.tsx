import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Connection, EdgeChange, Node } from 'reactflow'

import { appUrl, cancelRun, createCheckpoint, currentProject, dismissNotice, executionLogDownloadUrl, getSnapshot, listSessions, patchGraph, restoreCheckpoint, runAll, runNode, stopSession, uploadFile } from './lib/api'
import { GRID_SIZE, activeRunNodeId, artifactCounts, artifactFor, badgeForNode, currentRun, formatBytes, formatDurationSeconds, formatTimestamp, globalArtifactCounts, hiddenInputNames, inputBindingSource, inputState, queuedRunNodeIds, templateByRef } from './lib/helpers'
import type { ArtifactRecord, NodeRecord, NoticeRecord, ProjectSnapshot, TemplateRecord } from './lib/types'
import { ConfirmDialog, CreateConstantValueDialog, CreateFileDialog, CreateNotebookDialog, CreatePipelineDialog, Modal } from './components/Dialogs'
import { ArtifactPreviewPanel } from './components/ArtifactPreview'
import { ArtifactCounts } from './components/ArtifactCounts'
import { GraphCanvas } from './components/GraphCanvas'
import { PortPill } from './components/PortPill'
import { Download, Plus, Info, Palette, Play, X } from './components/Icons'

type ThemeMode = 'system' | 'light' | 'dark'

type PaletteEntry = {
  key: string
  title: string
  description: string
  kind: 'empty' | 'value_input' | 'file_input' | 'template' | 'pipeline'
  templateRef?: string
}

type PendingBlockCreation = {
  entry: PaletteEntry
  x: number
  y: number
}

type FileNodeEditState = {
  nodeId: string
  title: string
  artifactName: string
}

type NodeActionMenuState = {
  nodeId: string
  x: number
  y: number
}

type PendingPipelineCreation = {
  entry: PaletteEntry
  x: number
  y: number
  template: TemplateRecord
  suggestedPrefix: string
  requirePrefix: boolean
}

const NEW_NODE_WIDTH = 360
const NEW_NODE_HEIGHT = 220

type ConstantValueType = 'int' | 'float' | 'bool' | 'str' | 'list' | 'dict' | 'object'
type BlockCreateMode = 'notebook' | 'constant_value' | 'file' | 'pipeline'

type AppNotice = NoticeRecord & {
  origin: 'snapshot' | 'client'
}

type EditorSessionNoticeDetails = {
  session_id: string
  session_url: string
  ready?: boolean
}

type ConfirmationState =
  | {
      kind: 'run-upstream'
      nodeId: string
      mode: 'run_stale' | 'run_all'
      message: string
    }
  | {
      kind: 'run-all'
    }

type OptimisticGraphState = {
  snapshot: ProjectSnapshot
  clearSelection?: boolean
  clearArtifacts?: boolean
}

type SnapshotLike = Pick<ProjectSnapshot, 'project' | 'graph' | 'validation_issues' | 'notices' | 'artifacts' | 'runs' | 'checkpoints' | 'templates'>


function blockCreateMode(entry: PaletteEntry): BlockCreateMode | null {
  if (entry.kind === 'pipeline') {
    return 'pipeline'
  }
  if (entry.kind === 'value_input') {
    return 'constant_value'
  }
  if (entry.kind === 'file_input') {
    return 'file'
  }
  return 'notebook'
}


function normalizeNodeId(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

function artifactEndpoint(artifact: ArtifactRecord, action: 'download' | 'content'): string {
  const nodeId = encodeURIComponent(artifact.node_id)
  const artifactName = encodeURIComponent(artifact.artifact_name)
  return appUrl(`/api/v1/artifacts/${nodeId}/${artifactName}/${action}`)
}

const DATAFRAME_CSV_DOWNLOAD_MAX_BYTES = 100_000_000

function createClientNotice(
  issueId: string,
  severity: 'error' | 'warning',
  code: string,
  message: string,
  options: { nodeId?: string | null; details?: Record<string, unknown> } = {},
): AppNotice {
  return {
    issue_id: issueId,
    node_id: options.nodeId ?? null,
    severity,
    code,
    message,
    details: options.details ?? {},
    created_at: new Date().toISOString(),
    origin: 'client',
  }
}

function runFailureMessage(response: Record<string, unknown>, fallback: string): string {
  const directError = response.error
  if (typeof directError === 'string' && directError.trim()) {
    return directError
  }
  const nodeResults = response.node_results
  if (nodeResults && typeof nodeResults === 'object') {
    const nestedError = (nodeResults as { error?: unknown }).error
    if (typeof nestedError === 'string' && nestedError.trim()) {
      return nestedError
    }
  }
  return fallback
}

function ExecutionLogPanel({
  title,
  log,
  nodeId,
  filenameSuffix,
}: {
  title: string
  log: { text: string; truncated: boolean }
  nodeId: string
  filenameSuffix: 'stdout' | 'stderr'
}) {
  return (
    <div className="inspector-subblock">
      <div className="panel-header-row execution-log-header">
        <strong>{title}</strong>
        <a className="secondary small link-button" href={executionLogDownloadUrl(nodeId, filenameSuffix)}>
          <Download width={14} height={14} />
          Download
        </a>
      </div>
      <pre className="code-block docs-block execution-log-block">{log.text}</pre>
      {log.truncated ? <p className="muted-copy">Preview truncated by the server to 50 lines or 10k characters.</p> : null}
    </div>
  )
}

function isEditorOpenConflict(message: string): boolean {
  return message.includes('An editor is open for this notebook.')
}

function editorSessionDetails(details: Record<string, unknown>): EditorSessionNoticeDetails | null {
  if (typeof details.session_id !== 'string' || typeof details.session_url !== 'string') {
    return null
  }
  return {
    session_id: details.session_id,
    session_url: details.session_url,
    ready: typeof details.ready === 'boolean' ? details.ready : undefined,
  }
}

function cloneSnapshot(snapshot: ProjectSnapshot): ProjectSnapshot {
  return {
    ...snapshot,
    project: { ...snapshot.project },
    graph: {
      meta: { ...snapshot.graph.meta },
      nodes: snapshot.graph.nodes.map((node) => ({
        ...node,
        template: node.template ? { ...node.template } : node.template,
        ui: node.ui ? { ...node.ui } : node.ui,
        interface: node.interface
          ? {
              ...node.interface,
              inputs: node.interface.inputs.map((port) => ({ ...port })),
              outputs: node.interface.outputs.map((port) => ({ ...port })),
              assets: node.interface.assets.map((port) => ({ ...port })),
              issues: node.interface.issues.map((issue) => ({ ...issue })),
            }
          : node.interface,
      })),
      edges: snapshot.graph.edges.map((edge) => ({ ...edge })),
      layout: snapshot.graph.layout.map((entry) => ({ ...entry })),
    },
    validation_issues: snapshot.validation_issues.map((issue) => ({ ...issue })),
    notices: snapshot.notices.map((notice) => ({ ...notice })),
    artifacts: snapshot.artifacts.map((artifact) => ({ ...artifact, warnings: artifact.warnings.map((warning) => ({ ...warning })) })),
    runs: snapshot.runs.map((run) => ({ ...run, target_json: run.target_json, source_snapshot_json: run.source_snapshot_json })),
    checkpoints: snapshot.checkpoints.map((checkpoint) => ({ ...checkpoint })),
    templates: snapshot.templates.map((template) => ({ ...template })),
  }
}

function mergeGraphIntoSnapshot(snapshot: SnapshotLike, graph: { meta: ProjectSnapshot['graph']['meta']; nodes: ProjectSnapshot['graph']['nodes']; edges: ProjectSnapshot['graph']['edges']; layout: ProjectSnapshot['graph']['layout'] }): ProjectSnapshot {
  const merged = cloneSnapshot(snapshot as ProjectSnapshot)
  merged.graph = {
    meta: { ...graph.meta },
    nodes: graph.nodes.map((node) => ({
      ...node,
      template: node.template ? { ...node.template } : node.template,
      ui: node.ui ? { ...node.ui } : node.ui,
      interface: node.interface
        ? {
            ...node.interface,
            inputs: node.interface.inputs.map((port) => ({ ...port })),
            outputs: node.interface.outputs.map((port) => ({ ...port })),
            assets: node.interface.assets.map((port) => ({ ...port })),
            issues: node.interface.issues.map((issue) => ({ ...issue })),
          }
        : node.interface,
    })),
    edges: graph.edges.map((edge) => ({ ...edge })),
    layout: graph.layout.map((entry) => ({ ...entry })),
  }
  return merged
}

function applyOptimisticGraphOperations(snapshot: ProjectSnapshot, operations: Array<Record<string, unknown>>): OptimisticGraphState | null {
  const next = cloneSnapshot(snapshot)
  let changed = false
  let clearSelection = false
  let clearArtifacts = false

  for (const operation of operations) {
    const type = operation.type
    if (type === 'add_pipeline_template') {
      continue
    }
    if (type === 'update_node_layout') {
      const nodeId = String(operation.node_id)
      const layout = next.graph.layout.find((entry) => entry.node_id === nodeId)
      if (layout) {
        layout.x = Number(operation.x)
        layout.y = Number(operation.y)
        changed = true
      }
      continue
    }
    if (type === 'delete_node') {
      const nodeId = String(operation.node_id)
      next.graph.nodes = next.graph.nodes.filter((node) => node.id !== nodeId)
      next.graph.layout = next.graph.layout.filter((entry) => entry.node_id !== nodeId)
      next.graph.edges = next.graph.edges.filter((edge) => edge.source_node !== nodeId && edge.target_node !== nodeId)
      next.artifacts = next.artifacts.filter((artifact) => artifact.node_id !== nodeId)
      next.validation_issues = next.validation_issues.filter((issue) => issue.node_id !== nodeId)
      next.notices = next.notices.filter((notice) => notice.node_id !== nodeId)
      clearSelection = true
      clearArtifacts = true
      changed = true
      continue
    }
    if (type === 'remove_edge') {
      const edgeId = String(operation.edge_id)
      next.graph.edges = next.graph.edges.filter((edge) => edge.id !== edgeId)
      changed = true
      continue
    }
    if (type === 'add_edge') {
      const sourceNode = String(operation.source_node)
      const sourcePort = String(operation.source_port)
      const targetNode = String(operation.target_node)
      const targetPort = String(operation.target_port)
      const id = `${sourceNode}.${sourcePort}__${targetNode}.${targetPort}`
      if (!next.graph.edges.some((edge) => edge.id === id)) {
        next.graph.edges.push({ id, source_node: sourceNode, source_port: sourcePort, target_node: targetNode, target_port: targetPort })
        changed = true
      }
      continue
    }
    if (type === 'update_node_hidden_inputs') {
      const node = next.graph.nodes.find((entry) => entry.id === String(operation.node_id))
      if (node) {
        node.ui = { ...(node.ui ?? {}), hidden_inputs: Array.isArray(operation.hidden_inputs) ? operation.hidden_inputs.map(String) : [] }
        changed = true
      }
    }
  }

  if (!changed) {
    return null
  }

  next.graph.meta = {
    ...next.graph.meta,
    graph_version: next.graph.meta.graph_version + 1,
    updated_at: new Date().toISOString(),
  }

  return {
    snapshot: next,
    clearSelection,
    clearArtifacts,
  }
}

function setSnapshotData(queryClient: ReturnType<typeof useQueryClient>, updater: (current: ProjectSnapshot) => ProjectSnapshot) {
  queryClient.setQueryData(['snapshot'], (current: ProjectSnapshot | undefined) => {
    if (!current) {
      return current
    }
    return updater(current)
  })
  queryClient.setQueryData(['project-current'], (current: ProjectSnapshot | undefined) => {
    if (!current) {
      return current
    }
    return updater(current)
  })
}

const SNAPSHOT_REFRESH_EVENTS = [
  'artifact.state_changed',
  'checkpoint.created',
  'checkpoint.restored',
  'graph.updated',
  'notebook.reparsed',
  'notice.created',
  'notice.dismissed',
  'project.opened',
  'run.failed',
  'run.finished',
  'run.progress',
  'run.queued',
  'run.started',
  'validation.updated',
]

function validationIssuesForNode(snapshot: ProjectSnapshot, nodeId: string) {
  return snapshot.validation_issues.filter((issue) => issue.node_id === nodeId)
}

function formatIssueDetails(details: Record<string, unknown>): string | null {
  const entries = Object.entries(details)
  if (!entries.length) {
    return null
  }
  return JSON.stringify(details, null, 2)
}

function nodeRunFailures(snapshot: ProjectSnapshot, nodeId: string) {
  return snapshot.runs.filter((run) => {
    if (run.status !== 'failed' || !run.failure_json) {
      return false
    }
    if (typeof run.failure_json.node_id === 'string') {
      return run.failure_json.node_id === nodeId
    }
    const target = run.target_json
    if (typeof target.node_id === 'string') {
      return target.node_id === nodeId
    }
    if (Array.isArray(target.plan)) {
      return target.plan.includes(nodeId)
    }
    if (Array.isArray(target.node_ids)) {
      return target.node_ids.includes(nodeId)
    }
    return false
  })
}

function App() {
  const queryClient = useQueryClient()
  const [clientNotices, setClientNotices] = useState<AppNotice[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [artifactNodeId, setArtifactNodeId] = useState<string | null>(null)
  const [artifactExplorerOpen, setArtifactExplorerOpen] = useState(false)
  const [artifactFilter, setArtifactFilter] = useState('')
  const [templateRefView, setTemplateRefView] = useState<string | null>(null)
  const [showProjectInfo, setShowProjectInfo] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [templatesCollapsed, setTemplatesCollapsed] = useState(true)
  const [paletteSearch, setPaletteSearch] = useState('')
  const [draggedPaletteEntry, setDraggedPaletteEntry] = useState<PaletteEntry | null>(null)
  const [dragPointer, setDragPointer] = useState<{ x: number; y: number } | null>(null)
  const [pendingBlockCreation, setPendingBlockCreation] = useState<PendingBlockCreation | null>(null)
  const [pendingPipelineCreation, setPendingPipelineCreation] = useState<PendingPipelineCreation | null>(null)
  const [fileNodeEdit, setFileNodeEdit] = useState<FileNodeEditState | null>(null)
  const [nodeActionMenu, setNodeActionMenu] = useState<NodeActionMenuState | null>(null)
  const [optimisticGraph, setOptimisticGraph] = useState<OptimisticGraphState | null>(null)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [confirmationState, setConfirmationState] = useState<ConfirmationState | null>(null)
  const [serverNowOffsetMs, setServerNowOffsetMs] = useState(0)
  const [activeEditorNodeIds, setActiveEditorNodeIds] = useState<string[]>([])
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => {
    const stored = window.localStorage.getItem('bulletjournal-theme')
    if (stored === 'light' || stored === 'dark' || stored === 'system') {
      return stored
    }
    return 'system'
  })
  const eventSourceRef = useRef<EventSource | null>(null)
  const hadEventConnectionRef = useRef(false)
  const startupSearch = useMemo(() => new URLSearchParams(window.location.search), [])
  const loadingSession = startupSearch.get('session_id')
    ? {
        sessionId: startupSearch.get('session_id') as string,
        nodeId: startupSearch.get('node_id') ?? 'notebook',
      }
    : null

  const projectQuery = useQuery({
    queryKey: ['project-current'],
    queryFn: currentProject,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  const snapshot = projectQuery.data ?? null
  const projectId = snapshot?.project.project_id ?? null

  const snapshotQuery = useQuery({
    queryKey: ['snapshot'],
    queryFn: () => getSnapshot(),
    enabled: Boolean(projectId),
  })

  const serverSnapshot = snapshotQuery.data ?? snapshot
  const liveSnapshot = optimisticGraph?.snapshot ?? serverSnapshot

  useEffect(() => {
    if (!liveSnapshot?.server_time) {
      return
    }
    const parsedServerTime = Date.parse(liveSnapshot.server_time)
    if (Number.isNaN(parsedServerTime)) {
      return
    }
    setServerNowOffsetMs(parsedServerTime - Date.now())
  }, [liveSnapshot?.server_time])

  function upsertClientNotice(notice: AppNotice) {
    setClientNotices((current) => {
      const withoutMatch = current.filter((item) => item.issue_id !== notice.issue_id)
      return [...withoutMatch, notice]
    })
  }

  function dismissClientNotice(issueId: string) {
    setClientNotices((current) => current.filter((notice) => notice.issue_id !== issueId))
  }

  function reportClientError(
    issueId: string,
    code: string,
    message: string,
    options: { nodeId?: string | null; details?: Record<string, unknown> } = {},
  ) {
    upsertClientNotice(createClientNotice(issueId, 'error', code, message, options))
  }

  function reportClientWarning(
    issueId: string,
    code: string,
    message: string,
    options: { nodeId?: string | null; details?: Record<string, unknown> } = {},
  ) {
    upsertClientNotice(createClientNotice(issueId, 'warning', code, message, options))
  }

  const overlayNotices = useMemo<AppNotice[]>(() => {
    const persisted = (liveSnapshot?.notices ?? []).map<AppNotice>((notice) => ({
      ...notice,
      origin: 'snapshot',
    }))
    return [...persisted, ...clientNotices].sort((left, right) => {
      const leftRank = left.severity === 'error' ? 0 : 1
      const rightRank = right.severity === 'error' ? 0 : 1
      if (leftRank !== rightRank) {
        return leftRank - rightRank
      }
      const createdDelta = new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
      if (createdDelta !== 0) {
        return createdDelta
      }
      return left.issue_id.localeCompare(right.issue_id)
    })
  }, [clientNotices, liveSnapshot])

  useEffect(() => {
    if (!serverSnapshot) {
      setOptimisticGraph(null)
      return
    }
    setOptimisticGraph((current) => {
      if (!current) {
        return null
      }
      if (current.snapshot.graph.meta.graph_version > serverSnapshot.graph.meta.graph_version) {
        return current
      }
      if (current.clearSelection) {
        setSelectedNodeId(null)
      }
      if (current.clearArtifacts) {
        setArtifactNodeId(null)
      }
      return null
    })
  }, [serverSnapshot])

  useEffect(() => {
    setInspectorOpen(Boolean(selectedNodeId))
  }, [selectedNodeId])

  useEffect(() => {
    const root = document.documentElement
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    function applyTheme() {
      const resolved = themeMode === 'system'
        ? (media.matches ? 'dark' : 'light')
        : themeMode
      root.dataset.theme = resolved
    }
    applyTheme()
    window.localStorage.setItem('bulletjournal-theme', themeMode)
    media.addEventListener('change', applyTheme)
    return () => media.removeEventListener('change', applyTheme)
  }, [themeMode])

  useEffect(() => {
    if (!draggedPaletteEntry) {
      setDragPointer(null)
      return
    }
    function handleDragOver(event: DragEvent) {
      setDragPointer({ x: event.clientX, y: event.clientY })
    }
    function clearDragState() {
      setDragPointer(null)
    }
    window.addEventListener('dragover', handleDragOver)
    window.addEventListener('drop', clearDragState)
    window.addEventListener('dragend', clearDragState)
    return () => {
      window.removeEventListener('dragover', handleDragOver)
      window.removeEventListener('drop', clearDragState)
      window.removeEventListener('dragend', clearDragState)
    }
  }, [draggedPaletteEntry])

  useEffect(() => {
    let cancelled = false
    async function waitForSession() {
      if (!loadingSession) {
        return
      }
      try {
        for (let attempt = 0; attempt < 60; attempt += 1) {
          const sessions = await listSessions()
          const session = sessions.find((item) => item.session_id === loadingSession.sessionId)
          if (session?.ready && typeof session.url === 'string') {
            window.location.replace(session.url)
            return
          }
          await new Promise((resolve) => window.setTimeout(resolve, 250))
          if (cancelled) {
            return
          }
        }
      } catch {
        // ignore polling failure and let user retry
      }
    }
    void waitForSession()
    return () => {
      cancelled = true
    }
  }, [loadingSession])

  useEffect(() => {
    if (!projectId) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
      hadEventConnectionRef.current = false
      dismissClientNotice('connection-sse-disconnected')
      dismissClientNotice('connection-sse-reset')
      return
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
    }
    const source = new EventSource(appUrl('/api/v1/events'))
    eventSourceRef.current = source
    source.onopen = () => {
      if (hadEventConnectionRef.current) {
        dismissClientNotice('connection-sse-disconnected')
      }
      hadEventConnectionRef.current = true
    }
    const refreshSnapshot = () => {
      void queryClient.refetchQueries({ queryKey: ['snapshot'], exact: true })
    }
    source.onmessage = refreshSnapshot
    for (const eventType of SNAPSHOT_REFRESH_EVENTS) {
      source.addEventListener(eventType, refreshSnapshot)
    }
    source.addEventListener('stream.reset', () => {
      reportClientWarning(
        'connection-sse-reset',
        'event_stream_reset',
        'The live event stream fell behind and was resynced from the latest snapshot.',
      )
      void queryClient.refetchQueries({ queryKey: ['snapshot'], exact: true })
    })
    source.onerror = () => {
      reportClientError(
        'connection-sse-disconnected',
        'server_connection_lost',
        'The server connection was interrupted. Reconnecting now.',
      )
      void queryClient.refetchQueries({ queryKey: ['snapshot'], exact: true })
    }
    return () => {
      for (const eventType of SNAPSHOT_REFRESH_EVENTS) {
        source.removeEventListener(eventType, refreshSnapshot)
      }
      source.close()
    }
  }, [projectId, queryClient])

  const selectedNode = useMemo(
    () => liveSnapshot?.graph.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [liveSnapshot, selectedNodeId],
  )

  useEffect(() => {
    if (!projectId || !selectedNode || selectedNode.execution_meta?.status !== 'running') {
      return
    }
    const interval = window.setInterval(() => {
      void refreshSnapshot()
    }, 1000)
    return () => window.clearInterval(interval)
  }, [projectId, selectedNode?.id, selectedNode?.execution_meta?.status])

  useEffect(() => {
    if (!nodeActionMenu) {
      return
    }
    function handlePointerDown() {
      setNodeActionMenu(null)
    }
    window.addEventListener('pointerdown', handlePointerDown)
    return () => window.removeEventListener('pointerdown', handlePointerDown)
  }, [nodeActionMenu])

  useEffect(() => {
    if (!projectId) {
      setActiveEditorNodeIds([])
      return
    }
    let cancelled = false
    async function loadSessions() {
      try {
        const sessions = await listSessions()
        if (!cancelled) {
          setActiveEditorNodeIds(Array.from(new Set(sessions.map((session) => session.node_id))))
        }
      } catch {
        if (!cancelled) {
          setActiveEditorNodeIds([])
        }
      }
    }
    void loadSessions()
    return () => {
      cancelled = true
    }
  }, [projectId, liveSnapshot?.graph.meta.graph_version])

  useEffect(() => {
    if (!liveSnapshot) {
      return
    }
    if (liveSnapshot.graph.nodes.length === 0) {
      setTemplatesCollapsed(false)
      return
    }
    setTemplatesCollapsed(true)
  }, [liveSnapshot?.graph.nodes.length])

  const artifactNode = useMemo(
    () => liveSnapshot?.graph.nodes.find((node) => node.id === artifactNodeId) ?? null,
    [artifactNodeId, liveSnapshot],
  )

  const artifactList = useMemo(() => {
    if (!liveSnapshot) {
      return []
    }
    const selectedArtifacts = artifactNodeId
      ? liveSnapshot.artifacts.filter((artifact) => artifact.node_id === artifactNodeId)
      : liveSnapshot.artifacts
    const needle = artifactFilter.trim().toLowerCase()
    if (!needle) {
      return selectedArtifacts
    }
    return selectedArtifacts.filter((artifact) => {
      return `${artifact.node_id}/${artifact.artifact_name}`.toLowerCase().includes(needle)
    })
  }, [artifactFilter, artifactNodeId, liveSnapshot])

  const artifactListCounts = useMemo(
    () => artifactList.reduce(
      (totals, artifact) => {
        totals[artifact.state] += 1
        return totals
      },
      { ready: 0, stale: 0, pending: 0 },
    ),
    [artifactList],
  )

  useEffect(() => {
    if (!artifactExplorerOpen) {
      return
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setArtifactExplorerOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [artifactExplorerOpen])

  const existingNodeIds = useMemo(
    () => liveSnapshot?.graph.nodes.map((node) => node.id) ?? [],
    [liveSnapshot],
  )

  const existingNodeIdSet = useMemo(
    () => new Set(existingNodeIds),
    [existingNodeIds],
  )

  const paletteEntries = useMemo<PaletteEntry[]>(() => {
    const builtins: PaletteEntry[] = [
      {
        key: 'empty',
        title: 'New notebook',
        description: 'Generic notebook scaffold with one sample input and output.',
        kind: 'empty',
      },
      {
        key: 'value_input',
        title: 'Constant value',
        description: 'Create one or more ready-to-use constant outputs.',
        kind: 'value_input',
      },
      {
        key: 'file_input',
        title: 'File',
        description: 'Upload a file and expose it as a file artifact.',
        kind: 'file_input',
      },
    ]
    const templateEntries = (liveSnapshot?.templates ?? [])
      .filter((template) => template.kind === 'notebook' && template.ref !== 'builtin/value_input' && !template.hidden)
      .map<PaletteEntry>((template) => ({
        key: `template:${template.ref}`,
        title: template.title,
        description: template.description ?? template.ref,
        kind: 'template',
        templateRef: template.ref,
      }))
    const pipelineEntries = (liveSnapshot?.templates ?? [])
      .filter((template) => template.kind === 'pipeline')
      .map<PaletteEntry>((template) => ({
        key: `pipeline:${template.ref}`,
        title: template.title,
        description: template.description ?? template.ref,
        kind: 'pipeline',
        templateRef: template.ref,
      }))
    const needle = paletteSearch.trim().toLowerCase()
    const allEntries = [...builtins, ...templateEntries, ...pipelineEntries]
    if (!needle) {
      return allEntries
    }
    return allEntries.filter((entry) => `${entry.title} ${entry.description}`.toLowerCase().includes(needle))
  }, [liveSnapshot, paletteSearch])

  async function refreshSnapshot() {
    if (!projectId) {
      return
    }
    await queryClient.refetchQueries({ queryKey: ['snapshot'], exact: true })
  }

  async function mutateGraph(operations: Array<Record<string, unknown>>) {
    if (!liveSnapshot || !projectId) {
      return
    }
    const rollbackSnapshot = liveSnapshot
    try {
      const optimistic = applyOptimisticGraphOperations(liveSnapshot, operations)
      if (optimistic) {
        setOptimisticGraph(optimistic)
      }
      const response = await patchGraph(liveSnapshot.graph.meta.graph_version, operations as never)
      setSnapshotData(queryClient, (current) => mergeGraphIntoSnapshot(current, response))
      dismissClientNotice('graph-update')
      await refreshSnapshot()
    } catch (err) {
      setOptimisticGraph(null)
      setSnapshotData(queryClient, () => rollbackSnapshot)
      const message = err instanceof Error ? err.message : 'Graph update failed.'
      reportClientError('graph-update', 'graph_update_failed', message)
      await refreshSnapshot()
    }
  }

  async function handleCreateNode(
    payload: { type: 'empty' | 'template' | 'file_input'; nodeId: string; title: string; templateRef?: string; sourceText?: string; origin?: 'constant_value' | null },
    placement?: { x: number; y: number },
  ) {
    const baseX = 120 + ((liveSnapshot?.graph.nodes.length ?? 0) % 4) * 420
    const baseY = 120 + Math.floor((liveSnapshot?.graph.nodes.length ?? 0) / 4) * 280
    const x = snapToGrid((placement?.x ?? baseX) - NEW_NODE_WIDTH / 2)
    const y = snapToGrid((placement?.y ?? baseY) - NEW_NODE_HEIGHT / 2)
    if (payload.type === 'file_input') {
      await mutateGraph([
        { type: 'add_file_input_node', node_id: payload.nodeId, title: payload.title, x, y, w: NEW_NODE_WIDTH, h: NEW_NODE_HEIGHT },
      ])
      return
    }
    if (payload.type === 'template') {
      await mutateGraph([
        {
          type: 'add_notebook_node',
          node_id: payload.nodeId,
          title: payload.title,
          template_ref: payload.templateRef,
          source_text: payload.sourceText,
          ui: payload.origin ? { origin: payload.origin } : undefined,
          x,
          y,
          w: NEW_NODE_WIDTH,
          h: NEW_NODE_HEIGHT,
        },
      ])
      return
    }
    await mutateGraph([
      { type: 'add_notebook_node', node_id: payload.nodeId, title: payload.title, x, y, w: NEW_NODE_WIDTH, h: NEW_NODE_HEIGHT },
    ])
  }

  async function handleCreatePipelineTemplate(templateRef: string, placement: { x: number; y: number }, nodeIdPrefix?: string | null) {
    await mutateGraph([
      {
        type: 'add_pipeline_template',
        template_ref: templateRef,
        x: snapToGrid(placement.x),
        y: snapToGrid(placement.y),
        node_id_prefix: nodeIdPrefix ?? null,
      },
    ])
  }

  function pipelineTemplateByEntry(entry: PaletteEntry): TemplateRecord | null {
    if (!liveSnapshot || !entry.templateRef) {
      return null
    }
    return liveSnapshot.templates.find((template) => template.kind === 'pipeline' && template.ref === entry.templateRef) ?? null
  }

  function pipelineTemplateNodeIds(template: TemplateRecord): string[] {
    return pipelineDefinitionNodeIds(template)
  }

  function pipelinePrefixRequirements(template: TemplateRecord) {
    const templateNodeIds = pipelineTemplateNodeIds(template)
    const colliding = templateNodeIds.filter((nodeId) => existingNodeIdSet.has(nodeId))
    const suggestedPrefixBase = normalizeNodeId(template.title)
    let suggestedPrefix = suggestedPrefixBase
    if (colliding.length) {
      let index = 2
      while (!suggestedPrefix || templateNodeIds.some((nodeId) => existingNodeIdSet.has(`${suggestedPrefix}_${nodeId}`))) {
        suggestedPrefix = `${suggestedPrefixBase || 'pipeline'}_${index}`
        index += 1
      }
    }
    return {
      templateNodeIds,
      requirePrefix: colliding.length > 0,
      suggestedPrefix: colliding.length > 0 ? suggestedPrefix : '',
    }
  }

  async function openCreateBlockDialog(entry: PaletteEntry, placement?: { x: number; y: number }) {
    if (!liveSnapshot) {
      return
    }
    const baseX = 120 + (liveSnapshot.graph.nodes.length % 4) * 420
    const baseY = 120 + Math.floor(liveSnapshot.graph.nodes.length / 4) * 280
    const x = placement?.x ?? baseX
    const y = placement?.y ?? baseY
    if (entry.kind === 'pipeline') {
      const template = pipelineTemplateByEntry(entry)
      if (!template || !entry.templateRef) {
        return
      }
      setPendingBlockCreation(null)
      const pipelinePlacement = placement
        ? pipelineTopLeftForCenter(template, { x, y })
        : { x, y }
      const { templateNodeIds, requirePrefix, suggestedPrefix } = pipelinePrefixRequirements(template)
      if (!requirePrefix) {
        await handleCreatePipelineTemplate(entry.templateRef, pipelinePlacement, null)
        return
      }
      if (!templateNodeIds.length) {
        return
      }
      setPendingPipelineCreation({ entry, x: pipelinePlacement.x, y: pipelinePlacement.y, template, suggestedPrefix, requirePrefix })
      return
    }
    if (entry.kind === 'template') {
      const suggestedNodeId = normalizeNodeId(entry.title)
      if (suggestedNodeId && !existingNodeIdSet.has(suggestedNodeId)) {
        await handleCreateNode(
          {
            type: 'template',
            nodeId: suggestedNodeId,
            title: entry.title,
            templateRef: entry.templateRef,
          },
          { x, y },
        )
        return
      }
    }
    setPendingBlockCreation({
      entry,
      x,
      y,
    })
  }

  async function handleCreateFromPalette(entry: PaletteEntry) {
    await openCreateBlockDialog(entry)
  }

  async function handleConfirmCreatePipeline(payload: { nodeIdPrefix: string | null }) {
    if (!pendingPipelineCreation || !pendingPipelineCreation.entry.templateRef) {
      return
    }
    const { entry, x, y } = pendingPipelineCreation
    const templateRef = entry.templateRef!
    setPendingPipelineCreation(null)
    await handleCreatePipelineTemplate(templateRef, { x, y }, payload.nodeIdPrefix)
  }

  async function handleConfirmCreateBlock(payload: { nodeId: string; title: string }) {
    if (!pendingBlockCreation) {
      return
    }
    const { entry, x, y } = pendingBlockCreation
    setPendingBlockCreation(null)
    if (entry.kind === 'file_input') {
      await handleCreateNode({ type: 'file_input', nodeId: payload.nodeId, title: payload.title }, { x, y })
      return
    }
    if (entry.kind === 'template' || entry.kind === 'value_input') {
      await handleCreateNode({
        type: 'template',
        nodeId: payload.nodeId,
        title: payload.title,
        templateRef: entry.templateRef,
      }, { x, y })
      return
    }
    if (entry.kind === 'empty') {
      await handleCreateNode({ type: 'empty', nodeId: payload.nodeId, title: payload.title }, { x, y })
    }
  }

  async function handleCreateConstantValueBlock(payload: {
    nodeId: string
    title: string
    outputs: Array<{ name: string; dataType: ConstantValueType; value: string }>
  }) {
    if (!pendingBlockCreation || !projectId) {
      return
    }
    const { x, y } = pendingBlockCreation
    setPendingBlockCreation(null)
    const sourceText = buildConstantValueNotebookSource(payload.title, payload.outputs)
    await handleCreateNode(
      {
        type: 'template',
        nodeId: payload.nodeId,
        title: payload.title,
        sourceText,
        origin: 'constant_value',
      },
      { x, y },
    )
    const response = await runNode(payload.nodeId, 'run_stale', 'use_stale')
    if (response.status === 'failed') {
      reportClientError(`run:${payload.nodeId}:run_stale`, 'run_failed', runFailureMessage(response, 'Run failed.'), { nodeId: payload.nodeId, details: response })
    }
    await refreshSnapshot()
  }

  async function handleCreateFileBlock(payload: { nodeId: string; title: string; file: File | null; artifactName: string }) {
    if (!pendingBlockCreation || !projectId) {
      return
    }
    const { x, y } = pendingBlockCreation
    setPendingBlockCreation(null)
    await mutateGraph([
      {
        type: 'add_file_input_node',
        node_id: payload.nodeId,
        title: payload.title,
        artifact_name: payload.artifactName.trim() || 'file',
        x: snapToGrid(x - NEW_NODE_WIDTH / 2),
        y: snapToGrid(y - NEW_NODE_HEIGHT / 2),
        w: NEW_NODE_WIDTH,
        h: NEW_NODE_HEIGHT,
      },
    ])
    if (payload.file) {
      await uploadFile(payload.nodeId, payload.file)
      await refreshSnapshot()
    }
  }

  function handlePaletteDragStart(entry: PaletteEntry, position?: { x: number; y: number }) {
    setDraggedPaletteEntry(entry)
    setDragPointer(position ?? null)
  }

  function handlePaletteDragEnd() {
    setDraggedPaletteEntry(null)
    setDragPointer(null)
  }

  function handleBlockDrop(x: number, y: number) {
    if (!draggedPaletteEntry) {
      return
    }
    void openCreateBlockDialog(draggedPaletteEntry, { x, y })
    setDraggedPaletteEntry(null)
    setDragPointer(null)
  }

  async function handleRunNode(nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run') {
    if (!projectId) {
      return
    }
    try {
      const initialResponse = await runNode(nodeId, mode, mode === 'edit_run' ? null : 'use_stale')
      let response = initialResponse
      if (initialResponse.requires_confirmation) {
        if (mode === 'edit_run') {
          reportClientError(`run:${nodeId}:${mode}`, 'run_failed', 'Edit runs do not support upstream refresh confirmation.', { nodeId })
          return
        }
        setConfirmationState({
          kind: 'run-upstream',
          nodeId,
          mode,
          message: 'Some inputs are stale or pending. Refresh upstream notebooks first, or run with stale data.',
        })
        return
      }
      if (typeof response.session_id === 'string') {
        launchEditorTab(response.session_id, nodeId)
      } else if (response.status === 'failed') {
        reportClientError(`run:${nodeId}:${mode}`, 'run_failed', runFailureMessage(response, 'Run failed.'), { nodeId, details: response })
      } else if (response.status === 'blocked') {
        reportClientWarning(
          `run-blocked:${nodeId}:${mode}`,
          'run_blocked',
          'This run is blocked by missing or pending inputs.',
          { nodeId, details: response },
        )
      }
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Run failed.'
      if (isEditorOpenConflict(message)) {
        const sessions = await listSessions()
        const session = sessions.find((item) => item.node_id === nodeId)
        reportClientWarning(
          `editor-open:${nodeId}`,
          'editor_already_open',
          'An editor is open for this notebook.',
          {
            nodeId,
            details: session
              ? { session_id: session.session_id, session_url: session.url, ready: session.ready }
              : {},
          },
        )
        return
      }
      reportClientError(`run:${nodeId}:${mode}`, 'run_failed', message, { nodeId })
    }
  }

  async function handleRunAll() {
    if (!projectId) {
      return
    }
    setConfirmationState({ kind: 'run-all' })
  }

  async function confirmRunNodeWithAction(nodeId: string, mode: 'run_stale' | 'run_all', action: 'run_upstream' | 'use_stale') {
    if (!projectId) {
      return
    }
    try {
      const response = await runNode(nodeId, mode, action)
      if (response.status === 'failed') {
        reportClientError(`run:${nodeId}:${mode}`, 'run_failed', runFailureMessage(response, 'Run failed.'), { nodeId, details: response })
      } else if (response.status === 'blocked') {
        reportClientWarning(
          `run-blocked:${nodeId}:${mode}`,
          'run_blocked',
          'This run is blocked by missing or pending inputs.',
          { nodeId, details: response },
        )
      }
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Run failed.'
      reportClientError(`run:${nodeId}:${mode}`, 'run_failed', message, { nodeId })
    }
  }

  async function confirmRunAll() {
    if (!projectId) {
      return
    }
    try {
      const response = await runAll()
      if (response.status === 'failed') {
        reportClientError('run-all', 'run_queue_failed', runFailureMessage(response, 'Run queue failed.'), { details: response })
      }
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Run queue failed.'
      reportClientError('run-all', 'run_queue_failed', message)
    }
  }

  async function handleCancelRun() {
    if (!projectId || !liveSnapshot) {
      return
    }
    const active = currentRun(liveSnapshot)
    if (!active) {
      return
    }
    await cancelRun(active.run_id)
    await refreshSnapshot()
  }

  async function handleEdgeChanges(changes: EdgeChange[]) {
    const removals = changes.filter((change) => change.type === 'remove')
    if (!removals.length) {
      return
    }
    await mutateGraph(removals.map((change) => ({ type: 'remove_edge', edge_id: change.id })))
  }

  async function handleConnect(connection: Connection) {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) {
      return
    }
    const sourcePort = connection.sourceHandle.replace('out:', '')
    const targetPort = connection.targetHandle.replace('in:', '')
    await mutateGraph([
      {
        type: 'add_edge',
        source_node: connection.source,
        source_port: sourcePort,
        target_node: connection.target,
        target_port: targetPort,
      },
    ])
  }

  async function handleToggleHiddenInput(node: NodeRecord, inputName: string) {
    const currentHidden = hiddenInputNames(node)
    if (currentHidden.has(inputName)) {
      currentHidden.delete(inputName)
    } else {
      currentHidden.add(inputName)
    }
    await mutateGraph([
      {
        type: 'update_node_hidden_inputs',
        node_id: node.id,
        hidden_inputs: Array.from(currentHidden),
      },
    ])
  }

  async function handleUploadFile(nodeId: string, file: File) {
    if (!projectId) {
      return
    }
    try {
      await uploadFile(nodeId, file)
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Upload failed.'
      reportClientError(`upload:${nodeId}`, 'upload_failed', message, { nodeId })
    }
  }

  async function handleCreateCheckpoint() {
    if (!projectId) {
      return
    }
    await createCheckpoint()
    await refreshSnapshot()
  }

  async function handleRestoreCheckpoint(checkpointId: string) {
    if (!projectId) {
      return
    }
    if (!window.confirm(`Restore checkpoint ${checkpointId}?`)) {
      return
    }
    await restoreCheckpoint(checkpointId)
    setOptimisticGraph(null)
    setSelectedNodeId(null)
    setArtifactNodeId(null)
    setInspectorOpen(false)
    await refreshSnapshot()
  }

  async function handleNodeMove(nodeId: string, x: number, y: number) {
    await mutateGraph([
      {
        type: 'update_node_layout',
        node_id: nodeId,
        x: Math.round(x / 20) * 20,
        y: Math.round(y / 20) * 20,
      },
    ])
  }

  async function handleNodesDelete(nodes: Node[]) {
    if (!nodes.length) {
      return
    }
    await mutateGraph(nodes.map((node) => ({ type: 'delete_node', node_id: node.id })))
    setSelectedNodeId((current) => {
      const next = current && nodes.some((node) => node.id === current) ? null : current
      if (!next) {
        setInspectorOpen(false)
      }
      return next
    })
    setArtifactNodeId((current) => (current && nodes.some((node) => node.id === current) ? null : current))
  }

  function openFileNodeEdit(nodeId: string) {
    const node = liveSnapshot?.graph.nodes.find((entry) => entry.id === nodeId)
    if (!node || node.kind !== 'file_input') {
      return
    }
    setSelectedNodeId(nodeId)
    setInspectorOpen(true)
    setFileNodeEdit({
      nodeId,
      title: node.title,
      artifactName: node.ui?.artifact_name ?? 'file',
    })
  }

  async function handleDeleteNodeAction(nodeId: string) {
    if (!projectId) {
      return
    }
    const node = liveSnapshot?.graph.nodes.find((entry) => entry.id === nodeId)
    if (!node) {
      return
    }
    if (!window.confirm(`Create a checkpoint and delete block "${node.title}"?`)) {
      return
    }
    await createCheckpoint()
    await handleNodesDelete([{ id: nodeId } as Node])
    await refreshSnapshot()
    setNodeActionMenu(null)
  }

  async function handleDismissNotice(notice: AppNotice) {
    if (notice.origin === 'client' || !projectId) {
      dismissClientNotice(notice.issue_id)
      return
    }
    await dismissNotice(notice.issue_id)
    await refreshSnapshot()
  }

  async function handleOpenEditorNotice(notice: AppNotice) {
    const details = editorSessionDetails(notice.details)
    if (!details) {
      return
    }
    window.open(details.session_url, '_blank', 'noopener,noreferrer')
  }

  async function handleKillEditorNotice(notice: AppNotice) {
    if (!projectId) {
      return
    }
    const details = editorSessionDetails(notice.details)
    if (!details) {
      return
    }
    await stopSession(details.session_id)
    dismissClientNotice(notice.issue_id)
    await refreshSnapshot()
  }

  async function handleOpenEditor(nodeId: string) {
    await handleRunNode(nodeId, 'edit_run')
  }

  async function handleKillEditor(nodeId: string) {
    if (!projectId) {
      return
    }
    const sessions = await listSessions()
    const session = sessions.find((item) => item.node_id === nodeId)
    if (!session) {
      setActiveEditorNodeIds((current) => current.filter((id) => id !== nodeId))
      return
    }
    await stopSession(session.session_id)
    setActiveEditorNodeIds((current) => current.filter((id) => id !== nodeId))
    await refreshSnapshot()
  }

  const counts = liveSnapshot ? globalArtifactCounts(liveSnapshot) : { ready: 0, stale: 0, pending: 0 }
  const activeRun = liveSnapshot ? currentRun(liveSnapshot) : null
  const runningNodeId = liveSnapshot ? activeRunNodeId(liveSnapshot, activeRun) : null
  const queuedNodeIds = liveSnapshot ? queuedRunNodeIds(liveSnapshot, activeRun) : []
  const completedNodeIds = liveSnapshot
    ? liveSnapshot.graph.nodes
      .filter((node) => node.orchestrator_state?.status === 'succeeded')
      .map((node) => node.id)
    : []
  const clientNowAnchorMs = Date.now()
  const serverNowMs = clientNowAnchorMs + serverNowOffsetMs

  if (loadingSession) {
    return (
      <SessionLoadingScreen
        sessionId={loadingSession.sessionId}
        nodeId={loadingSession.nodeId}
        onCancel={() => {
          const url = new URL(window.location.href)
          url.search = ''
          window.history.replaceState({}, '', url.toString())
        }}
      />
    )
  }

  return (
    <div className="app-shell">
      <div className="canvas-underlay" />
      {draggedPaletteEntry && dragPointer ? (
        <div className="drag-cursor-preview" style={{ left: dragPointer.x, top: dragPointer.y }}>
          <div className="drop-preview-card">
            <div className={`drop-preview-badge ${draggedPaletteEntry.kind === 'file_input' || draggedPaletteEntry.kind === 'value_input' ? 'tone-input' : draggedPaletteEntry.kind === 'template' || draggedPaletteEntry.kind === 'pipeline' ? 'tone-template' : 'tone-custom'}`}>
              {draggedPaletteEntry.kind === 'file_input' ? 'F' : draggedPaletteEntry.kind === 'value_input' ? 'V' : draggedPaletteEntry.kind === 'template' ? 'T' : draggedPaletteEntry.kind === 'pipeline' ? 'P' : 'C'}
            </div>
            <div className="drop-preview-copy">
              <strong>{draggedPaletteEntry.title}</strong>
              <span>{draggedPaletteEntry.kind === 'pipeline' ? 'Drop to place pipeline' : 'Drop to place block'}</span>
            </div>
          </div>
        </div>
      ) : null}
      <div className="floating-actions floating-panel">
        <div className="topbar-actions">
          <button className="secondary icon-pill" onClick={() => setShowSettings(true)} aria-label="Editor settings"><Palette width={18} height={18} /></button>
          <button className="secondary icon-pill" onClick={() => setShowProjectInfo(true)} disabled={!projectId} aria-label="Project info"><Info width={18} height={18} /></button>
          {activeRun ? (
            <button className="danger" onClick={handleCancelRun}>Stop run</button>
          ) : (
            <button className="play-action" onClick={handleRunAll} disabled={!projectId} aria-label="Run pipeline" title="Run pipeline"><Play width={20} height={20} /></button>
          )}
          <button
            className="secondary artifact-summary-button"
            onClick={() => {
              setArtifactNodeId(null)
              setArtifactExplorerOpen(true)
            }}
            disabled={!projectId}
          >
            Artifacts <ArtifactCounts counts={counts} compact />
          </button>
        </div>
      </div>

      <section className="workspace-grid">
        <aside className={`sidebar left floating-panel ${templatesCollapsed ? 'collapsed' : ''}`}>
          <div className="panel template-sidebar">
            <button
              className="block-rail-toggle"
              onClick={() => setTemplatesCollapsed((current) => !current)}
              aria-label={templatesCollapsed ? 'Open Blocks panel' : 'Collapse Blocks panel'}
              title={templatesCollapsed ? 'Open Blocks panel' : 'Collapse Blocks panel'}
              aria-expanded={!templatesCollapsed}
              aria-controls="blocks-panel-content"
            >
              <Plus width={32} height={32} />
            </button>
            <div id="blocks-panel-content" className="template-sidebar-inner" aria-hidden={templatesCollapsed}>
              <div className="panel-header-row">
                <h2>Blocks</h2>
              </div>
              <label>
                <span>Search blocks</span>
                <input value={paletteSearch} onChange={(event) => setPaletteSearch(event.target.value)} placeholder="Search blocks or templates" />
              </label>
              <BlockPalette
                entries={paletteEntries}
                onCreate={handleCreateFromPalette}
                onInspectTemplate={setTemplateRefView}
                onDragStart={handlePaletteDragStart}
                onDragEnd={handlePaletteDragEnd}
              />
            </div>
          </div>
        </aside>

        <main className="canvas-panel">
          {liveSnapshot ? (
            <GraphCanvas
              snapshot={liveSnapshot}
              serverNowMs={serverNowMs}
              serverNowClientAnchorMs={clientNowAnchorMs}
              activeRunNodeId={runningNodeId}
              queuedRunNodeIds={queuedNodeIds}
              completedRunNodeIds={completedNodeIds}
              onConnect={handleConnect}
              onEdgesChange={handleEdgeChanges}
              onNodeSelect={setSelectedNodeId}
              onNodeContextMenu={(nodeId, position) => {
                setSelectedNodeId(nodeId)
                setInspectorOpen(true)
                setNodeActionMenu({ nodeId, x: position.x, y: position.y })
              }}
              onEditFileNode={openFileNodeEdit}
              activeEditorNodeIds={activeEditorNodeIds}
              onOpenEditor={(nodeId) => void handleOpenEditor(nodeId)}
              onKillEditor={(nodeId) => void handleKillEditor(nodeId)}
              onRunNode={handleRunNode}
              onOpenArtifacts={(nodeId) => {
                setArtifactNodeId(nodeId)
                setArtifactExplorerOpen(true)
              }}
              onCanvasInteract={() => setTemplatesCollapsed(true)}
              onCanvasClear={() => {
                setSelectedNodeId(null)
                setInspectorOpen(false)
              }}
              onNodeMove={handleNodeMove}
              onNodesDelete={handleNodesDelete}
              draggedBlock={draggedPaletteEntry ? { title: draggedPaletteEntry.title, kind: draggedPaletteEntry.kind } : null}
              onBlockDrop={handleBlockDrop}
            />
          ) : (
            <div className="empty-state">
              <h2>No project open</h2>
              <p>Open an existing BulletJournal project or initialize a new one to start editing the graph.</p>
            </div>
          )}
        </main>

        <aside className={`sidebar right floating-panel ${selectedNode && inspectorOpen ? 'open' : 'closed'}`}>
          <div className={`panel inspector-panel ${selectedNode && inspectorOpen ? 'open' : 'closed'}`}>
            <div className="panel-header-row">
              <h2>Inspector</h2>
              {selectedNode ? <button className="secondary" onClick={() => {
                setSelectedNodeId(null)
                setInspectorOpen(false)
              }}>Clear</button> : null}
            </div>
            {selectedNode ? (
              <NodeInspector
                snapshot={liveSnapshot as ProjectSnapshot}
                node={selectedNode}
                serverNowMs={serverNowMs}
                serverNowClientAnchorMs={clientNowAnchorMs}
                activeRunNodeId={runningNodeId}
                queuedRunNodeIds={queuedNodeIds}
                completedRunNodeIds={completedNodeIds}
                onToggleHiddenInput={handleToggleHiddenInput}
                onUploadFile={handleUploadFile}
                onOpenTemplate={setTemplateRefView}
                onEditFileNode={openFileNodeEdit}
                onDeleteNode={handleDeleteNodeAction}
              />
            ) : null}
          </div>
        </aside>
      </section>

      {nodeActionMenu && selectedNode ? (
        <div className="node-action-menu" style={{ left: nodeActionMenu.x, top: nodeActionMenu.y }} onClick={(event) => event.stopPropagation()}>
          {selectedNode.kind === 'file_input' ? (
            <button className="secondary menu-item" onClick={() => openFileNodeEdit(selectedNode.id)}>Edit block</button>
          ) : null}
          <button className="secondary menu-item danger-text" onClick={() => void handleDeleteNodeAction(selectedNode.id)}>Delete block</button>
        </div>
      ) : null}

      {artifactExplorerOpen ? (
        <Modal title={artifactNode ? `${artifactNode.title} artifacts` : 'Artifact explorer'} onClose={() => setArtifactExplorerOpen(false)} contentClassName="artifact-explorer-modal">
          <div className="artifact-explorer-shell">
            <div className="artifact-explorer-toolbar">
              <label className="artifact-explorer-search">
                <span>Search artifacts</span>
                <input value={artifactFilter} onChange={(event) => setArtifactFilter(event.target.value)} placeholder="Search by node or artifact name" />
              </label>
              <div className="artifact-explorer-actions">
                <ArtifactCounts counts={artifactListCounts} showLabels />
                {artifactNodeId ? <button className="secondary" onClick={() => setArtifactNodeId(null)}>All artifacts</button> : null}
              </div>
            </div>
            {artifactList.length ? (
              <div className="artifact-grid artifact-explorer-grid">
                {artifactList.map((artifact) => (
                  <ArtifactCard key={`${artifact.node_id}/${artifact.artifact_name}`} artifact={artifact} />
                ))}
              </div>
            ) : (
              <div className="artifact-empty-state">
                <h4>No artifacts found</h4>
                <p className="muted-copy">Try another search or switch back to all artifacts.</p>
              </div>
            )}
          </div>
        </Modal>
      ) : null}

      {templateRefView && liveSnapshot ? (
        <Modal title={templateByRef(liveSnapshot, templateRefView)?.title ?? 'Template'} onClose={() => setTemplateRefView(null)}>
          <pre className="code-block template-source">{templateByRef(liveSnapshot, templateRefView)?.source_text ?? 'Template source unavailable.'}</pre>
        </Modal>
      ) : null}

      {pendingPipelineCreation && liveSnapshot ? (
        <CreatePipelineDialog
          pipelineLabel={pendingPipelineCreation.entry.title}
          existingIds={existingNodeIds}
          templateNodeIds={pipelineTemplateNodeIds(pendingPipelineCreation.template)}
          suggestedPrefix={pendingPipelineCreation.suggestedPrefix}
          requirePrefix={pendingPipelineCreation.requirePrefix}
          onClose={() => setPendingPipelineCreation(null)}
          onCreate={handleConfirmCreatePipeline}
        />
      ) : null}

      {pendingBlockCreation && liveSnapshot && blockCreateMode(pendingBlockCreation.entry) === 'constant_value' ? (
        <CreateConstantValueDialog
          suggestedTitle={pendingBlockCreation.entry.title}
          existingIds={existingNodeIds}
          onClose={() => setPendingBlockCreation(null)}
          onCreate={handleCreateConstantValueBlock}
        />
      ) : null}

      {pendingBlockCreation && liveSnapshot && blockCreateMode(pendingBlockCreation.entry) === 'file' ? (
        <CreateFileDialog
          suggestedTitle={pendingBlockCreation.entry.title}
          existingIds={existingNodeIds}
          onClose={() => setPendingBlockCreation(null)}
          onCreate={handleCreateFileBlock}
        />
      ) : null}

      {fileNodeEdit ? (
        <CreateFileDialog
          mode="edit"
          suggestedTitle={fileNodeEdit.title}
          existingIds={existingNodeIds.filter((nodeId) => nodeId !== fileNodeEdit.nodeId)}
          fixedNodeId={fileNodeEdit.nodeId}
          initialArtifactName={fileNodeEdit.artifactName}
          onClose={() => setFileNodeEdit(null)}
          onCreate={async (payload) => {
            setFileNodeEdit(null)
            if (!projectId) {
              return
            }
            await mutateGraph([
              { type: 'update_node_title', node_id: fileNodeEdit.nodeId, title: payload.title },
            ])
            if (payload.file) {
              await uploadFile(fileNodeEdit.nodeId, payload.file)
              await refreshSnapshot()
            }
          }}
        />
      ) : null}

      {pendingBlockCreation && liveSnapshot && blockCreateMode(pendingBlockCreation.entry) === 'notebook' ? (
        <CreateNotebookDialog
          blockLabel={pendingBlockCreation.entry.title}
          suggestedTitle={pendingBlockCreation.entry.title}
          existingIds={existingNodeIds}
          onClose={() => setPendingBlockCreation(null)}
          onCreate={handleConfirmCreateBlock}
        />
      ) : null}

      {showProjectInfo && liveSnapshot ? (
        <Modal title="Project info" onClose={() => setShowProjectInfo(false)}>
          <div className="stack-list subtle">
            <div><span>ID</span><strong>{liveSnapshot.project.project_id}</strong></div>
            <div><span>Root</span><strong>{liveSnapshot.project.project_root}</strong></div>
            <div><span>Graph version</span><strong>{liveSnapshot.graph.meta.graph_version}</strong></div>
            <div><span>Updated</span><strong>{formatTimestamp(liveSnapshot.graph.meta.updated_at)}</strong></div>
            <div><span>Checkpoints</span><strong>{liveSnapshot.checkpoints.length}</strong></div>
            <div><span>Recent run</span><strong>{liveSnapshot.runs[0]?.status ?? 'None'}</strong></div>
          </div>
          <div className="inspector-block">
            <div className="panel-header-row">
              <h3>Checkpoints</h3>
              <button className="secondary" onClick={handleCreateCheckpoint}>Create</button>
            </div>
            <div className="stack-list checkpoint-list">
              {liveSnapshot.checkpoints.map((checkpoint) => (
                <div key={checkpoint.checkpoint_id} className="checkpoint-row">
                  <div>
                    <strong>{checkpoint.checkpoint_id}</strong>
                    <span>{formatTimestamp(checkpoint.created_at)}</span>
                  </div>
                  <button className="secondary" onClick={() => handleRestoreCheckpoint(checkpoint.checkpoint_id)}>Restore</button>
                </div>
              ))}
            </div>
          </div>
        </Modal>
      ) : null}

      {showSettings ? (
        <Modal title="Editor settings" onClose={() => setShowSettings(false)}>
          <div className="form-grid compact">
            <label>
              <span>Theme</span>
              <select value={themeMode} onChange={(event) => setThemeMode(event.target.value as ThemeMode)}>
                <option value="system">Same as system</option>
                <option value="light">Light</option>
                <option value="dark">Dark</option>
              </select>
            </label>
          </div>
        </Modal>
      ) : null}

      {confirmationState?.kind === 'run-all' ? (
        <ConfirmDialog
          title="Run all notebooks"
          message="Run all pending and stale notebooks in dependency order?"
          confirmLabel="Run all"
          onClose={() => setConfirmationState(null)}
          onConfirm={() => {
            setConfirmationState(null)
            void confirmRunAll()
          }}
        />
      ) : null}

      {confirmationState?.kind === 'run-upstream' ? (
        <ConfirmDialog
          title="Refresh upstream notebooks?"
          message={confirmationState.message}
          confirmLabel="Refresh upstream"
          alternateLabel="Use stale data"
          cancelLabel="Cancel"
          onClose={() => setConfirmationState(null)}
          onAlternate={() => {
            const pending = confirmationState
            setConfirmationState(null)
            void confirmRunNodeWithAction(pending.nodeId, pending.mode, 'use_stale')
          }}
          onConfirm={() => {
            const pending = confirmationState
            setConfirmationState(null)
            void confirmRunNodeWithAction(pending.nodeId, pending.mode, 'run_upstream')
          }}
        />
      ) : null}

      <NoticeOverlay
        notices={overlayNotices}
        onDismiss={(notice) => void handleDismissNotice(notice)}
        onOpenNode={(nodeId) => setSelectedNodeId(nodeId)}
        onOpenEditor={(notice) => void handleOpenEditorNotice(notice)}
        onKillEditor={(notice) => void handleKillEditorNotice(notice)}
      />
    </div>
  )
}

function SessionLoadingScreen({
  sessionId,
  nodeId,
  onCancel,
}: {
  sessionId: string
  nodeId: string
  onCancel: () => void
}) {
  return (
    <div className="session-splash">
      <div className="session-splash-card">
        <p className="eyebrow">Preparing editor</p>
        <h1>Launching Marimo</h1>
        <p className="subhead">
          Waiting for the local editor to become available for `{nodeId}`.
        </p>
        <div className="stack-list subtle">
          <div><span>Session</span><strong>{sessionId}</strong></div>
        </div>
        <div className="spinner" />
        <button className="ghost-button modal-close-button" onClick={() => {
          onCancel()
          window.close()
        }} aria-label="Close loading screen"><X width={18} height={18} /></button>
      </div>
    </div>
  )
}

function launchEditorTab(sessionId: string, nodeId: string) {
  const params = new URLSearchParams({
    session_id: sessionId,
    node_id: nodeId,
  })
  window.open(appUrl(`/?${params.toString()}`), '_blank', 'noopener,noreferrer')
}

function BlockPalette({
  entries,
  onCreate,
  onInspectTemplate,
  onDragStart,
  onDragEnd,
}: {
  entries: PaletteEntry[]
  onCreate: (entry: PaletteEntry) => Promise<void>
  onInspectTemplate: (ref: string) => void
  onDragStart: (entry: PaletteEntry, position?: { x: number; y: number }) => void
  onDragEnd: () => void
}) {
  const sections = [
    {
      title: 'Core blocks',
      items: entries.filter((entry) => entry.kind === 'empty' || entry.kind === 'value_input' || entry.kind === 'file_input'),
    },
    {
      title: 'Notebook templates',
      items: entries.filter((entry) => entry.kind === 'template'),
    },
    {
      title: 'Pipeline templates',
      items: entries.filter((entry) => entry.kind === 'pipeline'),
    },
  ]

  return (
    <div className="block-palette">
      {sections.map((section) => (
        <section key={section.title} className="palette-section">
          <h3>{section.title}</h3>
            <div className="stack-list templates-list">
              {section.items.map((entry) => (
                <div key={entry.key} className="template-tile palette-tile">
                  <button
                    className="palette-main draggable-block"
                    onClick={() => void onCreate(entry)}
                    draggable
                    onDragStart={(event) => {
                      const dragImage = document.createElement('div')
                      dragImage.textContent = ''
                      dragImage.style.width = '1px'
                      dragImage.style.height = '1px'
                      dragImage.style.opacity = '0'
                      dragImage.style.position = 'fixed'
                      dragImage.style.top = '0'
                      dragImage.style.left = '0'
                      document.body.appendChild(dragImage)
                      event.dataTransfer.effectAllowed = 'move'
                      event.dataTransfer.setData('text/plain', entry.key)
                      event.dataTransfer.setDragImage(dragImage, 0, 0)
                      window.setTimeout(() => {
                        dragImage.remove()
                      }, 0)
                      onDragStart(entry, { x: event.clientX, y: event.clientY })
                    }}
                    onDragEnd={onDragEnd}
                  >
                    <strong>{entry.title}</strong>
                    <span>{entry.description}</span>
                  </button>
                {entry.kind === 'template' || entry.kind === 'value_input' || entry.kind === 'pipeline' ? (
                  <button className="secondary small" onClick={(event) => {
                    event.stopPropagation()
                    if (entry.templateRef) {
                      onInspectTemplate(entry.templateRef)
                    }
                  }}>
                    View
                  </button>
                ) : null}
              </div>
            ))}
            {!section.items.length ? <p className="muted-copy">No matching blocks.</p> : null}
          </div>
        </section>
      ))}
    </div>
  )
}

function pipelineDefinitionNodeIds(template: TemplateRecord | null | undefined): string[] {
  if (!template) {
    return []
  }
  return (template.definition?.nodes ?? []).map((node) => node.id)
}


function pipelineTopLeftForCenter(template: TemplateRecord, center: { x: number; y: number }): { x: number; y: number } {
  const layout = template.definition?.layout ?? []
  if (!layout.length) {
    return center
  }
  const minX = Math.min(...layout.map((entry) => entry.x))
  const minY = Math.min(...layout.map((entry) => entry.y))
  const maxRight = Math.max(...layout.map((entry) => entry.x + entry.w))
  const maxBottom = Math.max(...layout.map((entry) => entry.y + entry.h))
  const width = maxRight - minX
  const height = maxBottom - minY
  return {
    x: center.x - width / 2,
    y: center.y - height / 2,
  }
}


function snapToGrid(value: number): number {
  return Math.round(value / GRID_SIZE) * GRID_SIZE
}


function buildConstantValueNotebookSource(
  title: string,
  outputs: Array<{ name: string; dataType: ConstantValueType; value: string }>,
): string {
  const setupImports = new Set(['from bulletjournal.runtime import artifacts'])

  const cells = outputs.flatMap((output) => {
    const variableName = `${output.name}_value`
    if (output.dataType === 'object') {
      return [
        '@app.cell',
        'def _():',
        `    placeholder_note_${output.name} = 'Edit this notebook to set ${output.name} to a custom object.'`,
        `    ${variableName} = None`,
        `    artifacts.push(${variableName}, name='${output.name}', data_type='object', is_output=True, description='Constant value output')`,
        `    return placeholder_note_${output.name}, ${variableName}`,
        '',
      ]
    }
    const dataTypeExpression = pythonTypeExpression(output.dataType)
    return [
      '@app.cell',
      'def _():',
      `    ${variableName} = ${output.value}`,
      `    artifacts.push(${variableName}, name='${output.name}', data_type=${dataTypeExpression}, is_output=True, description='Constant value output')`,
      `    return ${variableName}`,
      '',
    ]
  })

  return [
    'import marimo',
    '',
    "__generated_with = '0.20.4'",
    `app = marimo.App(width='medium', app_title=${JSON.stringify(title)})`,
    '',
    'with app.setup:',
    ...Array.from(setupImports).sort().map((line) => `    ${line}`),
    '',
    ...cells,
    "if __name__ == '__main__':",
    '    from bulletjournal.runtime.standalone import run_notebook_app',
    '',
    "    run_notebook_app(app, __file__)",
    '',
  ].join('\n')
}


function pythonTypeExpression(dataType: Exclude<ConstantValueType, 'object'>): string {
  return dataType
}

function NoticeOverlay({
  notices,
  onDismiss,
  onOpenNode,
  onOpenEditor,
  onKillEditor,
}: {
  notices: AppNotice[]
  onDismiss: (notice: AppNotice) => void
  onOpenNode: (nodeId: string) => void
  onOpenEditor: (notice: AppNotice) => void
  onKillEditor: (notice: AppNotice) => void
}) {
  if (!notices.length) {
    return null
  }

  return (
    <div className="notice-overlay" aria-live="polite" aria-label="Errors and warnings">
      {notices.map((notice) => {
        const dismissible = notice.severity === 'warning' || notice.origin === 'client'
        const editorDetails = notice.code === 'editor_already_open' ? editorSessionDetails(notice.details) : null
        return (
          <article key={notice.issue_id} className={`notice-card ${notice.severity}`}>
            <div className="notice-card-head">
              <div className="notice-card-copy">
                <p className="notice-label">{notice.severity === 'error' ? 'Error' : 'Warning'}</p>
                <strong>{notice.code}</strong>
              </div>
              {dismissible ? <button className="secondary small" onClick={() => onDismiss(notice)}>Dismiss</button> : null}
            </div>
            <p className="notice-message">{notice.message}</p>
            <div className="notice-card-foot">
              <span>{formatTimestamp(notice.created_at)}</span>
              <div className="notice-card-actions">
                {notice.node_id ? (
                  <button className="secondary small" onClick={() => onOpenNode(notice.node_id as string)}>Open node</button>
                ) : null}
                {editorDetails ? <button className="secondary small" onClick={() => onOpenEditor(notice)}>Open editor</button> : null}
                {editorDetails ? <button className="secondary small" onClick={() => onKillEditor(notice)}>Kill editor</button> : null}
              </div>
            </div>
          </article>
        )
      })}
    </div>
  )
}

function NodeInspector({
  snapshot,
  node,
  serverNowMs,
  serverNowClientAnchorMs,
  activeRunNodeId,
  queuedRunNodeIds,
  completedRunNodeIds,
  onToggleHiddenInput,
  onUploadFile,
  onOpenTemplate,
  onEditFileNode,
  onDeleteNode,
}: {
  snapshot: ProjectSnapshot
  node: NodeRecord
  serverNowMs: number
  serverNowClientAnchorMs: number
  activeRunNodeId: string | null
  queuedRunNodeIds: string[]
  completedRunNodeIds: string[]
  onToggleHiddenInput: (node: NodeRecord, inputName: string) => Promise<void>
  onUploadFile: (nodeId: string, file: File) => Promise<void>
  onOpenTemplate: (templateRef: string) => void
  onEditFileNode: (nodeId: string) => void
  onDeleteNode: (nodeId: string) => Promise<void>
}) {
  const badge = badgeForNode(snapshot, node)
  const counts = artifactCounts(snapshot, node.id)
  const template = templateByRef(snapshot, node.template?.ref)
  const validationIssues = validationIssuesForNode(snapshot, node.id)
  const blockingValidationIssues = validationIssues.filter((issue) => issue.severity === 'error')
  const runFailures = nodeRunFailures(snapshot, node.id)
  const [now, setNow] = useState(() => Date.now())
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (node.execution_meta?.status !== 'running') {
      return
    }
    const interval = window.setInterval(() => setNow(Date.now()), 100)
    return () => window.clearInterval(interval)
  }, [node.execution_meta?.status])

  const runningDurationLabel = useMemo(() => {
    if (node.execution_meta?.status !== 'running') {
      return null
    }
    const startedAt = Date.parse(node.execution_meta.started_at)
    if (Number.isNaN(startedAt)) {
      return null
    }
    return formatDurationSeconds((serverNowMs + (now - serverNowClientAnchorMs) - startedAt) / 1000)
  }, [node.execution_meta, now, serverNowMs, serverNowClientAnchorMs])
  const displayedCurrentCell = node.execution_meta?.current_cell
    ? {
        nodeId: node.id,
        cell_number: node.execution_meta.current_cell.cell_number,
        total_cells: node.execution_meta.current_cell.total_cells,
        cell_code: node.execution_meta.current_cell.cell_code,
      }
    : null
  const displayedState = useMemo(() => {
    if (node.orchestrator_state?.status === 'running' || activeRunNodeId === node.id) {
      return 'running'
    }
    if (node.orchestrator_state?.status === 'queued' || queuedRunNodeIds.includes(node.id)) {
      return 'queued'
    }
    if (node.orchestrator_state?.status === 'succeeded' || completedRunNodeIds.includes(node.id)) {
      return 'ready'
    }
    return node.state
  }, [activeRunNodeId, queuedRunNodeIds, completedRunNodeIds, node.id, node.state, node.orchestrator_state])

  return (
    <div className="inspector-stack">
      <div className="badge-line">
        <span className="rf-badge static" title={badge.title}>{badge.label}</span>
        <strong>{node.title}</strong>
      </div>
      <div className="stack-list subtle">
        <div><span>Node ID</span><strong>{node.id}</strong></div>
        <div><span>Kind</span><strong>{node.kind}</strong></div>
        <div><span>State</span><strong>{displayedState}</strong></div>
        <div><span>Validation</span><strong>{blockingValidationIssues.length ? `${blockingValidationIssues.length} error${blockingValidationIssues.length === 1 ? '' : 's'}` : 'ok'}</strong></div>
        <div><span>Artifacts</span><ArtifactCounts counts={counts} showLabels /></div>
      </div>

      {node.execution_meta ? (
        <div className="inspector-block">
          <h3>Execution</h3>
          <div className="stack-list subtle">
            <div><span>Origin</span><strong>Orchestrator</strong></div>
            <div><span>Status</span><strong>{node.execution_meta.status}</strong></div>
            <div><span>Started</span><strong>{formatTimestamp(node.execution_meta.started_at)}</strong></div>
            {node.execution_meta.status === 'running' && runningDurationLabel ? <div><span>Elapsed</span><strong>{runningDurationLabel}</strong></div> : null}
            {node.execution_meta.status !== 'running' && typeof node.execution_meta.duration_seconds === 'number' && node.state === 'ready' ? <div><span>Duration</span><strong>{formatDurationSeconds(node.execution_meta.duration_seconds)}</strong></div> : null}
          </div>
        </div>
      ) : null}

      {displayedCurrentCell ? (
        <div className="inspector-block">
          <h3>Current cell</h3>
          <div className="inspector-subblock">
            <strong>
              Cell {displayedCurrentCell.cell_number ?? '?'}
              /{displayedCurrentCell.total_cells ?? '?'}
            </strong>
            {displayedCurrentCell.cell_code ? <pre className="code-block docs-block">{displayedCurrentCell.cell_code}</pre> : null}
          </div>
        </div>
      ) : null}

      {node.execution_meta?.stdout ? (
        <div className="inspector-block">
          <h3>Stdout</h3>
          <ExecutionLogPanel
            title="Notebook stdout"
            log={node.execution_meta.stdout}
            nodeId={node.id}
            filenameSuffix="stdout"
          />
        </div>
      ) : null}

      {node.execution_meta?.stderr ? (
        <div className="inspector-block">
          <h3>Stderr</h3>
          <ExecutionLogPanel
            title="Notebook stderr"
            log={node.execution_meta.stderr}
            nodeId={node.id}
            filenameSuffix="stderr"
          />
        </div>
      ) : null}

      {node.template?.ref ? (
        <div className="inspector-block">
          <div className="panel-header-row">
            <h3>Template origin</h3>
            <button className="secondary" onClick={() => onOpenTemplate(node.template?.ref as string)}>View template</button>
          </div>
          <p className="muted-copy">{template?.ref ?? node.template.ref}</p>
        </div>
      ) : null}

      <div className="inspector-block">
        <h3>Notebook docs</h3>
        <pre className="code-block docs-block">{node.interface?.docs ?? 'No notebook docs found.'}</pre>
      </div>

      <div className="inspector-block">
        <h3>Inputs</h3>
        <div className="stack-list">
          {(node.interface?.inputs ?? []).map((port) => {
            const state = inputState(snapshot, node.id, port)
            const source = inputBindingSource(snapshot, node.id, port.name)
            const hidden = hiddenInputNames(node).has(port.name)
            return (
              <div key={port.name} className="inspector-port">
                <PortPill name={port.name} dataType={port.data_type} state={state} side="input" compact />
                <div className="inspector-port-meta">
                  <span>{source ? `${source.source_node}/${source.source_port}` : port.has_default ? 'default value' : 'not connected'}</span>
                  {port.has_default ? <span>default: {JSON.stringify(port.default)}</span> : null}
                </div>
                {port.has_default ? (
                  <button className="secondary small" onClick={() => onToggleHiddenInput(node, port.name)}>
                    {hidden ? 'Show on node' : 'Hide on node'}
                  </button>
                ) : null}
              </div>
            )
          })}
          {!node.interface?.inputs?.length ? <p className="muted-copy">No inputs.</p> : null}
        </div>
      </div>

      <div className="inspector-block">
        <h3>Outputs</h3>
        <div className="stack-list">
          {(node.interface?.outputs ?? []).map((port) => (
            <div key={port.name} className="inspector-port">
              <PortPill
                name={port.name}
                dataType={port.data_type}
                state={artifactFor(snapshot, node.id, port.name)?.state ?? 'pending'}
                side="output"
                compact
              />
            </div>
          ))}
          {!node.interface?.outputs?.length ? <p className="muted-copy">No outputs.</p> : null}
        </div>
      </div>

      <div className="inspector-block">
        <h3>Validation</h3>
        <div className="warning-list">
          {snapshot.notices.filter((issue) => issue.node_id === node.id).map((issue) => {
            const details = formatIssueDetails(issue.details)
            return (
              <div key={issue.issue_id} className={`warning-chip ${issue.severity}`}>
                <strong>{issue.code}</strong>
                <span>{issue.message}</span>
                {details ? <pre className="warning-details">{details}</pre> : null}
              </div>
            )
          })}
          {!snapshot.notices.some((issue) => issue.node_id === node.id) ? <p className="muted-copy">No active validation issues.</p> : null}
        </div>
      </div>

      <div className="inspector-block">
        <h3>Runtime errors</h3>
        <div className="warning-list">
          {runFailures.map((run) => {
            const failure = run.failure_json as Record<string, unknown>
            const traceback = typeof failure.traceback === 'string' ? failure.traceback : null
            const stderr = typeof failure.stderr === 'string' ? failure.stderr : null
            const errorMessage = typeof failure.error === 'string' ? failure.error : 'Run failed.'
            return (
              <div key={run.run_id} className="warning-chip error">
                <strong>{errorMessage}</strong>
                <span>{formatTimestamp(run.ended_at ?? run.started_at)}</span>
                {traceback ? <pre className="warning-details">{traceback}</pre> : null}
                {!traceback && stderr ? <pre className="warning-details">{stderr}</pre> : null}
              </div>
            )
          })}
          {!runFailures.length ? <p className="muted-copy">No recorded runtime errors.</p> : null}
        </div>
      </div>

      {node.kind === 'file_input' ? (
        <div className="inspector-block">
          <h3>File upload</h3>
          <input
            ref={fileInputRef}
            type="file"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) {
                void onUploadFile(node.id, file)
              }
            }}
          />
        </div>
      ) : null}

      <div className="inspector-block">
        <h3>Actions</h3>
        <div className="stack-list">
          {node.kind === 'file_input' ? <button className="secondary" onClick={() => onEditFileNode(node.id)}>Edit block</button> : null}
          <button className="danger" onClick={() => void onDeleteNode(node.id)}>Delete block</button>
        </div>
      </div>
    </div>
  )
}

function ArtifactCard({ artifact }: { artifact: ArtifactRecord }) {
  const downloadHref = artifactEndpoint(artifact, 'download')
  const imageSrc = artifact.preview?.kind === 'file' && artifact.preview.mime_type?.startsWith('image/')
    ? artifactEndpoint(artifact, 'content')
    : null
  const isDataFrame = artifact.data_type === 'pandas.DataFrame'
  const canDownloadCsv = isDataFrame && (artifact.size_bytes ?? 0) <= DATAFRAME_CSV_DOWNLOAD_MAX_BYTES
  const csvDisabledReason = canDownloadCsv ? null : 'CSV export is limited to DataFrame artifacts up to 100 MB.'
  const csvDownloadHref = `${downloadHref}?format=csv`
  const defaultDownloadLabel = artifact.extension?.toLowerCase() ?? 'file'

  return (
    <article className={`artifact-card state-${artifact.state}`}>
        <div className="artifact-head">
          <div className="artifact-title-block">
            <div className="artifact-title-row">
              <strong>{artifact.node_id}/{artifact.artifact_name}</strong>
              <span className={`artifact-state-label ${artifact.state}`}>{artifact.state}</span>
            </div>
            <span>{artifact.data_type ?? 'unknown'}</span>
          </div>
        <div className="artifact-download-actions">
          {isDataFrame ? (
            <>
              <a className="secondary link-button artifact-download-button" href={downloadHref}>
                <Download width={16} height={16} />
                .parquet
              </a>
              <span className="artifact-download-tooltip-shell" title={csvDisabledReason ?? undefined}>
                <a
                  className={`secondary link-button artifact-download-button${canDownloadCsv ? '' : ' disabled'}`}
                  href={canDownloadCsv ? csvDownloadHref : undefined}
                  aria-disabled={!canDownloadCsv}
                  onClick={(event) => {
                    if (!canDownloadCsv) {
                      event.preventDefault()
                    }
                  }}
                >
                  <Download width={16} height={16} />
                  .csv
                </a>
                {!canDownloadCsv ? (
                  <span className="artifact-download-help" tabIndex={0} aria-label={csvDisabledReason ?? undefined}>
                    <Info width={14} height={14} />
                    <span className="artifact-tooltip">{csvDisabledReason}</span>
                  </span>
                ) : null}
              </span>
            </>
          ) : (
            <a className="secondary link-button artifact-download-button" href={downloadHref}>
              <Download width={16} height={16} />
              {defaultDownloadLabel}
            </a>
          )}
        </div>
      </div>
      <ArtifactPreviewPanel preview={artifact.preview} imageSrc={imageSrc} />
      <div className="artifact-meta-grid">
        <span>Storage: {artifact.storage_kind ?? 'n/a'}</span>
        <span>Lineage: {artifact.lineage_mode ?? 'n/a'}</span>
        <span>Created: {formatTimestamp(artifact.created_at)}</span>
        <span>Size: {formatBytes(artifact.size_bytes)}</span>
      </div>
    </article>
  )
}

export default App
