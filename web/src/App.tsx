import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Connection, EdgeChange, Node } from 'reactflow'

import { cancelRun, createCheckpoint, currentProject, dismissNotice, getSnapshot, initProject, listSessions, openProject, patchGraph, restoreCheckpoint, runAll, runNode, uploadFile } from './lib/api'
import { GRID_SIZE, artifactCounts, artifactFor, badgeForNode, currentRun, formatTimestamp, globalArtifactCounts, hiddenInputNames, inputBindingSource, inputState, templateByRef } from './lib/helpers'
import type { ArtifactRecord, NodeRecord, NoticeRecord, ProjectSnapshot } from './lib/types'
import { CreateConstantValueDialog, CreateFileDialog, CreateNotebookDialog, Modal } from './components/Dialogs'
import { ArtifactPreviewPanel } from './components/ArtifactPreview'
import { ArtifactCounts } from './components/ArtifactCounts'
import { GraphCanvas } from './components/GraphCanvas'
import { PortPill } from './components/PortPill'
import { Plus, Info, Palette, Play } from './components/Icons'

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

type ConstantValueType = 'int' | 'float' | 'bool' | 'str' | 'list' | 'dict' | 'object'
type BlockCreateMode = 'notebook' | 'constant_value' | 'file'

type AppNotice = NoticeRecord & {
  origin: 'snapshot' | 'client'
}


function blockCreateMode(entry: PaletteEntry): BlockCreateMode | null {
  if (entry.kind === 'pipeline') {
    return null
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

function artifactEndpoint(projectId: string | null, artifact: ArtifactRecord, action: 'download' | 'content'): string {
  if (!projectId) {
    return '#'
  }
  const nodeId = encodeURIComponent(artifact.node_id)
  const artifactName = encodeURIComponent(artifact.artifact_name)
  return `/api/v1/projects/${projectId}/artifacts/${nodeId}/${artifactName}/${action}`
}

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

function App() {
  const queryClient = useQueryClient()
  const [projectPath, setProjectPath] = useState('')
  const [projectTitle, setProjectTitle] = useState('')
  const [clientNotices, setClientNotices] = useState<AppNotice[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [artifactNodeId, setArtifactNodeId] = useState<string | null>(null)
  const [artifactExplorerOpen, setArtifactExplorerOpen] = useState(false)
  const [artifactFilter, setArtifactFilter] = useState('')
  const [templateRefView, setTemplateRefView] = useState<string | null>(null)
  const [showProjectInfo, setShowProjectInfo] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [templatesCollapsed, setTemplatesCollapsed] = useState(false)
  const [paletteSearch, setPaletteSearch] = useState('')
  const [draggedPaletteEntry, setDraggedPaletteEntry] = useState<PaletteEntry | null>(null)
  const [dragPointer, setDragPointer] = useState<{ x: number; y: number } | null>(null)
  const [pendingBlockCreation, setPendingBlockCreation] = useState<PendingBlockCreation | null>(null)
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
  const loadingSession = startupSearch.get('project_id') && startupSearch.get('session_id')
    ? {
        projectId: startupSearch.get('project_id') as string,
        sessionId: startupSearch.get('session_id') as string,
        nodeId: startupSearch.get('node_id') ?? 'notebook',
      }
    : null

  const projectQuery = useQuery({
    queryKey: ['project-current'],
    queryFn: currentProject,
    retry: false,
  })

  const snapshot = projectQuery.data ?? null
  const projectId = snapshot?.project.project_id ?? null

  const snapshotQuery = useQuery({
    queryKey: ['snapshot', projectId],
    queryFn: () => getSnapshot(projectId as string),
    enabled: Boolean(projectId),
  })

  const liveSnapshot = snapshotQuery.data ?? snapshot

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
          const sessions = await listSessions(loadingSession.projectId)
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
    const source = new EventSource(`/api/v1/projects/${projectId}/events`)
    eventSourceRef.current = source
    source.onopen = () => {
      if (hadEventConnectionRef.current) {
        dismissClientNotice('connection-sse-disconnected')
      }
      hadEventConnectionRef.current = true
    }
    source.onmessage = () => {
      queryClient.invalidateQueries({ queryKey: ['snapshot', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project-current'] })
    }
    source.addEventListener('stream.reset', () => {
      reportClientWarning(
        'connection-sse-reset',
        'event_stream_reset',
        'The live event stream fell behind and was resynced from the latest snapshot.',
      )
      queryClient.invalidateQueries({ queryKey: ['snapshot', projectId] })
    })
    source.onerror = () => {
      reportClientError(
        'connection-sse-disconnected',
        'server_connection_lost',
        'The server connection was interrupted. Reconnecting now.',
      )
      queryClient.invalidateQueries({ queryKey: ['snapshot', projectId] })
    }
    return () => {
      source.close()
    }
  }, [projectId, queryClient])

  const openMutation = useMutation({
    mutationFn: (path: string) => openProject(path),
    onSuccess: (data) => {
      queryClient.setQueryData(['project-current'], data)
      queryClient.setQueryData(['snapshot', data.project.project_id], data)
      dismissClientNotice('project-open')
    },
    onError: (err: Error) => reportClientError('project-open', 'project_open_failed', err.message),
  })

  const initMutation = useMutation({
    mutationFn: ({ path, title }: { path: string; title?: string }) => initProject(path, title),
    onSuccess: (data) => {
      queryClient.setQueryData(['project-current'], data)
      queryClient.setQueryData(['snapshot', data.project.project_id], data)
      dismissClientNotice('project-init')
    },
    onError: (err: Error) => reportClientError('project-init', 'project_init_failed', err.message),
  })

  const selectedNode = useMemo(
    () => liveSnapshot?.graph.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [liveSnapshot, selectedNodeId],
  )

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
      .filter((template) => template.ref !== 'value_input.py')
      .map<PaletteEntry>((template) => ({
        key: `template:${template.ref}`,
        title: template.title,
        description: template.ref,
        kind: 'template',
        templateRef: template.ref,
      }))
    const pipelineEntries: PaletteEntry[] = [
      {
        key: 'pipeline-placeholder',
        title: 'Pipeline templates coming later',
        description: 'Reserved section for post-MVP pipeline templates.',
        kind: 'pipeline',
      },
    ]
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
    await queryClient.invalidateQueries({ queryKey: ['snapshot', projectId] })
    await queryClient.invalidateQueries({ queryKey: ['project-current'] })
  }

  async function mutateGraph(operations: Array<Record<string, unknown>>) {
    if (!liveSnapshot || !projectId) {
      return
    }
    try {
      const response = await patchGraph(projectId, liveSnapshot.graph.meta.graph_version, operations as never)
      dismissClientNotice('graph-update')
      if (response.interrupted_run) {
        reportClientWarning(
          `run-interrupted:${response.interrupted_run.run_id}`,
          'run_interrupted_by_graph_edit',
          'The current run was interrupted because the graph changed.',
          {
            nodeId: response.interrupted_run.node_id,
            details: {
              run_id: response.interrupted_run.run_id,
              node_ids: response.interrupted_run.node_ids,
            },
          },
        )
      }
      await refreshSnapshot()
    } catch (err) {
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
    const x = snapToGrid(placement?.x ?? baseX)
    const y = snapToGrid(placement?.y ?? baseY)
    if (payload.type === 'file_input') {
      await mutateGraph([
        { type: 'add_file_input_node', node_id: payload.nodeId, title: payload.title, x, y },
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
        },
      ])
      return
    }
    await mutateGraph([
      { type: 'add_notebook_node', node_id: payload.nodeId, title: payload.title, x, y },
    ])
  }

  async function openCreateBlockDialog(entry: PaletteEntry, placement?: { x: number; y: number }) {
    if (!liveSnapshot || blockCreateMode(entry) === null) {
      return
    }
    const baseX = 120 + (liveSnapshot.graph.nodes.length % 4) * 420
    const baseY = 120 + Math.floor(liveSnapshot.graph.nodes.length / 4) * 280
    const x = placement?.x ?? baseX
    const y = placement?.y ?? baseY
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

  async function handleConfirmCreateBlock(payload: { nodeId: string; title: string }) {
    if (!pendingBlockCreation) {
      return
    }
    const { entry, x, y } = pendingBlockCreation
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
      setPendingBlockCreation(null)
      return
    }
    if (entry.kind === 'empty') {
      await handleCreateNode({ type: 'empty', nodeId: payload.nodeId, title: payload.title }, { x, y })
      setPendingBlockCreation(null)
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
    const response = await runNode(projectId, payload.nodeId, 'run_stale', 'use_stale')
    if (response.status === 'failed') {
      reportClientError(`run:${payload.nodeId}:run_stale`, 'run_failed', runFailureMessage(response, 'Run failed.'), { nodeId: payload.nodeId, details: response })
    }
    await refreshSnapshot()
    setPendingBlockCreation(null)
  }

  async function handleCreateFileBlock(payload: { nodeId: string; title: string; file: File; artifactName: string }) {
    if (!pendingBlockCreation || !projectId) {
      return
    }
    const { x, y } = pendingBlockCreation
    await mutateGraph([
      {
        type: 'add_file_input_node',
        node_id: payload.nodeId,
        title: payload.title,
        artifact_name: payload.artifactName.trim() || 'file',
        x: snapToGrid(x),
        y: snapToGrid(y),
      },
    ])
    await uploadFile(projectId, payload.nodeId, payload.file)
    await refreshSnapshot()
    setPendingBlockCreation(null)
  }

  function handlePaletteDragStart(entry: PaletteEntry, position?: { x: number; y: number }) {
    if (entry.kind === 'pipeline') {
      return
    }
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
      const initialResponse = await runNode(projectId, nodeId, mode, mode === 'edit_run' ? null : 'use_stale')
      let response = initialResponse
      if (initialResponse.requires_confirmation) {
        const useUpstream = window.confirm('Some inputs are stale or pending. Click OK to refresh upstream notebooks first, or Cancel to use stale data.')
        response = await runNode(
          projectId,
          nodeId,
          mode,
          useUpstream ? 'run_upstream' : 'use_stale',
        )
      }
      if (typeof response.session_id === 'string') {
        launchEditorTab(projectId, response.session_id, nodeId)
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
      reportClientError(`run:${nodeId}:${mode}`, 'run_failed', message, { nodeId })
    }
  }

  async function handleRunAll() {
    if (!projectId) {
      return
    }
    if (!window.confirm('Run all pending and stale notebooks in dependency order?')) {
      return
    }
    try {
      const response = await runAll(projectId)
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
    await cancelRun(projectId, active.run_id)
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
      await uploadFile(projectId, nodeId, file)
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
    await createCheckpoint(projectId)
    await refreshSnapshot()
  }

  async function handleRestoreCheckpoint(checkpointId: string) {
    if (!projectId) {
      return
    }
    if (!window.confirm(`Restore checkpoint ${checkpointId}?`)) {
      return
    }
    await restoreCheckpoint(projectId, checkpointId)
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
    setSelectedNodeId((current) => (current && nodes.some((node) => node.id === current) ? null : current))
    setArtifactNodeId((current) => (current && nodes.some((node) => node.id === current) ? null : current))
  }

  async function handleDismissNotice(notice: AppNotice) {
    if (notice.origin === 'client' || !projectId) {
      dismissClientNotice(notice.issue_id)
      return
    }
    await dismissNotice(projectId, notice.issue_id)
    await refreshSnapshot()
  }

  const counts = liveSnapshot ? globalArtifactCounts(liveSnapshot) : { ready: 0, stale: 0, pending: 0 }
  const activeRun = liveSnapshot ? currentRun(liveSnapshot) : null

  if (loadingSession) {
    return (
      <SessionLoadingScreen
        projectId={loadingSession.projectId}
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
            <div className={`drop-preview-badge ${draggedPaletteEntry.kind === 'file_input' || draggedPaletteEntry.kind === 'value_input' ? 'tone-input' : draggedPaletteEntry.kind === 'template' ? 'tone-template' : 'tone-custom'}`}>
              {draggedPaletteEntry.kind === 'file_input' ? 'F' : draggedPaletteEntry.kind === 'value_input' ? 'V' : draggedPaletteEntry.kind === 'template' ? 'T' : 'C'}
            </div>
            <div className="drop-preview-copy">
              <strong>{draggedPaletteEntry.title}</strong>
              <span>Drop to place block</span>
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
              onConnect={handleConnect}
              onEdgesChange={handleEdgeChanges}
              onNodeSelect={setSelectedNodeId}
              onRunNode={handleRunNode}
              onOpenArtifacts={(nodeId) => {
                setArtifactNodeId(nodeId)
                setArtifactExplorerOpen(true)
              }}
              onCanvasInteract={() => setTemplatesCollapsed(true)}
              onNodeMove={handleNodeMove}
              onNodesDelete={handleNodesDelete}
              selectedNodeId={selectedNodeId}
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

        <aside className="sidebar right floating-panel">
          <div className="panel">
            <div className="panel-header-row">
              <h2>Inspector</h2>
              {selectedNode ? <button className="secondary" onClick={() => setSelectedNodeId(null)}>Clear</button> : null}
            </div>
            {selectedNode ? (
              <NodeInspector
                snapshot={liveSnapshot as ProjectSnapshot}
                node={selectedNode}
                onToggleHiddenInput={handleToggleHiddenInput}
                onUploadFile={handleUploadFile}
                onOpenTemplate={setTemplateRefView}
              />
            ) : (
              <p className="muted-copy">Select a node to inspect its docs, ports, bindings, and actions.</p>
            )}
          </div>
        </aside>
      </section>

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
                  <ArtifactCard key={`${artifact.node_id}/${artifact.artifact_name}`} artifact={artifact} projectId={projectId} />
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
          <div className="form-grid compact open-project-form">
            <label>
              <span>Project path</span>
              <input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="/path/to/project" />
            </label>
            <div className="inline-actions">
              <button className="secondary" onClick={() => openMutation.mutate(projectPath)} disabled={!projectPath.trim() || openMutation.isPending}>
                {openMutation.isPending ? 'Opening...' : 'Open'}
              </button>
            </div>
            <label>
              <span>New project title</span>
              <input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="Study 2026 03" />
            </label>
            <button
              onClick={() => initMutation.mutate({ path: projectPath, title: projectTitle || undefined })}
              disabled={!projectPath.trim() || initMutation.isPending}
            >
              {initMutation.isPending ? 'Creating...' : 'Init project'}
            </button>
          </div>
          <div className="stack-list subtle">
            <div><span>ID</span><strong>{liveSnapshot.project.project_id}</strong></div>
            <div><span>Root</span><strong>{liveSnapshot.project.root}</strong></div>
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

      <NoticeOverlay
        notices={overlayNotices}
        onDismiss={(notice) => void handleDismissNotice(notice)}
        onOpenNode={(nodeId) => setSelectedNodeId(nodeId)}
      />
    </div>
  )
}

function SessionLoadingScreen({
  projectId,
  sessionId,
  nodeId,
  onCancel,
}: {
  projectId: string
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
          <div><span>Project</span><strong>{projectId}</strong></div>
          <div><span>Session</span><strong>{sessionId}</strong></div>
        </div>
        <div className="spinner" />
        <button className="secondary" onClick={() => {
          onCancel()
          window.close()
        }}>Close</button>
      </div>
    </div>
  )
}

function launchEditorTab(projectId: string, sessionId: string, nodeId: string) {
  const params = new URLSearchParams({
    project_id: projectId,
    session_id: sessionId,
    node_id: nodeId,
  })
  window.open(`/?${params.toString()}`, '_blank', 'noopener,noreferrer')
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
                    className={`palette-main ${entry.kind === 'pipeline' ? '' : 'draggable-block'}`}
                    onClick={() => void onCreate(entry)}
                    disabled={entry.kind === 'pipeline'}
                    draggable={entry.kind !== 'pipeline'}
                    onDragStart={(event) => {
                      if (entry.kind === 'pipeline') {
                        return
                      }
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
                {entry.kind === 'template' || entry.kind === 'value_input' ? (
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
}: {
  notices: AppNotice[]
  onDismiss: (notice: AppNotice) => void
  onOpenNode: (nodeId: string) => void
}) {
  if (!notices.length) {
    return null
  }

  return (
    <div className="notice-overlay" aria-live="polite" aria-label="Errors and warnings">
      {notices.map((notice) => {
        const dismissible = notice.severity === 'warning'
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
              {notice.node_id ? (
                <button className="secondary small" onClick={() => onOpenNode(notice.node_id as string)}>Open node</button>
              ) : null}
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
  onToggleHiddenInput,
  onUploadFile,
  onOpenTemplate,
}: {
  snapshot: ProjectSnapshot
  node: NodeRecord
  onToggleHiddenInput: (node: NodeRecord, inputName: string) => Promise<void>
  onUploadFile: (nodeId: string, file: File) => Promise<void>
  onOpenTemplate: (templateRef: string) => void
}) {
  const badge = badgeForNode(snapshot, node)
  const counts = artifactCounts(snapshot, node.id)
  const template = templateByRef(snapshot, node.template?.ref)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  return (
    <div className="inspector-stack">
      <div className="badge-line">
        <span className="rf-badge static" title={badge.title}>{badge.label}</span>
        <strong>{node.title}</strong>
      </div>
      <div className="stack-list subtle">
        <div><span>Node ID</span><strong>{node.id}</strong></div>
        <div><span>Kind</span><strong>{node.kind}</strong></div>
        <div><span>State</span><strong>{node.state}</strong></div>
        <div><span>Artifacts</span><ArtifactCounts counts={counts} showLabels /></div>
      </div>

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
        <h3>Warnings</h3>
        <div className="warning-list">
          {snapshot.notices.filter((issue) => issue.node_id === node.id).map((issue) => (
            <div key={issue.issue_id} className={`warning-chip ${issue.severity}`}>
              <strong>{issue.code}</strong>
              <span>{issue.message}</span>
            </div>
          ))}
          {!snapshot.notices.some((issue) => issue.node_id === node.id) ? <p className="muted-copy">No active validation issues.</p> : null}
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
    </div>
  )
}

function ArtifactCard({ artifact, projectId }: { artifact: ArtifactRecord; projectId: string | null }) {
  const downloadHref = artifactEndpoint(projectId, artifact, 'download')
  const imageSrc = artifact.preview?.kind === 'file' && artifact.preview.mime_type?.startsWith('image/')
    ? artifactEndpoint(projectId, artifact, 'content')
    : null

  return (
    <article className={`artifact-card state-${artifact.state}`}>
      <div className="artifact-head">
        <div>
          <strong>{artifact.node_id}/{artifact.artifact_name}</strong>
          <span className={`artifact-state-label ${artifact.state}`}>{artifact.state}</span>
          <span>{artifact.data_type ?? 'unknown'}</span>
        </div>
        <a className="secondary link-button" href={downloadHref}>Download</a>
      </div>
      <ArtifactPreviewPanel preview={artifact.preview} imageSrc={imageSrc} />
      <div className="artifact-meta-grid">
        <span>Storage: {artifact.storage_kind ?? 'n/a'}</span>
        <span>Lineage: {artifact.lineage_mode ?? 'n/a'}</span>
        <span>Created: {formatTimestamp(artifact.created_at)}</span>
        <span>Size: {artifact.size_bytes ?? 0} bytes</span>
      </div>
    </article>
  )
}

export default App
