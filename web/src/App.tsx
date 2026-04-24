import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Connection, EdgeChange, Node } from 'reactflow'

import { appUrl, cancelRun, createCheckpoint, currentProject, dismissNotice, downloadNotebookSource, getSnapshot, listSessions, notebookDownloadUrl, patchGraph, restoreCheckpoint, runAll, runNode, runSelection, setArtifactState, setNodeOutputsState, stopSession, uploadFile } from './lib/api'
import { GRID_SIZE, activeRunNodeId, artifactFor, artifactsForDisplay, currentRun, formatTimestamp, globalArtifactCounts, inputState, inputsForNode, outputsForNode, queuedRunNodeIds, templateByRef } from './lib/helpers'
import { areaSettings, type AreaColorKey, type AreaTitlePosition } from './lib/area'
import type { ArtifactRecord, GraphPatchOperation, LayoutRecord, NodeRecord, ProjectSnapshot, SessionRecord, TemplateRecord } from './lib/types'
import type { AppNotice, ClipboardGraph, ClipboardNodeRecord, ConstantValueType, GraphHistoryEntry, GraphMutationPlan, NodeActionItem, OptimisticGraphState, PaletteEntry, PalettePreviewBlock, PortActionMenuState } from './appTypes'
import { applyOptimisticGraphOperations, areaAddOperationForNode, artifactTargetForPort, blockCreateMode, buildConstantValueNotebookSource, clampContextMenuPosition, cloneSnapshot, copiedTitle, createClientNotice, edgeIdForPorts, edgeIdsForPort, editorSessionDetails, expandMutationPlan, fileInputAddOperationForNode, formatMarkdownCode, formatRunBlockedMessage, formatRunFailureMessage, freezeBlockMessage, frozenBlockBlockersForDelete, frozenBlockBlockersForRemovedEdges, frozenBlockBlockersForStaleRoots, isEditableTarget, isEditorOpenConflict, isFreezeConflict, isManagedRunFailure, mergeGraphIntoSnapshot, normalizeNodeId, notebookAddOperationForNode, organizerAddOperationForNode, pipelineDefinitionNodeIds, pipelineTemplateNodeRecords, pipelineTopLeftForCenter, SNAPSHOT_REFRESH_EVENTS, SNAPSHOT_REFRESH_THROTTLE_MS, snapToGrid, uniqueCopiedNodeId } from './lib/appHelpers'
import { ArtifactCard } from './components/ArtifactCard'
import { ArtifactCounts } from './components/ArtifactCounts'
import { BlockPalette } from './components/BlockPalette'
import { ActionButtons } from './components/ActionButtons'
import { ConfirmDialog, CreateConstantValueDialog, CreateFileDialog, CreateNotebookDialog, CreateOrganizerPortDialog, CreatePipelineDialog, EditAreaDialog, EditOrganizerDialog, Modal } from './components/Dialogs'
import { GraphCanvas } from './components/GraphCanvas'
import { Info, Palette, Play, Plus } from './components/Icons'
import { NodeInspector } from './components/NodeInspector'
import { NoticeOverlay } from './components/NoticeOverlay'
import { SessionLoadingScreen } from './components/SessionLoadingScreen'
import { SimpleMarkdown } from './components/SimpleMarkdown'

type ThemeMode = 'system' | 'light' | 'dark'

type PendingBlockCreation = {
  entry: PaletteEntry
  x: number
  y: number
}

type FileNodeEditState = {
  nodeId: string
  title: string
  artifactName: string
  frozen: boolean
}

type NodeActionMenuState = {
  nodeIds: string[]
  x: number
  y: number
  grouped?: boolean
  selectionCount?: number
}

type PendingPipelineCreation = {
  entry: PaletteEntry
  x: number
  y: number
  template: TemplateRecord
  suggestedPrefix: string
  requirePrefix: boolean
}

type PendingOrganizerConnection = {
  organizerNodeId: string
  insertIndex: number
  dataType: string
  portKey: string
  suggestedName: string
  sourceNode: string
  sourcePort: string
  targetNode: string
  targetPort: string
}

type PendingAreaCreation = {
  x: number
  y: number
}

type OrganizerNodeEditState = {
  nodeId: string
  title: string
  ports: Array<{ key: string; name: string; data_type: string }>
  frozen: boolean
}

type AreaNodeEditState = {
  nodeId: string
  title: string
  titlePosition: AreaTitlePosition
  color: AreaColorKey
  filled: boolean
}

const NEW_NODE_WIDTH = 360
const NEW_NODE_HEIGHT = 220
const ORGANIZER_NODE_WIDTH = 160
const ORGANIZER_NODE_HEIGHT = 140
const AREA_NODE_WIDTH = 480
const AREA_NODE_HEIGHT = 280
const PLACEMENT_PADDING = 40
const PLACEMENT_SEARCH_STEP = GRID_SIZE * 2
const MAX_PLACEMENT_RINGS = 24

type PlacementRect = {
  left: number
  top: number
  right: number
  bottom: number
}

type ArtifactMutationState = 'ready' | 'stale'
type NotebookRunScope = 'node' | 'ancestors' | 'descendants'

type ConfirmationState =
  | {
      kind: 'run-upstream'
      nodeId: string | null
      nodeIds?: string[]
      mode: 'run_stale' | 'run_all'
      scope: NotebookRunScope
      message: string
      useStaleDisabled: boolean
      useStaleDisabledReason?: string
    }
  | {
      kind: 'run-all'
    }
  | {
      kind: 'node-outputs-state'
      nodeIds: string[]
      state: ArtifactMutationState
      onlyCurrentState: ArtifactMutationState | 'pending' | null
      title: string
      message: string
    }
  | {
      kind: 'artifact-state'
      nodeId: string
      artifactName: string
      state: ArtifactMutationState
      title: string
      message: string
    }
  | {
      kind: 'node-frozen'
      nodeIds: string[]
      frozen: boolean
      title: string
      message: string
    }
  | {
      kind: 'delete-nodes'
      nodeIds: string[]
      edgeIds: string[]
      createCheckpoint: boolean
      title: string
      message: string
    }

function setSnapshotData(
  queryClient: ReturnType<typeof useQueryClient>,
  fallbackSnapshot: ProjectSnapshot,
  updater: (current: ProjectSnapshot) => ProjectSnapshot,
) {
  queryClient.setQueryData(['snapshot'], (current: ProjectSnapshot | undefined) => updater(current ?? fallbackSnapshot))
  queryClient.setQueryData(['project-current'], (current: ProjectSnapshot | undefined) => updater(current ?? fallbackSnapshot))
}

function App() {
  const queryClient = useQueryClient()
  const [clientNotices, setClientNotices] = useState<AppNotice[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([])
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<string[]>([])
  const [artifactNodeId, setArtifactNodeId] = useState<string | null>(null)
  const [artifactExplorerOpen, setArtifactExplorerOpen] = useState(false)
  const [artifactFilter, setArtifactFilter] = useState('')
  const [paletteInfoEntry, setPaletteInfoEntry] = useState<PaletteEntry | null>(null)
  const [showProjectInfo, setShowProjectInfo] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [templatesCollapsed, setTemplatesCollapsed] = useState(true)
  const [showHiddenTemplates, setShowHiddenTemplates] = useState(false)
  const [paletteSearch, setPaletteSearch] = useState('')
  const [draggedPaletteEntry, setDraggedPaletteEntry] = useState<PaletteEntry | null>(null)
  const [paletteViewport, setPaletteViewport] = useState<{ center: { x: number; y: number }; zoom: number } | null>(null)
  const [pendingBlockCreation, setPendingBlockCreation] = useState<PendingBlockCreation | null>(null)
  const [pendingPipelineCreation, setPendingPipelineCreation] = useState<PendingPipelineCreation | null>(null)
  const [pendingOrganizerConnection, setPendingOrganizerConnection] = useState<PendingOrganizerConnection | null>(null)
  const [pendingAreaCreation, setPendingAreaCreation] = useState<PendingAreaCreation | null>(null)
  const [fileNodeEdit, setFileNodeEdit] = useState<FileNodeEditState | null>(null)
  const [organizerNodeEdit, setOrganizerNodeEdit] = useState<OrganizerNodeEditState | null>(null)
  const [areaNodeEdit, setAreaNodeEdit] = useState<AreaNodeEditState | null>(null)
  const [nodeActionMenu, setNodeActionMenu] = useState<NodeActionMenuState | null>(null)
  const [portActionMenu, setPortActionMenu] = useState<PortActionMenuState | null>(null)
  const [optimisticGraph, setOptimisticGraph] = useState<OptimisticGraphState | null>(null)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [confirmationState, setConfirmationState] = useState<ConfirmationState | null>(null)
  const [clipboardGraph, setClipboardGraph] = useState<ClipboardGraph | null>(null)
  const [graphHistoryPast, setGraphHistoryPast] = useState<GraphHistoryEntry[]>([])
  const [graphHistoryFuture, setGraphHistoryFuture] = useState<GraphHistoryEntry[]>([])
  const [serverClock, setServerClock] = useState(() => ({
    serverNowMs: Date.now(),
    clientAnchorMs: Date.now(),
  }))
  const [pasteSequence, setPasteSequence] = useState(0)
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
  const nodeActionMenuRef = useRef<HTMLDivElement | null>(null)
  const portActionMenuRef = useRef<HTMLDivElement | null>(null)
  const snapshotRefreshTimeoutRef = useRef<number | null>(null)
  const snapshotRefreshInFlightRef = useRef<Promise<void> | null>(null)
  const snapshotRefreshQueuedRef = useRef(false)
  const pendingClickSelectionRef = useRef<{ nodeIds: string[]; edgeIds: string[]; token: number } | null>(null)
  const pendingClickSelectionTokenRef = useRef(0)
  const lastSnapshotRefreshAtRef = useRef(0)
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

  function canOpenInspectorForNode(nodeId: string | null): boolean {
    if (!nodeId || !liveSnapshot) {
      return false
    }
    const node = liveSnapshot.graph.nodes.find((entry) => entry.id === nodeId)
    return Boolean(node && node.kind !== 'area')
  }

  function applySelection(nodeIds: string[], edgeIds: string[], options: { openInspector?: boolean } = {}) {
    setSelectedNodeIds(nodeIds)
    setSelectedEdgeIds(edgeIds)
    const singleNodeId = nodeIds.length === 1 && edgeIds.length === 0 ? nodeIds[0] : null
    const canOpenInspector = canOpenInspectorForNode(singleNodeId)
    setSelectedNodeId(singleNodeId)
    if (options.openInspector !== undefined) {
      setInspectorOpen(options.openInspector && canOpenInspector)
      return
    }
    setInspectorOpen(canOpenInspector)
  }

  function selectSingleNode(nodeId: string | null, options: { openInspector?: boolean } = {}) {
    applySelection(nodeId ? [nodeId] : [], [], { openInspector: options.openInspector ?? Boolean(nodeId) })
  }

  function selectCreatedNodes(nodeIds: string[]) {
    if (!nodeIds.length) {
      return
    }
    applySelection(nodeIds, [], { openInspector: nodeIds.length === 1 })
  }

  function selectionMatches(left: string[], right: string[]): boolean {
    if (left.length !== right.length) {
      return false
    }
    const leftSet = new Set(left)
    return right.every((item) => leftSet.has(item))
  }

  function rememberPendingClickSelection(nodeIds: string[], edgeIds: string[]) {
    const token = pendingClickSelectionTokenRef.current + 1
    pendingClickSelectionTokenRef.current = token
    pendingClickSelectionRef.current = { nodeIds, edgeIds, token }
    window.requestAnimationFrame(() => {
      if (pendingClickSelectionRef.current?.token === token) {
        pendingClickSelectionRef.current = null
      }
    })
  }

  function toggleSelectionItem(items: string[], itemId: string): string[] {
    return items.includes(itemId) ? items.filter((item) => item !== itemId) : [...items, itemId]
  }

  function handleNodeSelection(nodeId: string, options: { additive?: boolean } = {}) {
    if (options.additive) {
      const nextNodeIds = toggleSelectionItem(selectedNodeIds, nodeId)
      rememberPendingClickSelection(nextNodeIds, selectedEdgeIds)
      applySelection(nextNodeIds, selectedEdgeIds, { openInspector: false })
      return
    }
    rememberPendingClickSelection([nodeId], [])
    selectSingleNode(nodeId)
  }

  function handleEdgeSelection(edgeId: string, options: { additive?: boolean } = {}) {
    if (options.additive) {
      const nextEdgeIds = toggleSelectionItem(selectedEdgeIds, edgeId)
      rememberPendingClickSelection(selectedNodeIds, nextEdgeIds)
      applySelection(selectedNodeIds, nextEdgeIds, { openInspector: false })
      return
    }
    rememberPendingClickSelection([], [edgeId])
    applySelection([], [edgeId], { openInspector: false })
  }

  function openSelectedNodeActionMenu(position: { x: number; y: number }, nodeIds = selectedNodeIds) {
    if (!nodeIds.length) {
      return
    }
    const clamped = clampContextMenuPosition(position)
    const usesCurrentSelection = nodeIds.length === selectedNodeIds.length
      && nodeIds.every((nodeId) => selectedNodeIds.includes(nodeId))
    const selectionCount = usesCurrentSelection ? selectedNodeIds.length + selectedEdgeIds.length : nodeIds.length
    setPortActionMenu(null)
    setNodeActionMenu({
      nodeIds,
      x: clamped.x,
      y: clamped.y,
      grouped: selectionCount > 1,
      selectionCount,
    })
  }

  useEffect(() => {
    return () => {
      if (snapshotRefreshTimeoutRef.current !== null) {
        window.clearTimeout(snapshotRefreshTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (projectId) {
      return
    }
    if (snapshotRefreshTimeoutRef.current !== null) {
      window.clearTimeout(snapshotRefreshTimeoutRef.current)
      snapshotRefreshTimeoutRef.current = null
    }
    snapshotRefreshQueuedRef.current = false
  }, [projectId])

  useEffect(() => {
    setGraphHistoryPast([])
    setGraphHistoryFuture([])
  }, [projectId])

  useEffect(() => {
    if (!liveSnapshot?.server_time) {
      return
    }
    const parsedServerTime = Date.parse(liveSnapshot.server_time)
    if (Number.isNaN(parsedServerTime)) {
      return
    }
    setServerClock({
      serverNowMs: parsedServerTime,
      clientAnchorMs: Date.now(),
    })
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
        applySelection([], [], { openInspector: false })
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
      void scheduleSnapshotRefresh()
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
      void refreshSnapshotNow()
    })
    source.onerror = () => {
      reportClientError(
        'connection-sse-disconnected',
        'server_connection_lost',
        'The server connection was interrupted. Reconnecting now.',
      )
      void scheduleSnapshotRefresh()
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

  const nodeActionMenuNode = useMemo(
    () => {
      if (!liveSnapshot || !nodeActionMenu?.nodeIds.length) {
        return []
      }
      const nodeIdSet = new Set(nodeActionMenu.nodeIds)
      return liveSnapshot.graph.nodes.filter((node) => nodeIdSet.has(node.id))
    },
    [liveSnapshot, nodeActionMenu],
  )

  const primaryNodeActionMenuNode = nodeActionMenuNode[0] ?? null

  const portActionMenuNode = useMemo(
    () => liveSnapshot?.graph.nodes.find((node) => node.id === portActionMenu?.nodeId) ?? null,
    [liveSnapshot, portActionMenu],
  )

  const portActionArtifact = useMemo(
    () => (liveSnapshot && portActionMenu ? artifactTargetForPort(liveSnapshot, portActionMenu) : null),
    [liveSnapshot, portActionMenu],
  )

  const portActionHead = useMemo(
    () => (liveSnapshot && portActionArtifact
      ? artifactFor(liveSnapshot, portActionArtifact.nodeId, portActionArtifact.artifactName)
      : null),
    [liveSnapshot, portActionArtifact],
  )

  const portActionEdgeIds = useMemo(
    () => (liveSnapshot && portActionMenu ? edgeIdsForPort(liveSnapshot, portActionMenu) : []),
    [liveSnapshot, portActionMenu],
  )

  const portActionMutationFrozenBlockers = useMemo(
    () => (liveSnapshot && portActionArtifact
      ? frozenBlockBlockersForStaleRoots(liveSnapshot, [portActionArtifact.nodeId])
      : []),
    [liveSnapshot, portActionArtifact],
  )

  const portDisconnectFrozenBlockers = useMemo(
    () => (liveSnapshot && portActionEdgeIds.length
      ? frozenBlockBlockersForRemovedEdges(liveSnapshot, portActionEdgeIds)
      : []),
    [liveSnapshot, portActionEdgeIds],
  )

  const portActionMutationBlockedReason = portActionMutationFrozenBlockers.length
    ? freezeBlockMessage(portActionMutationFrozenBlockers)
    : undefined

  const portDisconnectBlockedReason = portDisconnectFrozenBlockers.length
    ? freezeBlockMessage(portDisconnectFrozenBlockers)
    : undefined

  function nodeInputsAreReady(node: NodeRecord): boolean {
    if (!liveSnapshot || node.kind !== 'notebook') {
      return true
    }
    return (node.interface?.inputs ?? []).every((port) => inputState(liveSnapshot, node.id, port) === 'ready')
  }

  function nodeTitle(nodeId: string): string {
    return liveSnapshot?.graph.nodes.find((node) => node.id === nodeId)?.title ?? nodeId
  }

  function nodeLabel(nodeId: string): string {
    const title = nodeTitle(nodeId)
    return title === nodeId ? formatMarkdownCode(nodeId) : `${formatMarkdownCode(title)} (${formatMarkdownCode(nodeId)})`
  }

  function isOrganizerGhostHandle(handleId: string | null | undefined): boolean {
    return Boolean(handleId && (handleId.startsWith('ghost-in:') || handleId.startsWith('ghost-out:')))
  }

  function organizerInsertIndexFromHandle(handleId: string): number {
    const index = Number(handleId.split(':')[1] ?? 0)
    return Number.isFinite(index) && index >= 0 ? index : 0
  }

  function nextAvailableNodeId(base: string): string {
    const normalizedBase = normalizeNodeId(base) || 'node'
    if (!existingNodeIdSet.has(normalizedBase)) {
      return normalizedBase
    }
    let index = 2
    while (existingNodeIdSet.has(`${normalizedBase}_${index}`)) {
      index += 1
    }
    return `${normalizedBase}_${index}`
  }

  function nextOrganizerPortKey(existingKeys: Set<string>, suggestedName: string): string {
    const normalizedBase = normalizeNodeId(suggestedName) || 'port'
    if (!existingKeys.has(normalizedBase)) {
      return normalizedBase
    }
    let index = 2
    while (existingKeys.has(`${normalizedBase}_${index}`)) {
      index += 1
    }
    return `${normalizedBase}_${index}`
  }

  function isNodeQueuedForExecution(nodeId: string): boolean {
    if (!liveSnapshot) {
      return false
    }
    const node = liveSnapshot.graph.nodes.find((entry) => entry.id === nodeId)
    return node?.orchestrator_state?.status === 'queued' || node?.orchestrator_state?.status === 'running'
  }

  function notebookDependencyClosure(nodeIds: string[], direction: 'upstream' | 'downstream'): string[] {
    if (!liveSnapshot || !nodeIds.length) {
      return []
    }
    const nodeById = new Map(liveSnapshot.graph.nodes.map((node) => [node.id, node]))
    const adjacency = new Map<string, string[]>()
    for (const edge of liveSnapshot.graph.edges) {
      const key = direction === 'upstream' ? edge.target_node : edge.source_node
      const connected = adjacency.get(key) ?? []
      connected.push(direction === 'upstream' ? edge.source_node : edge.target_node)
      adjacency.set(key, connected)
    }
    const planned = new Set<string>()
    const queue = [...nodeIds]
    const visited = new Set<string>()
    while (queue.length) {
      const currentNodeId = queue.shift() as string
      if (visited.has(currentNodeId)) {
        continue
      }
      visited.add(currentNodeId)
      const node = nodeById.get(currentNodeId)
      if (!node) {
        continue
      }
      if (node.kind === 'notebook') {
        planned.add(currentNodeId)
      }
      for (const relatedNodeId of adjacency.get(currentNodeId) ?? []) {
        queue.push(relatedNodeId)
      }
    }
    return Array.from(planned)
  }

  function plannedNotebookIdsForRun(nodeId: string, scope: NotebookRunScope, action: 'use_stale' | 'run_upstream' | null = null): string[] {
    if (scope === 'node') {
      return action === 'run_upstream' ? notebookDependencyClosure([nodeId], 'upstream') : [nodeId]
    }
    if (scope === 'ancestors') {
      return notebookDependencyClosure([nodeId], 'upstream')
    }
    const descendants = notebookDependencyClosure([nodeId], 'downstream')
    if (action !== 'run_upstream') {
      return descendants
    }
    return notebookDependencyClosure(descendants, 'upstream')
  }

  function plannedNotebookIdsForSelectionRun(nodeIds: string[], action: 'use_stale' | 'run_upstream' | null = null): string[] {
    const notebookNodeIds = (liveSnapshot?.graph.nodes ?? [])
      .filter((node) => node.kind === 'notebook' && nodeIds.includes(node.id))
      .map((node) => node.id)
    if (action !== 'run_upstream') {
      return notebookNodeIds
    }
    return notebookDependencyClosure(notebookNodeIds, 'upstream')
  }

  function runResponseHasPendingInputs(response: Record<string, unknown>): boolean {
    if (!Array.isArray(response.blocked_inputs)) {
      return false
    }
    return response.blocked_inputs.some((blockedInput) => {
      return Boolean(blockedInput && typeof blockedInput === 'object' && (blockedInput as { state?: unknown }).state === 'pending')
    })
  }

  function plannedNotebookIdsForRunAll(): string[] {
    if (!liveSnapshot) {
      return []
    }
    return liveSnapshot.graph.nodes
      .filter((node) => node.kind === 'notebook' && (
        node.state !== 'ready'
        || node.orchestrator_state?.status === 'queued'
        || node.orchestrator_state?.status === 'running'
      ))
      .map((node) => node.id)
  }

  async function openEditorSessionsForNodeIds(nodeIds: string[]): Promise<SessionRecord[]> {
    if (!nodeIds.length) {
      return []
    }
    const targetNodeIds = new Set(nodeIds)
    try {
      const sessions = await listSessions()
      return sessions.filter((session) => targetNodeIds.has(session.node_id))
    } catch {
      return activeEditorNodeIds
        .filter((nodeId) => targetNodeIds.has(nodeId))
        .map((nodeId) => ({ session_id: '', node_id: nodeId, run_id: '', url: '' }))
    }
  }

  async function ensureNoOpenEditorsForRun(nodeIds: string[]): Promise<boolean> {
    const blockingSessions = await openEditorSessionsForNodeIds(nodeIds)
    if (!blockingSessions.length) {
      return true
    }
    const blockingNodeIds = Array.from(new Set(blockingSessions.map((session) => session.node_id)))
    const labels = blockingNodeIds.map((nodeId) => `\`${nodeTitle(nodeId)}\` (${nodeId})`).join(', ')
    if (blockingSessions.length === 1 && blockingSessions[0].session_id && blockingSessions[0].url) {
      const session = blockingSessions[0]
      reportClientWarning(
        `run-editor-open:${session.node_id}`,
        'editor_already_open',
        `Cannot start this orchestrated run while an editor is open for ${nodeLabel(session.node_id)}.`,
        {
          nodeId: session.node_id,
          details: { session_id: session.session_id, session_url: session.url, ready: session.ready },
        },
      )
      return false
    }
    reportClientWarning(
      `run-editor-open:${blockingNodeIds.join(',')}`,
      'editor_open_in_run_queue',
      `Cannot start this orchestrated run while editors are open for ${labels}. Close or kill those editors first.`,
      { nodeId: blockingNodeIds.length === 1 ? blockingNodeIds[0] : null, details: { node_ids: blockingNodeIds } },
    )
    return false
  }

  function reportEditorBlockedByExecution(nodeId: string) {
    const message = isNodeQueuedForExecution(nodeId)
      ? `Cannot open the editor for ${nodeLabel(nodeId)} while it is queued or running in the orchestrated execution queue.`
      : `Cannot open the editor for ${nodeLabel(nodeId)} right now.`
    reportClientWarning(`editor-blocked-by-run:${nodeId}`, 'editor_blocked_by_execution', message, { nodeId })
  }

  async function handleSetNodeOutputsStateForNodes(
    nodeIds: string[],
    state: ArtifactMutationState,
    onlyCurrentState: ArtifactMutationState | 'pending' | null = null,
  ) {
    if (!projectId || !nodeIds.length) {
      return
    }
    try {
      await Promise.all(nodeIds.map((nodeId) => setNodeOutputsState(nodeId, state, onlyCurrentState)))
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Node output state update failed.'
      reportClientError(
        `node-output-state:${nodeIds.join(',')}:${state}:${onlyCurrentState ?? 'all'}`,
        'node_output_state_update_failed',
        message,
        { nodeId: nodeIds.length === 1 ? nodeIds[0] : null, details: { node_ids: nodeIds } },
      )
    }
  }

  async function handleSetNodesFrozen(nodeIds: string[], frozen: boolean) {
    if (!nodeIds.length) {
      return
    }
    const redo = {
      operations: nodeIds.map((nodeId) => ({ type: 'update_node_frozen', node_id: nodeId, frozen } satisfies GraphPatchOperation)),
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
    setNodeActionMenu(null)
  }

  function requestSetNodesFrozen(nodeIds: string[], frozen: boolean) {
    if (!nodeIds.length) {
      return
    }
    if (frozen) {
      void handleSetNodesFrozen(nodeIds, true)
      return
    }
    setConfirmationState({
      kind: 'node-frozen',
      nodeIds,
      frozen: false,
      title: nodeIds.length === 1 ? 'Unfreeze block?' : 'Unfreeze selected blocks?',
      message: nodeIds.length === 1
        ? 'This will unfreeze the block and any frozen descendants downstream of it.'
        : `This will unfreeze ${nodeIds.length} selected blocks and any frozen descendants downstream of them.`,
    })
  }

  function requestDeleteSelection(nodeIds: string[], edgeIds: string[], options: { createCheckpoint?: boolean } = {}) {
    if (!projectId || !liveSnapshot || (!nodeIds.length && !edgeIds.length)) {
      return
    }
    if (!nodeIds.length) {
      void handleDeleteSelection(nodeIds, edgeIds)
      return
    }
    const nodes = nodeIds
      .map((nodeId) => liveSnapshot.graph.nodes.find((entry) => entry.id === nodeId) ?? null)
      .filter((node): node is NodeRecord => node !== null)
    if (!nodes.length) {
      return
    }
    const createCheckpointBeforeDelete = options.createCheckpoint ?? true
    const title = nodes.length === 1 ? 'Delete block?' : 'Delete selected blocks?'
    const message = nodes.length === 1
      ? `Delete block "${nodes[0].title}"?`
      : `Delete ${nodes.length} selected blocks?`
    setConfirmationState({
      kind: 'delete-nodes',
      nodeIds: nodes.map((node) => node.id),
      edgeIds,
      createCheckpoint: createCheckpointBeforeDelete,
      title,
      message,
    })
  }

  function nodeActionsForNode(node: NodeRecord, options: { dismissMenu?: () => void } = {}): NodeActionItem[] {
    const dismissMenu = options.dismissMenu ?? (() => undefined)
    const freezeBlockedOutputMutations = liveSnapshot ? frozenBlockBlockersForStaleRoots(liveSnapshot, [node.id]) : []
    const deleteFrozenBlockers = liveSnapshot ? frozenBlockBlockersForDelete(liveSnapshot, node.id) : []
    const outputMutationBlockedReason = freezeBlockedOutputMutations.length ? freezeBlockMessage(freezeBlockedOutputMutations) : undefined
    const deleteBlockedReason = deleteFrozenBlockers.length ? freezeBlockMessage(deleteFrozenBlockers) : undefined
    const artifactHeads = liveSnapshot
      ? [...(node.interface?.outputs ?? []), ...(node.interface?.assets ?? [])]
          .map((port) => artifactFor(liveSnapshot, node.id, port.name))
          .filter((artifact): artifact is ArtifactRecord => artifact !== undefined && artifact.current_version_id !== null)
      : []
    const canMarkOutputsStale = artifactHeads.some((artifact) => artifact.state !== 'stale')
    const canMarkOutputsReady = artifactHeads.some((artifact) => artifact.state === 'stale') && nodeInputsAreReady(node)
    const actions: NodeActionItem[] = []

    if (node.kind === 'notebook') {
      actions.push({
        key: 'run-node',
        label: 'Run',
        tone: 'success',
        onClick: () => {
          dismissMenu()
          void handleRunNode(node.id, 'run_stale')
        },
      })
    }

    if (node.kind === 'file_input') {
      actions.push({
        key: 'edit-file-input',
        label: 'Edit block',
        onClick: () => {
          dismissMenu()
          openFileNodeEdit(node.id)
        },
      })
    }

    if (node.kind === 'organizer') {
      actions.push({
        key: 'edit-organizer',
        label: 'Edit block',
        onClick: () => {
          dismissMenu()
          openOrganizerNodeEdit(node.id)
        },
      })
    }

    if (node.kind === 'area') {
      actions.push({
        key: 'edit-area',
        label: 'Edit block',
        onClick: () => {
          dismissMenu()
          openAreaNodeEdit(node.id)
        },
      })
    }

    if (node.kind === 'notebook') {
      actions.push({
        key: 'download-notebook',
        label: 'Download notebook',
        href: notebookDownloadUrl(node.id),
        onClick: () => dismissMenu(),
      })
    }

    if (node.kind !== 'organizer' && node.kind !== 'area') {
      actions.push(
        {
          key: 'mark-outputs-stale',
          label: 'Mark all outputs stale',
          disabled: !canMarkOutputsStale || Boolean(outputMutationBlockedReason),
          title: outputMutationBlockedReason,
          onClick: () => {
            dismissMenu()
            void handleSetNodeOutputsStateAction(node.id, 'stale')
          },
        },
        {
          key: 'mark-stale-outputs-ready',
          label: 'Mark stale outputs ready',
          disabled: !canMarkOutputsReady || Boolean(outputMutationBlockedReason),
          title: outputMutationBlockedReason,
          onClick: () => {
            dismissMenu()
            setConfirmationState({
              kind: 'node-outputs-state',
              nodeIds: [node.id],
              state: 'ready',
              onlyCurrentState: 'stale',
              title: 'Mark stale outputs ready?',
              message: 'This bypasses consistency checks and marks every stale output on this block as ready.',
            })
          },
        },
      )
    }

    if (node.kind !== 'area') {
      actions.push({
        key: 'toggle-frozen',
        label: node.ui?.frozen ? 'Unfreeze block' : 'Freeze block',
        onClick: () => {
          dismissMenu()
          requestSetNodesFrozen([node.id], !node.ui?.frozen)
        },
      })
    }

    actions.push({
      key: 'delete-node',
      label: 'Delete block',
      tone: 'danger',
      disabled: Boolean(deleteBlockedReason),
      title: deleteBlockedReason,
      onClick: () => {
        dismissMenu()
        handleDeleteNodeAction(node.id)
      },
    })

    return actions
  }

  function nodeActionsForMenu(nodeIds: string[], options: { dismissMenu?: () => void } = {}): NodeActionItem[] {
    const dismissMenu = options.dismissMenu ?? (() => undefined)
    const menuNodes = nodeIds
      .map((nodeId) => liveSnapshot?.graph.nodes.find((node) => node.id === nodeId) ?? null)
      .filter((node): node is NodeRecord => node !== null)
    if (menuNodes.length === 1 && !nodeActionMenu?.grouped) {
      return nodeActionsForNode(menuNodes[0], options)
    }
    if (!menuNodes.length) {
      return []
    }

    const freezableNodes = menuNodes.filter((node) => node.kind !== 'area')
    const runnableNotebookNodeIds = menuNodes.filter((node) => node.kind === 'notebook').map((node) => node.id)
    const affectedOutputNodeIds = menuNodes.filter((node) => {
      if (!liveSnapshot) {
        return false
      }
      return [...(node.interface?.outputs ?? []), ...(node.interface?.assets ?? [])]
        .some((port) => artifactFor(liveSnapshot, node.id, port.name)?.current_version_id !== null)
    }).map((node) => node.id)
    const staleMutationNodes = menuNodes.filter((node) => {
      if (!liveSnapshot) {
        return false
      }
      return [...(node.interface?.outputs ?? []), ...(node.interface?.assets ?? [])]
        .some((port) => artifactFor(liveSnapshot, node.id, port.name)?.state !== 'stale')
    })
    const readyMutationNodes = menuNodes.filter((node) => {
      if (!liveSnapshot) {
        return false
      }
      const hasStaleOutputs = [...(node.interface?.outputs ?? []), ...(node.interface?.assets ?? [])]
        .some((port) => artifactFor(liveSnapshot, node.id, port.name)?.state === 'stale')
      return hasStaleOutputs && nodeInputsAreReady(node)
    })
    const staleEligibleNodeIds = staleMutationNodes.map((node) => node.id)
    const readyEligibleNodeIds = readyMutationNodes.map((node) => node.id)
    const outputMutationFrozenBlockers = liveSnapshot
      ? Array.from(new Map(
          staleMutationNodes.flatMap((node) => frozenBlockBlockersForStaleRoots(liveSnapshot, [node.id]).map((blocker) => [blocker.id, blocker] as const)),
        ).values())
      : []
    const readyMutationFrozenBlockers = liveSnapshot
      ? Array.from(new Map(
          readyMutationNodes.flatMap((node) => frozenBlockBlockersForStaleRoots(liveSnapshot, [node.id]).map((blocker) => [blocker.id, blocker] as const)),
        ).values())
      : []
    const deleteFrozenBlockers = liveSnapshot
      ? Array.from(new Map(
          menuNodes.flatMap((node) => frozenBlockBlockersForDelete(liveSnapshot, node.id).map((blocker) => [blocker.id, blocker] as const)),
        ).values())
      : []
    const freezableNodeIds = freezableNodes.filter((node) => !node.ui?.frozen).map((node) => node.id)
    const unfreezableNodeIds = freezableNodes.filter((node) => node.ui?.frozen).map((node) => node.id)
    const outputMutationBlockedReason = outputMutationFrozenBlockers.length ? freezeBlockMessage(outputMutationFrozenBlockers) : undefined
    const readyMutationBlockedReason = readyMutationFrozenBlockers.length ? freezeBlockMessage(readyMutationFrozenBlockers) : undefined
    const deleteBlockedReason = deleteFrozenBlockers.length ? freezeBlockMessage(deleteFrozenBlockers) : undefined

    return [
      {
        key: 'run-selected-nodes',
        label: 'Run',
        tone: 'success',
        disabled: runnableNotebookNodeIds.length === 0,
        onClick: () => {
          dismissMenu()
          void handleRunSelection(runnableNotebookNodeIds)
        },
      },
      {
        key: 'mark-selected-outputs-stale',
        label: 'Mark outputs stale',
        disabled: staleEligibleNodeIds.length === 0 || affectedOutputNodeIds.length === 0 || Boolean(outputMutationBlockedReason),
        title: outputMutationBlockedReason,
        onClick: () => {
          dismissMenu()
          void handleSetNodeOutputsStateForNodes(staleEligibleNodeIds, 'stale')
        },
      },
      {
        key: 'mark-selected-stale-outputs-ready',
        label: 'Mark stale outputs ready',
        disabled: readyEligibleNodeIds.length === 0 || Boolean(readyMutationBlockedReason),
        title: readyMutationBlockedReason,
        onClick: () => {
          dismissMenu()
          setConfirmationState({
            kind: 'node-outputs-state',
            nodeIds: readyEligibleNodeIds,
            state: 'ready',
            onlyCurrentState: 'stale',
            title: 'Mark selected stale outputs ready?',
            message: `This bypasses consistency checks and marks stale outputs as ready on ${readyEligibleNodeIds.length} selected block${readyEligibleNodeIds.length === 1 ? '' : 's'}.`,
          })
        },
      },
      {
        key: 'freeze-selected-blocks',
        label: 'Freeze blocks',
        disabled: freezableNodeIds.length === 0,
        onClick: () => {
          dismissMenu()
          void handleSetNodesFrozen(freezableNodeIds, true)
        },
      },
      {
        key: 'unfreeze-selected-blocks',
        label: 'Unfreeze blocks',
        disabled: unfreezableNodeIds.length === 0,
        onClick: () => {
          dismissMenu()
          requestSetNodesFrozen(unfreezableNodeIds, false)
        },
      },
      {
        key: 'delete-selected-nodes',
        label: 'Delete blocks',
        tone: 'danger',
        disabled: Boolean(deleteBlockedReason),
        title: deleteBlockedReason,
        onClick: () => {
          dismissMenu()
          requestDeleteSelection(menuNodes.map((node) => node.id), [], { createCheckpoint: true })
        },
      },
    ]
  }

  useEffect(() => {
    if (!projectId || !selectedNode || selectedNode.execution_meta?.status !== 'running') {
      return
    }
    const interval = window.setInterval(() => {
      void scheduleSnapshotRefresh()
    }, 1000)
    return () => window.clearInterval(interval)
  }, [projectId, selectedNode?.id, selectedNode?.execution_meta?.status])

  useEffect(() => {
    if (!nodeActionMenu) {
      return
    }
    function handlePointerDown(event: PointerEvent) {
      if (nodeActionMenuRef.current?.contains(event.target as globalThis.Node)) {
        return
      }
      setNodeActionMenu(null)
    }
    window.addEventListener('pointerdown', handlePointerDown)
    return () => window.removeEventListener('pointerdown', handlePointerDown)
  }, [nodeActionMenu])

  useEffect(() => {
    if (!portActionMenu) {
      return
    }
    function handlePointerDown(event: PointerEvent) {
      if (portActionMenuRef.current?.contains(event.target as globalThis.Node)) {
        return
      }
      setPortActionMenu(null)
    }
    window.addEventListener('pointerdown', handlePointerDown)
    return () => window.removeEventListener('pointerdown', handlePointerDown)
  }, [portActionMenu])

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
    const interval = window.setInterval(() => {
      void loadSessions()
    }, 2000)
    function handleWindowFocus() {
      void loadSessions()
    }
    window.addEventListener('focus', handleWindowFocus)
    return () => {
      cancelled = true
      window.clearInterval(interval)
      window.removeEventListener('focus', handleWindowFocus)
    }
  }, [projectId])

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
    const orderedArtifacts = artifactsForDisplay(liveSnapshot, selectedArtifacts)
    const needle = artifactFilter.trim().toLowerCase()
    if (!needle) {
      return orderedArtifacts
    }
    return orderedArtifacts.filter((artifact) => {
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

  function palettePreviewBlocks(
    title: string,
    kind: 'empty' | 'value_input' | 'file_input' | 'organizer' | 'area' | 'template' | 'pipeline',
    template: TemplateRecord | null = null,
  ): PalettePreviewBlock[] {
    if (kind === 'organizer') {
      return [{
        key: `${kind}:${title}`,
        title,
        kind: 'organizer',
        x: 0,
        y: 0,
        width: 160,
        height: 140,
      }]
    }
    if (kind === 'area') {
      return [{
        key: `${kind}:${title}`,
        title,
        kind: 'area',
        x: 0,
        y: 0,
        width: 320,
        height: 220,
      }]
    }
    if (!template || template.kind !== 'pipeline') {
      return [{
        key: `${kind}:${title}`,
        title,
        kind: kind === 'file_input' ? 'file_input' : 'notebook',
        x: 0,
        y: 0,
        width: NEW_NODE_WIDTH,
        height: NEW_NODE_HEIGHT,
      }]
    }
    const definitionNodes = template.definition?.nodes ?? []
    const layout = template.definition?.layout ?? []
    if (!definitionNodes.length || !layout.length) {
      return [{
        key: `${template.ref}:preview`,
        title,
        kind: 'notebook',
        x: 0,
        y: 0,
        width: NEW_NODE_WIDTH,
        height: NEW_NODE_HEIGHT,
      }]
    }
    const layoutByNodeId = Object.fromEntries(layout.map((entry) => [entry.node_id, entry]))
    const placedNodes = definitionNodes
      .map((node) => {
        const nodeLayout = layoutByNodeId[node.id]
        if (!nodeLayout) {
          return null
        }
        return {
          key: `${template.ref}:${node.id}`,
          title: node.title,
          kind: node.kind,
          x: nodeLayout.x,
          y: nodeLayout.y,
          width: nodeLayout.w,
          height: nodeLayout.h,
        } satisfies PalettePreviewBlock
      })
      .filter((node): node is PalettePreviewBlock => node !== null)
    if (!placedNodes.length) {
      return [{
        key: `${template.ref}:preview`,
        title,
        kind: 'notebook',
        x: 0,
        y: 0,
        width: NEW_NODE_WIDTH,
        height: NEW_NODE_HEIGHT,
      }]
    }
    const minX = Math.min(...placedNodes.map((node) => node.x))
    const minY = Math.min(...placedNodes.map((node) => node.y))
    return placedNodes.map((node) => ({
      ...node,
      x: node.x - minX,
      y: node.y - minY,
    }))
  }

  function palettePreviewSize(blocks: PalettePreviewBlock[]): { width: number; height: number } {
    if (!blocks.length) {
      return { width: NEW_NODE_WIDTH, height: NEW_NODE_HEIGHT }
    }
    return {
      width: Math.max(...blocks.map((block) => block.x + block.width)),
      height: Math.max(...blocks.map((block) => block.y + block.height)),
    }
  }

  const paletteEntries = useMemo<PaletteEntry[]>(() => {
    const builtins = [
      {
        key: 'empty',
        title: 'New notebook',
        description: 'Generic notebook scaffold with one sample input and output.',
        kind: 'empty',
        previewBlocks: palettePreviewBlocks('New notebook', 'empty'),
      },
      {
        key: 'value_input',
        title: 'Constant value',
        description: 'Create one or more ready-to-use constant outputs.',
        kind: 'value_input',
        previewBlocks: palettePreviewBlocks('Constant value', 'value_input'),
      },
      {
        key: 'file_input',
        title: 'File',
        description: 'Upload a file and expose it as a file artifact.',
        kind: 'file_input',
        previewBlocks: palettePreviewBlocks('File', 'file_input'),
      },
      {
        key: 'organizer',
        title: 'Organizer',
        description: 'Add a slim passthrough patch panel for grouping and routing connections.',
        kind: 'organizer',
        previewBlocks: palettePreviewBlocks('Organizer', 'organizer'),
      },
      {
        key: 'area',
        title: 'Area',
        description: 'Add a visual grouping rectangle behind functional blocks.',
        kind: 'area',
        previewBlocks: palettePreviewBlocks('Area', 'area'),
      },
    ] satisfies PaletteEntry[]
    const builtinsWithPreview = builtins.map<PaletteEntry>((entry) => ({
      ...entry,
      previewSize: palettePreviewSize(entry.previewBlocks ?? []),
    }))
    const templateEntries = (liveSnapshot?.templates ?? [])
      .filter(
        (template) => template.kind === 'notebook'
          && template.ref !== 'builtin/value_input'
          && (showHiddenTemplates || !template.hidden),
      )
      .map<PaletteEntry>((template) => {
        const previewBlocks = palettePreviewBlocks(template.title, 'template')
        return {
          key: `template:${template.ref}`,
          title: template.title,
          documentation: template.documentation,
          kind: 'template',
          templateRef: template.ref,
          templateName: template.name,
          templateProvider: template.provider,
          previewBlocks,
          previewSize: palettePreviewSize(previewBlocks),
        }
      })
    const pipelineEntries = (liveSnapshot?.templates ?? [])
      .filter((template) => template.kind === 'pipeline')
      .map<PaletteEntry>((template) => {
        const previewBlocks = palettePreviewBlocks(template.title, 'pipeline', template)
        return {
          key: `pipeline:${template.ref}`,
          title: template.title,
          documentation: template.documentation,
          kind: 'pipeline',
          templateRef: template.ref,
          templateName: template.name,
          templateProvider: template.provider,
          previewBlocks,
          previewSize: palettePreviewSize(previewBlocks),
        }
      })
    const needle = paletteSearch.trim().toLowerCase()
    const allEntries = [...builtinsWithPreview, ...templateEntries, ...pipelineEntries]
    if (!needle) {
      return allEntries
    }
    return allEntries.filter((entry) => (
      `${entry.title} ${entry.description ?? ''} ${entry.documentation ?? ''} ${entry.templateName ?? ''} ${entry.templateProvider ?? ''}`
    ).toLowerCase().includes(needle))
  }, [liveSnapshot, paletteSearch, showHiddenTemplates])

  const groupTemplatesByProvider = useMemo(() => {
    const providers = new Set((liveSnapshot?.templates ?? []).map((template) => template.provider))
    return providers.size > 1
  }, [liveSnapshot])

  const paletteInfoTemplate = useMemo(() => {
    if (!liveSnapshot || !paletteInfoEntry?.templateRef) {
      return null
    }
    return templateByRef(liveSnapshot, paletteInfoEntry.templateRef)
  }, [liveSnapshot, paletteInfoEntry])

  const paletteInfoPipelineReferences = useMemo(() => {
    if (!liveSnapshot || paletteInfoTemplate?.kind !== 'pipeline') {
      return []
    }
    const refs = Array.from(new Set(
      (paletteInfoTemplate.definition?.nodes ?? [])
        .map((node) => (typeof node.template_ref === 'string' && node.template_ref ? node.template_ref : null))
        .filter((ref): ref is string => ref !== null),
    ))
    return refs.map((ref) => {
      const template = templateByRef(liveSnapshot, ref)
      return {
        ref,
        title: template?.title ?? ref,
      }
    })
  }, [liveSnapshot, paletteInfoTemplate])

  function openTemplateInfo(templateRef: string) {
    const template = liveSnapshot ? templateByRef(liveSnapshot, templateRef) : null
    setPaletteInfoEntry({
      key: `info:${templateRef}`,
      title: template?.title ?? templateRef,
      documentation: template?.documentation,
      kind: template?.kind === 'pipeline' ? 'pipeline' : 'template',
      templateRef,
      templateName: template?.name,
      templateProvider: template?.provider,
    })
  }

  function notebookNeedsInlineSource(node: NodeRecord): boolean {
    return node.kind === 'notebook' && !(node.template?.ref && node.template_status === 'template')
  }

  async function notebookSourceByNodeIds(nodeIds: string[]): Promise<Map<string, string>> {
    if (!liveSnapshot) {
      return new Map()
    }
    const notebookNodes = nodeIds
      .map((nodeId) => liveSnapshot.graph.nodes.find((node) => node.id === nodeId) ?? null)
      .filter((node): node is NodeRecord => node !== null && notebookNeedsInlineSource(node))
    const uniqueNodeIds = Array.from(new Set(notebookNodes.map((node) => node.id)))
    const entries = await Promise.all(
      uniqueNodeIds.map(async (nodeId) => [nodeId, await downloadNotebookSource(nodeId)] as const),
    )
    return new Map(entries)
  }

  function simpleHistoryEntryForPlan(snapshotData: ProjectSnapshot, redo: GraphMutationPlan): GraphHistoryEntry | null {
    const undoOperations: GraphPatchOperation[] = []
    for (const operation of [...expandMutationPlan(redo)].reverse()) {
      switch (operation.type) {
        case 'add_notebook_node':
        case 'add_file_input_node':
        case 'add_organizer_node':
        case 'add_area_node':
          undoOperations.push({ type: 'delete_node', node_id: operation.node_id })
          break
        case 'add_edge':
          undoOperations.push({
            type: 'remove_edge',
            edge_id: edgeIdForPorts(
              operation.source_node,
              operation.source_port,
              operation.target_node,
              operation.target_port,
            ),
          })
          break
        case 'remove_edge': {
          const edge = snapshotData.graph.edges.find((entry) => entry.id === operation.edge_id)
          if (!edge) {
            return null
          }
          undoOperations.push({
            type: 'add_edge',
            source_node: edge.source_node,
            source_port: edge.source_port,
            target_node: edge.target_node,
            target_port: edge.target_port,
          })
          break
        }
        case 'update_node_layout': {
          const layout = snapshotData.graph.layout.find((entry) => entry.node_id === operation.node_id)
          if (!layout) {
            return null
          }
          undoOperations.push({
            type: 'update_node_layout',
            node_id: operation.node_id,
            x: layout.x,
            y: layout.y,
            w: layout.w,
            h: layout.h,
          })
          break
        }
        case 'update_node_title': {
          const node = snapshotData.graph.nodes.find((entry) => entry.id === operation.node_id)
          if (!node) {
            return null
          }
          undoOperations.push({ type: 'update_node_title', node_id: operation.node_id, title: node.title })
          break
        }
        case 'update_organizer_ports': {
          const node = snapshotData.graph.nodes.find((entry) => entry.id === operation.node_id)
          if (!node) {
            return null
          }
          const previousPorts = node.ui?.organizer_ports ?? []
          const nextPorts = operation.ports
          const nextPortByKey = new Map(nextPorts.map((port) => [port.key, port]))
          const removedKeys = previousPorts
            .filter((port) => {
              const nextPort = nextPortByKey.get(port.key)
              return !nextPort || nextPort.data_type !== port.data_type
            })
            .map((port) => port.key)
          undoOperations.push({
            type: 'update_organizer_ports',
            node_id: operation.node_id,
            ports: previousPorts.map((port) => ({ ...port })),
          })
          undoOperations.push(
            ...snapshotData.graph.edges
              .filter((edge) => {
                if (edge.source_node === operation.node_id) {
                  return removedKeys.includes(edge.source_port)
                }
                if (edge.target_node === operation.node_id) {
                  return removedKeys.includes(edge.target_port)
                }
                return false
              })
              .map((edge) => ({
                type: 'add_edge',
                source_node: edge.source_node,
                source_port: edge.source_port,
                target_node: edge.target_node,
                target_port: edge.target_port,
              } satisfies GraphPatchOperation)),
          )
          break
        }
        case 'update_area_style': {
          const node = snapshotData.graph.nodes.find((entry) => entry.id === operation.node_id)
          if (!node) {
            return null
          }
          undoOperations.push({
            type: 'update_area_style',
            node_id: operation.node_id,
            title_position: String(node.ui?.title_position ?? 'top-left'),
            color: String(node.ui?.area_color ?? 'blue'),
            filled: Boolean(node.ui?.area_filled ?? true),
          })
          break
        }
        case 'update_node_frozen': {
          const node = snapshotData.graph.nodes.find((entry) => entry.id === operation.node_id)
          if (!node) {
            return null
          }
          undoOperations.push({
            type: 'update_node_frozen',
            node_id: operation.node_id,
            frozen: Boolean(node.ui?.frozen),
          })
          break
        }
        default:
          return null
      }
    }
    return {
      undo: { operations: undoOperations },
      redo,
    }
  }

  async function deleteHistoryEntry(nodeIds: string[]): Promise<GraphHistoryEntry | null> {
    if (!liveSnapshot || !nodeIds.length) {
      return null
    }
    const deletedNodeIdSet = new Set(nodeIds)
    const nodes = nodeIds
      .map((nodeId) => liveSnapshot.graph.nodes.find((node) => node.id === nodeId) ?? null)
      .filter((node): node is NodeRecord => node !== null)
    if (nodes.length !== nodeIds.length) {
      return null
    }
    const layouts = nodeIds
      .map((nodeId) => liveSnapshot.graph.layout.find((entry) => entry.node_id === nodeId) ?? null)
      .filter((entry): entry is LayoutRecord => entry !== null)
    if (layouts.length !== nodeIds.length) {
      return null
    }
    const sourceByNodeId = await notebookSourceByNodeIds(nodeIds)
    const layoutByNodeId = new Map(layouts.map((entry) => [entry.node_id, entry]))
      const undoNodeOperations: GraphPatchOperation[] = nodes.map((node) => {
        const layout = layoutByNodeId.get(node.id) as LayoutRecord
        if (node.kind === 'notebook') {
          return notebookAddOperationForNode(node, layout, sourceByNodeId.get(node.id) ?? null, node.id, node.title)
        }
      if (node.kind === 'organizer') {
        return organizerAddOperationForNode(node, layout, node.id, node.title)
      }
      if (node.kind === 'area') {
        return areaAddOperationForNode(node, layout, node.id, node.title)
      }
        return fileInputAddOperationForNode(node, layout, node.id, node.title)
      })
    const restoredEdges = liveSnapshot.graph.edges.filter(
      (edge) => deletedNodeIdSet.has(edge.source_node) || deletedNodeIdSet.has(edge.target_node),
    )
    const undoEdgeOperations: GraphPatchOperation[] = restoredEdges.map((edge) => ({
      type: 'add_edge',
      source_node: edge.source_node,
      source_port: edge.source_port,
      target_node: edge.target_node,
      target_port: edge.target_port,
    }))
    return {
      undo: { operations: [...undoNodeOperations, ...undoEdgeOperations] },
      redo: { operations: nodeIds.map((nodeId) => ({ type: 'delete_node', node_id: nodeId })) },
    }
  }

  async function deleteSelectionHistoryEntry(nodeIds: string[], edgeIds: string[]): Promise<GraphHistoryEntry | null> {
    if (!liveSnapshot || (!nodeIds.length && !edgeIds.length)) {
      return null
    }
    const deletedNodeIdSet = new Set(nodeIds)
    const detachedEdgeIds = edgeIds.filter((edgeId) => {
      const edge = liveSnapshot.graph.edges.find((entry) => entry.id === edgeId)
      if (!edge) {
        return false
      }
      return !deletedNodeIdSet.has(edge.source_node) && !deletedNodeIdSet.has(edge.target_node)
    })
    const nodeHistory = nodeIds.length ? await deleteHistoryEntry(nodeIds) : null
    const edgeHistory = detachedEdgeIds.length
      ? simpleHistoryEntryForPlan(
          liveSnapshot,
          {
            operations: detachedEdgeIds.map((edgeId) => ({ type: 'remove_edge', edge_id: edgeId } satisfies GraphPatchOperation)),
          },
        )
      : null
    if (nodeIds.length && !nodeHistory) {
      return null
    }
    if (detachedEdgeIds.length && !edgeHistory) {
      return null
    }
    const undoOperations = [
      ...(nodeHistory?.undo.operations ?? []),
      ...(edgeHistory?.undo.operations ?? []),
    ]
    const redoOperations = [
      ...(edgeHistory?.redo.operations ?? []),
      ...(nodeHistory?.redo.operations ?? []),
    ]
    if (!undoOperations.length || !redoOperations.length) {
      return null
    }
    return {
      undo: { operations: undoOperations },
      redo: { operations: redoOperations },
    }
  }

  async function clipboardGraphForSelection(nodeIds: string[]): Promise<ClipboardGraph | null> {
    if (!liveSnapshot || !nodeIds.length) {
      return null
    }
    const selectedNodeIdSet = new Set(nodeIds)
    const sourceByNodeId = await notebookSourceByNodeIds(nodeIds)
    const nodes = nodeIds
      .map((nodeId) => {
        const node = liveSnapshot.graph.nodes.find((entry) => entry.id === nodeId)
        const layout = liveSnapshot.graph.layout.find((entry) => entry.node_id === nodeId)
        if (!node || !layout) {
          return null
        }
        return {
          node,
          layout,
          sourceText: sourceByNodeId.get(nodeId) ?? null,
        } satisfies ClipboardNodeRecord
      })
      .filter((entry): entry is ClipboardNodeRecord => entry !== null)
    if (nodes.length !== nodeIds.length) {
      return null
    }
    const edges = liveSnapshot.graph.edges.filter(
      (edge) => selectedNodeIdSet.has(edge.source_node) && selectedNodeIdSet.has(edge.target_node),
    )
    return { nodes, edges }
  }

  function queueSnapshotRefresh(delayMs: number) {
    if (snapshotRefreshTimeoutRef.current !== null) {
      return
    }
    snapshotRefreshTimeoutRef.current = window.setTimeout(() => {
      snapshotRefreshTimeoutRef.current = null
      if (!snapshotRefreshQueuedRef.current) {
        return
      }
      snapshotRefreshQueuedRef.current = false
      void refreshSnapshotNow()
    }, delayMs)
  }

  async function refreshSnapshotNow() {
    if (!projectId) {
      return
    }
    if (snapshotRefreshTimeoutRef.current !== null) {
      window.clearTimeout(snapshotRefreshTimeoutRef.current)
      snapshotRefreshTimeoutRef.current = null
    }
    if (snapshotRefreshInFlightRef.current) {
      snapshotRefreshQueuedRef.current = true
      await snapshotRefreshInFlightRef.current
      return
    }
    const refreshPromise = queryClient
      .refetchQueries({ queryKey: ['snapshot'], exact: true })
      .then(() => {
        lastSnapshotRefreshAtRef.current = Date.now()
      })
      .finally(() => {
        snapshotRefreshInFlightRef.current = null
        if (!snapshotRefreshQueuedRef.current || !projectId) {
          return
        }
        const remainingDelay = Math.max(
          0,
          SNAPSHOT_REFRESH_THROTTLE_MS - (Date.now() - lastSnapshotRefreshAtRef.current),
        )
        snapshotRefreshQueuedRef.current = false
        if (remainingDelay === 0) {
          void refreshSnapshotNow()
          return
        }
        snapshotRefreshQueuedRef.current = true
        queueSnapshotRefresh(remainingDelay)
      })
    snapshotRefreshInFlightRef.current = refreshPromise
    await refreshPromise
  }

  function scheduleSnapshotRefresh() {
    if (!projectId) {
      return
    }
    snapshotRefreshQueuedRef.current = true
    if (snapshotRefreshInFlightRef.current) {
      return
    }
    const remainingDelay = Math.max(
      0,
      SNAPSHOT_REFRESH_THROTTLE_MS - (Date.now() - lastSnapshotRefreshAtRef.current),
    )
    if (remainingDelay === 0) {
      snapshotRefreshQueuedRef.current = false
      void refreshSnapshotNow()
      return
    }
    queueSnapshotRefresh(remainingDelay)
  }

  async function refreshSnapshot() {
    await refreshSnapshotNow()
  }

  async function refreshActiveEditorNodeIds() {
    if (!projectId) {
      setActiveEditorNodeIds([])
      return
    }
    try {
      const sessions = await listSessions()
      setActiveEditorNodeIds(Array.from(new Set(sessions.map((session) => session.node_id))))
    } catch {
      setActiveEditorNodeIds([])
    }
  }

  async function mutateGraph(
    operations: GraphPatchOperation[],
    options: { history?: GraphHistoryEntry | null; onSuccess?: () => void } = {},
  ): Promise<boolean> {
    const currentSnapshot = (queryClient.getQueryData(['snapshot']) as ProjectSnapshot | undefined)
      ?? (queryClient.getQueryData(['project-current']) as ProjectSnapshot | undefined)
      ?? liveSnapshot
    if (!currentSnapshot || !projectId) {
      return false
    }
    const rollbackSnapshot = currentSnapshot
    try {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: ['snapshot'], exact: true }),
        queryClient.cancelQueries({ queryKey: ['project-current'], exact: true }),
      ])
      const optimistic = applyOptimisticGraphOperations(currentSnapshot, operations as Array<Record<string, unknown>>)
      if (optimistic) {
        setOptimisticGraph(optimistic)
      }
      const response = await patchGraph(currentSnapshot.graph.meta.graph_version, operations)
      setSnapshotData(queryClient, rollbackSnapshot, (current) => mergeGraphIntoSnapshot(current, response))
      if (options.history) {
        setGraphHistoryPast((current) => [...current, options.history as GraphHistoryEntry])
        setGraphHistoryFuture([])
      }
      options.onSuccess?.()
      dismissClientNotice('graph-update')
      await refreshSnapshot()
      return true
    } catch (err) {
      setOptimisticGraph(null)
      setSnapshotData(queryClient, rollbackSnapshot, () => rollbackSnapshot)
      const message = err instanceof Error ? err.message : 'Graph update failed.'
      if (isFreezeConflict(message)) {
        reportClientWarning('graph-update-frozen', 'frozen_block', message)
      } else {
        reportClientError('graph-update', 'graph_update_failed', message)
      }
      await refreshSnapshot()
      return false
    }
  }

  async function stopEditorsForNodes(nodeIds: string[]) {
    if (!projectId || !nodeIds.length) {
      return
    }
    const targetNodeIds = new Set(nodeIds)
    try {
      const sessions = await listSessions()
      const matching = sessions.filter((session) => targetNodeIds.has(session.node_id))
      await Promise.all(matching.map((session) => stopSession(session.session_id)))
      setActiveEditorNodeIds((current) => current.filter((nodeId) => !targetNodeIds.has(nodeId)))
    } catch {
      // Deletion still stops editors on the backend as a fallback.
    }
  }

  async function handleDeleteSelection(nodeIds: string[], edgeIds: string[]) {
    if (!projectId || !liveSnapshot || (!nodeIds.length && !edgeIds.length)) {
      return
    }
    const history = await deleteSelectionHistoryEntry(nodeIds, edgeIds)
    const deletedNodeIdSet = new Set(nodeIds)
    const detachedEdgeIds = edgeIds.filter((edgeId) => {
      const edge = liveSnapshot.graph.edges.find((entry) => entry.id === edgeId)
      if (!edge) {
        return false
      }
      return !deletedNodeIdSet.has(edge.source_node) && !deletedNodeIdSet.has(edge.target_node)
    })
    if (!nodeIds.length && !detachedEdgeIds.length) {
      return
    }
    if (nodeIds.length) {
      await stopEditorsForNodes(nodeIds)
    }
    const operations: GraphPatchOperation[] = [
      ...detachedEdgeIds.map((edgeId) => ({ type: 'remove_edge', edge_id: edgeId } satisfies GraphPatchOperation)),
      ...nodeIds.map((nodeId) => ({ type: 'delete_node', node_id: nodeId } satisfies GraphPatchOperation)),
    ]
    const success = await mutateGraph(operations, { history })
    if (!success) {
      return
    }
    applySelection([], [], { openInspector: false })
    setArtifactNodeId((current) => (current && deletedNodeIdSet.has(current) ? null : current))
    setNodeActionMenu(null)
    setPortActionMenu(null)
  }

  async function handleSetArtifactStateAction(nodeId: string, artifactName: string, state: ArtifactMutationState) {
    if (!projectId) {
      return
    }
    try {
      await setArtifactState(nodeId, artifactName, state)
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Artifact state update failed.'
      reportClientError(
        `artifact-state:${nodeId}:${artifactName}:${state}`,
        'artifact_state_update_failed',
        message,
        { nodeId },
      )
    }
  }

  async function handleSetNodeOutputsStateAction(
    nodeId: string,
    state: ArtifactMutationState,
    onlyCurrentState: ArtifactMutationState | 'pending' | null = null,
  ) {
    if (!projectId) {
      return
    }
    try {
      await setNodeOutputsState(nodeId, state, onlyCurrentState)
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Node output state update failed.'
      reportClientError(
        `node-output-state:${nodeId}:${state}:${onlyCurrentState ?? 'all'}`,
        'node_output_state_update_failed',
        message,
        { nodeId },
      )
    }
  }

  async function handleDisconnectPort(menu: PortActionMenuState) {
    if (!liveSnapshot) {
      return
    }
    const edgeIds = edgeIdsForPort(liveSnapshot, menu)
    if (!edgeIds.length) {
      return
    }
    const redo = { operations: edgeIds.map((edgeId) => ({ type: 'remove_edge', edge_id: edgeId } satisfies GraphPatchOperation)) }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
    setPortActionMenu(null)
  }

  async function handleCreateNode(
    payload: { type: 'empty' | 'template' | 'file_input' | 'organizer' | 'area'; nodeId: string; title: string; templateRef?: string; sourceText?: string; origin?: 'constant_value' | null; area?: { titlePosition: AreaTitlePosition; color: AreaColorKey; filled: boolean } },
    placement?: { x: number; y: number },
  ) {
    const baseX = 120 + ((liveSnapshot?.graph.nodes.length ?? 0) % 4) * 420
    const baseY = 120 + Math.floor((liveSnapshot?.graph.nodes.length ?? 0) / 4) * 280
    const width = payload.type === 'organizer' ? ORGANIZER_NODE_WIDTH : payload.type === 'area' ? AREA_NODE_WIDTH : NEW_NODE_WIDTH
    const height = payload.type === 'organizer' ? ORGANIZER_NODE_HEIGHT : payload.type === 'area' ? AREA_NODE_HEIGHT : NEW_NODE_HEIGHT
    const x = snapToGrid((placement?.x ?? baseX) - width / 2)
    const y = snapToGrid((placement?.y ?? baseY) - height / 2)
    if (payload.type === 'file_input') {
      const redo = {
        operations: [
          { type: 'add_file_input_node', node_id: payload.nodeId, title: payload.title, x, y, w: NEW_NODE_WIDTH, h: NEW_NODE_HEIGHT } satisfies GraphPatchOperation,
        ],
      }
      await mutateGraph(redo.operations, {
        history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null,
        onSuccess: () => selectCreatedNodes([payload.nodeId]),
      })
      return
    }
    if (payload.type === 'organizer') {
      const redo = {
        operations: [
          { type: 'add_organizer_node', node_id: payload.nodeId, title: payload.title, x, y, w: width, h: height } satisfies GraphPatchOperation,
        ],
      }
      await mutateGraph(redo.operations, {
        history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null,
        onSuccess: () => selectCreatedNodes([payload.nodeId]),
      })
      return
    }
    if (payload.type === 'area') {
      const redo = {
        operations: [
          {
            type: 'add_area_node',
            node_id: payload.nodeId,
            title: payload.title,
            ui: payload.area ? {
              title_position: payload.area.titlePosition,
              area_color: payload.area.color,
              area_filled: payload.area.filled,
            } : undefined,
            x,
            y,
            w: width,
            h: height,
          } satisfies GraphPatchOperation,
        ],
      }
      await mutateGraph(redo.operations, {
        history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null,
        onSuccess: () => selectCreatedNodes([payload.nodeId]),
      })
      return
    }
    if (payload.type === 'template') {
      const redo = {
        operations: [
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
          } satisfies GraphPatchOperation,
        ],
      }
      await mutateGraph(redo.operations, {
        history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null,
        onSuccess: () => selectCreatedNodes([payload.nodeId]),
      })
      return
    }
    const redo = {
      operations: [
        { type: 'add_notebook_node', node_id: payload.nodeId, title: payload.title, x, y, w: NEW_NODE_WIDTH, h: NEW_NODE_HEIGHT } satisfies GraphPatchOperation,
      ],
    }
    await mutateGraph(redo.operations, {
      history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null,
      onSuccess: () => selectCreatedNodes([payload.nodeId]),
    })
  }

  async function handleCreatePipelineTemplate(templateRef: string, placement: { x: number; y: number }, nodeIdPrefix?: string | null) {
    if (!liveSnapshot) {
      return
    }
    const createdNodes = pipelineTemplateNodeRecords(liveSnapshot, templateRef, nodeIdPrefix)
    const redo: GraphMutationPlan = {
      operations: [
        {
          type: 'add_pipeline_template',
          template_ref: templateRef,
          x: snapToGrid(placement.x),
          y: snapToGrid(placement.y),
          node_id_prefix: nodeIdPrefix ?? null,
        },
      ],
    }
    const history: GraphHistoryEntry | null = createdNodes.length
      ? {
          undo: {
            operations: createdNodes.map((node) => ({ type: 'delete_node', node_id: node.nodeId })),
          },
          redo,
        }
      : null
    await mutateGraph(redo.operations, {
      history,
      onSuccess: () => selectCreatedNodes(createdNodes.map((node) => node.nodeId)),
    })
  }

  function pipelineTemplateByEntry(entry: PaletteEntry): TemplateRecord | null {
    if (!liveSnapshot || !entry.templateRef) {
      return null
    }
    return liveSnapshot.templates.find((template) => template.kind === 'pipeline' && template.ref === entry.templateRef) ?? null
  }

  function pipelinePlacementSize(template: TemplateRecord): { width: number; height: number } {
    const layout = template.definition?.layout ?? []
    if (!layout.length) {
      return {
        width: template.definition?.nodes?.length ? template.definition.nodes.length * NEW_NODE_WIDTH : NEW_NODE_WIDTH,
        height: NEW_NODE_HEIGHT,
      }
    }
    const minX = Math.min(...layout.map((entry) => entry.x))
    const minY = Math.min(...layout.map((entry) => entry.y))
    const maxRight = Math.max(...layout.map((entry) => entry.x + entry.w))
    const maxBottom = Math.max(...layout.map((entry) => entry.y + entry.h))
    return {
      width: Math.max(maxRight - minX, NEW_NODE_WIDTH),
      height: Math.max(maxBottom - minY, NEW_NODE_HEIGHT),
    }
  }

  function placementSizeForEntry(entry: PaletteEntry): { width: number; height: number } {
    if (entry.kind === 'organizer') {
      return { width: ORGANIZER_NODE_WIDTH, height: ORGANIZER_NODE_HEIGHT }
    }
    if (entry.kind === 'area') {
      return { width: AREA_NODE_WIDTH, height: AREA_NODE_HEIGHT }
    }
    if (entry.kind === 'pipeline') {
      const template = pipelineTemplateByEntry(entry)
      return template ? pipelinePlacementSize(template) : { width: NEW_NODE_WIDTH, height: NEW_NODE_HEIGHT }
    }
    return { width: NEW_NODE_WIDTH, height: NEW_NODE_HEIGHT }
  }

  function placementRectFromCenter(center: { x: number; y: number }, size: { width: number; height: number }): PlacementRect {
    return {
      left: center.x - size.width / 2,
      top: center.y - size.height / 2,
      right: center.x + size.width / 2,
      bottom: center.y + size.height / 2,
    }
  }

  function placementRectForLayout(layout: LayoutRecord): PlacementRect {
    return {
      left: layout.x - PLACEMENT_PADDING,
      top: layout.y - PLACEMENT_PADDING,
      right: layout.x + layout.w + PLACEMENT_PADDING,
      bottom: layout.y + layout.h + PLACEMENT_PADDING,
    }
  }

  function placementRectsIntersect(left: PlacementRect, right: PlacementRect): boolean {
    return left.left < right.right
      && left.right > right.left
      && left.top < right.bottom
      && left.bottom > right.top
  }

  function placementRingOffsets(ring: number): Array<{ dx: number; dy: number }> {
    if (ring === 0) {
      return [{ dx: 0, dy: 0 }]
    }
    const offsets: Array<{ dx: number; dy: number }> = []
    const seen = new Set<string>()
    const push = (dx: number, dy: number) => {
      const key = `${dx}:${dy}`
      if (seen.has(key)) {
        return
      }
      seen.add(key)
      offsets.push({ dx, dy })
    }
    push(ring, 0)
    for (let dy = 1; dy <= ring; dy += 1) {
      push(ring, dy)
    }
    for (let dx = ring - 1; dx >= -ring; dx -= 1) {
      push(dx, ring)
    }
    for (let dy = ring - 1; dy >= -ring; dy -= 1) {
      push(-ring, dy)
    }
    for (let dx = -ring + 1; dx <= ring; dx += 1) {
      push(dx, -ring)
    }
    for (let dy = -ring + 1; dy < 0; dy += 1) {
      push(ring, dy)
    }
    return offsets
  }

  function preferredPlacementAnchor(size: { width: number; height: number }): { x: number; y: number } {
    if (liveSnapshot && selectedNodeIds.length === 1) {
      const selectedNodeId = selectedNodeIds[0]
      const selectedNode = liveSnapshot.graph.nodes.find((node) => node.id === selectedNodeId)
      const selectedLayout = liveSnapshot.graph.layout.find((entry) => entry.node_id === selectedNodeId)
      if (selectedNode && selectedNode.kind !== 'area' && selectedLayout) {
        return {
          x: snapToGrid(selectedLayout.x + selectedLayout.w + PLACEMENT_PADDING + size.width / 2),
          y: snapToGrid(selectedLayout.y + selectedLayout.h / 2),
        }
      }
    }
    if (paletteViewport) {
      return {
        x: snapToGrid(paletteViewport.center.x),
        y: snapToGrid(paletteViewport.center.y),
      }
    }
    return {
      x: 120 + ((liveSnapshot?.graph.nodes.length ?? 0) % 4) * 420,
      y: 120 + Math.floor((liveSnapshot?.graph.nodes.length ?? 0) / 4) * 280,
    }
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

  function suggestPalettePlacement(entry: PaletteEntry): { x: number; y: number } {
    const size = placementSizeForEntry(entry)
    const anchor = preferredPlacementAnchor(size)
    if (!liveSnapshot) {
      return anchor
    }
    const nodeById = new Map(liveSnapshot.graph.nodes.map((node) => [node.id, node]))
    const occupied = liveSnapshot.graph.layout
      .filter((layout) => nodeById.get(layout.node_id)?.kind !== 'area')
      .map(placementRectForLayout)
    for (let ring = 0; ring <= MAX_PLACEMENT_RINGS; ring += 1) {
      for (const offset of placementRingOffsets(ring)) {
        const candidate = {
          x: snapToGrid(anchor.x + offset.dx * PLACEMENT_SEARCH_STEP),
          y: snapToGrid(anchor.y + offset.dy * PLACEMENT_SEARCH_STEP),
        }
        const candidateRect = placementRectFromCenter(candidate, size)
        if (!occupied.some((rect) => placementRectsIntersect(candidateRect, rect))) {
          return candidate
        }
      }
    }
    return anchor
  }

  async function openCreateBlockDialog(entry: PaletteEntry, placement?: { x: number; y: number }) {
    if (!liveSnapshot) {
      return
    }
    const defaultPlacement = suggestPalettePlacement(entry)
    const x = placement?.x ?? defaultPlacement.x
    const y = placement?.y ?? defaultPlacement.y
    if (entry.kind === 'pipeline') {
      const template = pipelineTemplateByEntry(entry)
      if (!template || !entry.templateRef) {
        return
      }
      setPendingBlockCreation(null)
      const pipelinePlacement = pipelineTopLeftForCenter(template, { x, y })
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
    if (entry.kind === 'organizer') {
      await handleCreateNode(
        {
          type: 'organizer',
          nodeId: nextAvailableNodeId('organizer'),
          title: 'Organizer',
        },
        { x, y },
      )
      return
    }
    if (entry.kind === 'area') {
      setPendingAreaCreation({ x, y })
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
      await handleCreateNode(
        {
          type: 'empty',
          nodeId: payload.nodeId,
          title: payload.title,
        },
        { x, y },
      )
    }
  }

  async function handleConfirmCreateArea(payload: { title: string; titlePosition: AreaTitlePosition; color: AreaColorKey; filled: boolean }) {
    if (!pendingAreaCreation) {
      return
    }
    const { x, y } = pendingAreaCreation
    await handleCreateNode(
      {
        type: 'area',
        nodeId: nextAvailableNodeId('area'),
        title: payload.title,
        area: {
          titlePosition: payload.titlePosition,
          color: payload.color,
          filled: payload.filled,
        },
      },
      { x, y },
    )
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
      reportClientError(`run:${payload.nodeId}:run_stale`, 'run_failed', formatRunFailureMessage(liveSnapshot, response, 'Run failed.'), { nodeId: payload.nodeId, details: response })
    }
    await refreshSnapshot()
  }

  async function handleCreateFileBlock(payload: { nodeId: string; title: string; file: File | null; artifactName: string }) {
    if (!pendingBlockCreation || !projectId) {
      return
    }
    const { x, y } = pendingBlockCreation
    setPendingBlockCreation(null)
    const redo = {
      operations: [
        {
          type: 'add_file_input_node',
          node_id: payload.nodeId,
          title: payload.title,
          artifact_name: payload.artifactName.trim() || 'file',
          x: snapToGrid(x - NEW_NODE_WIDTH / 2),
          y: snapToGrid(y - NEW_NODE_HEIGHT / 2),
          w: NEW_NODE_WIDTH,
          h: NEW_NODE_HEIGHT,
        } satisfies GraphPatchOperation,
      ],
    }
    const success = await mutateGraph(redo.operations, {
      history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null,
      onSuccess: () => selectCreatedNodes([payload.nodeId]),
    })
    if (!success) {
      return
    }
    if (payload.file) {
      await handleUploadFile(payload.nodeId, payload.file)
    }
  }

  function handlePaletteDragStart(entry: PaletteEntry, position?: { x: number; y: number }) {
    setDraggedPaletteEntry(entry)
  }

  function handlePaletteDragEnd() {
    setDraggedPaletteEntry(null)
  }

  function handleBlockDrop(x: number, y: number) {
    if (!draggedPaletteEntry) {
      return
    }
    void openCreateBlockDialog(draggedPaletteEntry, { x, y })
    setDraggedPaletteEntry(null)
  }

  function handlePaletteViewportChange(nextViewport: { center: { x: number; y: number }; zoom: number }) {
    setPaletteViewport((current) => {
      if (
        current
        && Math.abs(current.center.x - nextViewport.center.x) < 0.5
        && Math.abs(current.center.y - nextViewport.center.y) < 0.5
        && Math.abs(current.zoom - nextViewport.zoom) < 0.001
      ) {
        return current
      }
      return nextViewport
    })
  }

  async function handleRunNode(nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run', scope: NotebookRunScope = 'node') {
    if (!projectId) {
      return
    }
    const initialAction = mode === 'edit_run'
      ? null
      : scope === 'ancestors'
        ? 'run_upstream'
        : null
    if (mode !== 'edit_run') {
      const plannedNodeIds = plannedNotebookIdsForRun(nodeId, scope, initialAction)
      const canStartRun = await ensureNoOpenEditorsForRun(plannedNodeIds)
      if (!canStartRun) {
        return
      }
    }
    try {
      const initialResponse = await runNode(nodeId, mode, initialAction, scope)
      let response = initialResponse
      if (initialResponse.requires_confirmation) {
        if (mode === 'edit_run') {
          reportClientError(
            `run:${nodeId}:${mode}`,
            'run_failed',
            `Edit runs do not support upstream refresh confirmation for ${nodeLabel(nodeId)}.`,
            { nodeId },
          )
          return
        }
        setConfirmationState({
          kind: 'run-upstream',
          nodeId,
          mode,
          scope,
          message: 'One or more notebooks in this run have stale or pending inputs. Refresh ancestor blocks first, or run on stale inputs?',
          useStaleDisabled: runResponseHasPendingInputs(initialResponse),
          useStaleDisabledReason: runResponseHasPendingInputs(initialResponse)
            ? 'One or more input artifacts are pending and must be refreshed.'
            : undefined,
        })
        return
      }
      if (typeof response.session_id === 'string') {
        setActiveEditorNodeIds((current) => (current.includes(nodeId) ? current : [...current, nodeId]))
        void refreshActiveEditorNodeIds()
        launchEditorTab(response.session_id, nodeId)
      } else if (response.status === 'failed') {
        if (!isManagedRunFailure(response)) {
          reportClientError(`run:${nodeId}:${mode}`, 'run_failed', formatRunFailureMessage(liveSnapshot, response, 'Run failed.'), { nodeId, details: response })
        }
      } else if (response.status === 'blocked') {
        reportClientWarning(
          `run-blocked:${nodeId}:${mode}`,
          'run_blocked',
          formatRunBlockedMessage(liveSnapshot, nodeId, response),
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
          `An editor is already open for ${nodeLabel(nodeId)}.`,
          {
            nodeId,
            details: session
              ? { session_id: session.session_id, session_url: session.url, ready: session.ready }
              : {},
          },
        )
        return
      }
      if (isFreezeConflict(message)) {
        reportClientWarning(`run-frozen:${nodeId}:${mode}`, 'frozen_block', message, { nodeId })
      } else {
        reportClientError(`run:${nodeId}:${mode}`, 'run_failed', message, { nodeId })
      }
    }
  }

  async function handleRunSelection(nodeIds: string[]) {
    if (!projectId || !nodeIds.length) {
      return
    }
    const plannedNodeIds = plannedNotebookIdsForSelectionRun(nodeIds)
    if (!plannedNodeIds.length) {
      return
    }
    const canStartRun = await ensureNoOpenEditorsForRun(plannedNodeIds)
    if (!canStartRun) {
      return
    }
    try {
      const initialResponse = await runSelection(nodeIds)
      if (initialResponse.requires_confirmation) {
        setConfirmationState({
          kind: 'run-upstream',
          nodeId: null,
          nodeIds,
          mode: 'run_stale',
          scope: 'node',
          message: 'One or more notebooks in this run have stale or pending inputs. Refresh ancestor blocks first, or run on stale inputs?',
          useStaleDisabled: runResponseHasPendingInputs(initialResponse),
          useStaleDisabledReason: runResponseHasPendingInputs(initialResponse)
            ? 'One or more input artifacts are pending and must be refreshed.'
            : undefined,
        })
        return
      }
      if (initialResponse.status === 'failed') {
        if (!isManagedRunFailure(initialResponse)) {
          reportClientError('run-selection', 'run_failed', formatRunFailureMessage(liveSnapshot, initialResponse, 'Run failed.'), { details: initialResponse })
        }
      } else if (initialResponse.status === 'blocked') {
        reportClientWarning('run-selection-blocked', 'run_blocked', formatRunBlockedMessage(liveSnapshot, null, initialResponse), { details: initialResponse })
      }
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Run failed.'
      reportClientError('run-selection', 'run_failed', message)
    }
  }

  async function handleRunAll() {
    if (!projectId) {
      return
    }
    setConfirmationState({ kind: 'run-all' })
  }

  async function confirmRunNodeWithAction(
    nodeId: string,
    mode: 'run_stale' | 'run_all',
    scope: NotebookRunScope,
    action: 'run_upstream' | 'use_stale',
  ) {
    if (!projectId) {
      return
    }
    const plannedNodeIds = plannedNotebookIdsForRun(nodeId, scope, action)
    const canStartRun = await ensureNoOpenEditorsForRun(plannedNodeIds)
    if (!canStartRun) {
      return
    }
    try {
      const response = await runNode(nodeId, mode, action, scope)
      if (response.status === 'failed') {
        if (!isManagedRunFailure(response)) {
          reportClientError(`run:${nodeId}:${mode}`, 'run_failed', formatRunFailureMessage(liveSnapshot, response, 'Run failed.'), { nodeId, details: response })
        }
      } else if (response.status === 'blocked') {
        reportClientWarning(
          `run-blocked:${nodeId}:${mode}`,
          'run_blocked',
          formatRunBlockedMessage(liveSnapshot, nodeId, response),
          { nodeId, details: response },
        )
      }
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Run failed.'
      reportClientError(`run:${nodeId}:${mode}`, 'run_failed', message, { nodeId })
    }
  }

  async function confirmRunSelectionWithAction(nodeIds: string[], action: 'run_upstream' | 'use_stale') {
    if (!projectId || !nodeIds.length) {
      return
    }
    const plannedNodeIds = plannedNotebookIdsForSelectionRun(nodeIds, action)
    const canStartRun = await ensureNoOpenEditorsForRun(plannedNodeIds)
    if (!canStartRun) {
      return
    }
    try {
      const response = await runSelection(nodeIds, action)
      if (response.status === 'failed') {
        if (!isManagedRunFailure(response)) {
          reportClientError('run-selection', 'run_failed', formatRunFailureMessage(liveSnapshot, response, 'Run failed.'), { details: response })
        }
      } else if (response.status === 'blocked') {
        reportClientWarning('run-selection-blocked', 'run_blocked', formatRunBlockedMessage(liveSnapshot, null, response), { details: response })
      }
      await refreshSnapshot()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Run failed.'
      reportClientError('run-selection', 'run_failed', message)
    }
  }

  async function confirmRunAll() {
    if (!projectId) {
      return
    }
    const canStartRun = await ensureNoOpenEditorsForRun(plannedNotebookIdsForRunAll())
    if (!canStartRun) {
      return
    }
    try {
      const response = await runAll()
      if (response.status === 'failed') {
        if (!isManagedRunFailure(response)) {
          reportClientError('run-all', 'run_queue_failed', formatRunFailureMessage(liveSnapshot, response, 'Run queue failed.'), { details: response })
        }
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
    const redo = {
      operations: removals.map((change) => ({ type: 'remove_edge', edge_id: change.id } satisfies GraphPatchOperation)),
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
  }

  async function handleUpdateOrganizerPorts(
    node: NodeRecord,
    ports: Array<{ key: string; name: string; data_type: string }>,
  ) {
    const redo = {
      operations: [
        {
          type: 'update_organizer_ports',
          node_id: node.id,
          ports,
        } satisfies GraphPatchOperation,
      ],
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
  }

  async function handleSaveOrganizerEdit(ports: Array<{ key: string; name: string; data_type: string }>) {
    if (!organizerNodeEdit || !liveSnapshot) {
      return
    }
    const node = liveSnapshot.graph.nodes.find((entry) => entry.id === organizerNodeEdit.nodeId)
    if (!node || node.kind !== 'organizer') {
      return
    }
    await handleUpdateOrganizerPorts(node, ports)
    setOrganizerNodeEdit(null)
  }

  async function handleUpdateAreaStyle(
    node: NodeRecord,
    payload: { title: string; titlePosition: string; color: string; filled: boolean },
  ) {
    const redo = {
      operations: [
        { type: 'update_node_title', node_id: node.id, title: payload.title } satisfies GraphPatchOperation,
        {
          type: 'update_area_style',
          node_id: node.id,
          title_position: payload.titlePosition,
          color: payload.color,
          filled: payload.filled,
        } satisfies GraphPatchOperation,
      ],
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
  }

  async function handleSaveAreaEdit(payload: { title: string; titlePosition: string; color: string; filled: boolean }) {
    if (!areaNodeEdit || !liveSnapshot) {
      return
    }
    const node = liveSnapshot.graph.nodes.find((entry) => entry.id === areaNodeEdit.nodeId)
    if (!node || node.kind !== 'area') {
      return
    }
    await handleUpdateAreaStyle(node, payload)
    setAreaNodeEdit(null)
  }

  async function handleConnect(connection: Connection) {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) {
      return
    }
    const sourceGhost = isOrganizerGhostHandle(connection.sourceHandle)
    const targetGhost = isOrganizerGhostHandle(connection.targetHandle)
    if (sourceGhost || targetGhost) {
      if (!liveSnapshot || sourceGhost === targetGhost) {
        return
      }
      const organizerNodeId = sourceGhost ? connection.source : connection.target
      const organizerNode = liveSnapshot.graph.nodes.find((node) => node.id === organizerNodeId)
      if (!organizerNode || organizerNode.kind !== 'organizer') {
        return
      }
      const oppositeNodeId = sourceGhost ? connection.target : connection.source
      const oppositeHandleId = sourceGhost ? connection.targetHandle : connection.sourceHandle
      const oppositeNode = liveSnapshot.graph.nodes.find((node) => node.id === oppositeNodeId)
      if (!oppositeNode) {
        return
      }
      const oppositePortName = oppositeHandleId.replace(/^out:|^in:/, '')
      const oppositePort = sourceGhost
        ? inputsForNode(oppositeNode).find((port) => port.name === oppositePortName)
        : [...outputsForNode(oppositeNode), ...(oppositeNode.interface?.assets ?? [])].find((port) => port.name === oppositePortName)
      if (!oppositePort) {
        return
      }
      const organizerPortKey = nextOrganizerPortKey(
        new Set((organizerNode.ui?.organizer_ports ?? []).map((port) => port.key)),
        oppositePort.name,
      )
      setPendingOrganizerConnection({
        organizerNodeId,
        insertIndex: organizerInsertIndexFromHandle(sourceGhost ? connection.sourceHandle : connection.targetHandle),
        dataType: oppositePort.data_type,
        portKey: organizerPortKey,
        suggestedName: oppositePort.label?.trim() || oppositePort.name,
        sourceNode: sourceGhost ? organizerNodeId : connection.source,
        sourcePort: sourceGhost ? '' : oppositePortName,
        targetNode: targetGhost ? organizerNodeId : connection.target,
        targetPort: targetGhost ? '' : oppositePortName,
      })
      return
    }
    const sourcePort = connection.sourceHandle.replace('out:', '')
    const targetPort = connection.targetHandle.replace('in:', '')
    const redo = {
      operations: [
        {
          type: 'add_edge',
          source_node: connection.source,
          source_port: sourcePort,
          target_node: connection.target,
          target_port: targetPort,
        } satisfies GraphPatchOperation,
      ],
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
  }

  async function handleConfirmCreateOrganizerLane(payload: { name: string }) {
    if (!pendingOrganizerConnection || !liveSnapshot) {
      return
    }
    const pending = pendingOrganizerConnection
    setPendingOrganizerConnection(null)
    const organizerNode = liveSnapshot.graph.nodes.find((node) => node.id === pending.organizerNodeId)
    if (!organizerNode) {
      return
    }
    const currentPorts = (organizerNode.ui?.organizer_ports ?? []).map((port) => ({ ...port }))
    const nextPort = {
      key: pending.portKey,
      name: payload.name,
      data_type: pending.dataType,
    }
    const nextPorts = currentPorts.map((port) => ({ ...port }))
    nextPorts.splice(pending.insertIndex, 0, nextPort)
    const redo = {
      operations: [
        {
          type: 'update_organizer_ports',
          node_id: pending.organizerNodeId,
          ports: nextPorts,
        } satisfies GraphPatchOperation,
        {
          type: 'add_edge',
          source_node: pending.sourceNode,
          source_port: pending.sourcePort || nextPort.key,
          target_node: pending.targetNode,
          target_port: pending.targetPort || nextPort.key,
        } satisfies GraphPatchOperation,
      ],
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
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
      if (isFreezeConflict(message)) {
        reportClientWarning(`upload-frozen:${nodeId}`, 'frozen_block', message, { nodeId })
      } else {
        reportClientError(`upload:${nodeId}`, 'upload_failed', message, { nodeId })
      }
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
    applySelection([], [], { openInspector: false })
    setArtifactNodeId(null)
    setShowProjectInfo(false)
    setGraphHistoryPast([])
    setGraphHistoryFuture([])
    await refreshSnapshot()
  }

  async function handleNodeMove(nodeId: string, x: number, y: number) {
    const redo = {
      operations: [
        {
          type: 'update_node_layout',
          node_id: nodeId,
          x: Math.round(x / 20) * 20,
          y: Math.round(y / 20) * 20,
        } satisfies GraphPatchOperation,
      ],
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
  }

  async function handleNodeResize(nodeId: string, x: number, y: number, w: number, h: number) {
    const redo = {
      operations: [
        {
          type: 'update_node_layout',
          node_id: nodeId,
          x: Math.round(x / 20) * 20,
          y: Math.round(y / 20) * 20,
          w: Math.max(80, Math.round(w / 20) * 20),
          h: Math.max(80, Math.round(h / 20) * 20),
        } satisfies GraphPatchOperation,
      ],
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
  }

  function handleNodesDelete(nodes: Node[]) {
    requestDeleteSelection(nodes.map((node) => node.id), [])
  }

  function openFileNodeEdit(nodeId: string) {
    const node = liveSnapshot?.graph.nodes.find((entry) => entry.id === nodeId)
    if (!node || node.kind !== 'file_input') {
      return
    }
    selectSingleNode(nodeId)
    setFileNodeEdit({
      nodeId,
      title: node.title,
      artifactName: node.ui?.artifact_name ?? 'file',
      frozen: Boolean(node.ui?.frozen),
    })
  }

  function openOrganizerNodeEdit(nodeId: string) {
    const node = liveSnapshot?.graph.nodes.find((entry) => entry.id === nodeId)
    if (!node || node.kind !== 'organizer') {
      return
    }
    selectSingleNode(nodeId)
    setOrganizerNodeEdit({
      nodeId,
      title: node.title,
      ports: (node.ui?.organizer_ports ?? []).map((port) => ({ ...port })),
      frozen: Boolean(node.ui?.frozen),
    })
  }

  function openAreaNodeEdit(nodeId: string) {
    const node = liveSnapshot?.graph.nodes.find((entry) => entry.id === nodeId)
    if (!node || node.kind !== 'area') {
      return
    }
    const settings = areaSettings(node)
    selectSingleNode(nodeId)
    setAreaNodeEdit({
      nodeId,
      title: node.title,
      titlePosition: settings.titlePosition,
      color: settings.color,
      filled: settings.filled,
    })
  }

  function handleDeleteNodeAction(nodeId: string) {
    requestDeleteSelection([nodeId], [], { createCheckpoint: true })
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
    await refreshActiveEditorNodeIds()
    await refreshSnapshot()
  }

  async function handleOpenEditor(nodeId: string) {
    if (isNodeQueuedForExecution(nodeId)) {
      reportEditorBlockedByExecution(nodeId)
      return
    }
    await handleRunNode(nodeId, 'edit_run')
  }

  async function handleKillEditor(nodeId: string) {
    if (!projectId) {
      return
    }
    const sessions = await listSessions()
    const session = sessions.find((item) => item.node_id === nodeId)
    if (!session) {
      await refreshActiveEditorNodeIds()
      return
    }
    await stopSession(session.session_id)
    await refreshActiveEditorNodeIds()
    await refreshSnapshot()
  }

  async function handleCopySelection() {
    const clipboard = await clipboardGraphForSelection(selectedNodeIds)
    if (!clipboard) {
      return
    }
    setClipboardGraph(clipboard)
    setPasteSequence(0)
  }

  async function handlePasteClipboard() {
    if (!clipboardGraph || !liveSnapshot) {
      return
    }
    const existingIds = new Set(liveSnapshot.graph.nodes.map((node) => node.id))
    const nodeIdMap = new Map<string, string>()
    const nextNodeIds: string[] = []
    const offset = 40 * (pasteSequence + 1)
    const nodeOperations: GraphPatchOperation[] = []
    const edgeOperations: GraphPatchOperation[] = []

    for (const item of clipboardGraph.nodes) {
      const nextNodeId = uniqueCopiedNodeId(item.node.id, existingIds)
      existingIds.add(nextNodeId)
      nodeIdMap.set(item.node.id, nextNodeId)
      nextNodeIds.push(nextNodeId)
      const nextLayout: LayoutRecord = {
        ...item.layout,
        node_id: nextNodeId,
        x: snapToGrid(item.layout.x + offset),
        y: snapToGrid(item.layout.y + offset),
      }
      const nextTitle = copiedTitle(item.node.title)
      if (item.node.kind === 'notebook') {
        nodeOperations.push(notebookAddOperationForNode(item.node, nextLayout, item.sourceText, nextNodeId, nextTitle))
      } else if (item.node.kind === 'organizer') {
        nodeOperations.push(organizerAddOperationForNode(item.node, nextLayout, nextNodeId, nextTitle))
      } else if (item.node.kind === 'area') {
        nodeOperations.push(areaAddOperationForNode(item.node, nextLayout, nextNodeId, nextTitle))
      } else {
        nodeOperations.push(fileInputAddOperationForNode(item.node, nextLayout, nextNodeId, nextTitle))
      }
    }

    for (const edge of clipboardGraph.edges) {
      const sourceNode = nodeIdMap.get(edge.source_node)
      const targetNode = nodeIdMap.get(edge.target_node)
      if (!sourceNode || !targetNode) {
        continue
      }
      edgeOperations.push({
        type: 'add_edge',
        source_node: sourceNode,
        source_port: edge.source_port,
        target_node: targetNode,
        target_port: edge.target_port,
      })
    }

    const redo = { operations: nodeOperations, followUpOperations: edgeOperations }
    const history = simpleHistoryEntryForPlan(liveSnapshot, redo)
    const commitPasteSuccess = () => {
      applySelection(nextNodeIds, [], { openInspector: nextNodeIds.length === 1 })
      setPasteSequence((current) => current + 1)
      if (history) {
        setGraphHistoryPast((current) => [...current, history])
        setGraphHistoryFuture([])
      }
    }

    const nodesAdded = await mutateGraph(nodeOperations)
    if (!nodesAdded) {
      return
    }
    const success = edgeOperations.length
      ? await mutateGraph(edgeOperations, { onSuccess: commitPasteSuccess })
      : (() => {
          commitPasteSuccess()
          return Promise.resolve(true)
        })()
    if (!success) {
      if (nextNodeIds.length) {
        await mutateGraph(nextNodeIds.map((nextNodeId) => ({ type: 'delete_node', node_id: nextNodeId })))
      }
      return
    }
  }

  async function handleUndo() {
    const entry = graphHistoryPast[graphHistoryPast.length - 1]
    if (!entry) {
      return
    }
    const success = await mutateGraph(expandMutationPlan(entry.undo))
    if (!success) {
      return
    }
    setGraphHistoryPast((current) => current.slice(0, -1))
    setGraphHistoryFuture((current) => [entry, ...current])
    applySelection([], [], { openInspector: false })
  }

  async function handleRedo() {
    const entry = graphHistoryFuture[0]
    if (!entry) {
      return
    }
    const success = await mutateGraph(expandMutationPlan(entry.redo))
    if (!success) {
      return
    }
    setGraphHistoryFuture((current) => current.slice(1))
    setGraphHistoryPast((current) => [...current, entry])
    applySelection([], [], { openInspector: false })
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (isEditableTarget(event.target)) {
        return
      }
      const key = event.key.toLowerCase()
      if ((key === 'backspace' || key === 'delete') && (selectedNodeIds.length > 0 || selectedEdgeIds.length > 0)) {
        event.preventDefault()
        requestDeleteSelection(selectedNodeIds, selectedEdgeIds)
        return
      }
      const primaryModifier = event.metaKey || event.ctrlKey
      if (!primaryModifier) {
        return
      }
      if (key === 'c' && selectedNodeIds.length > 0) {
        event.preventDefault()
        void handleCopySelection()
        return
      }
      if (key === 'v' && clipboardGraph) {
        event.preventDefault()
        void handlePasteClipboard()
        return
      }
      if (key === 'z' && !event.shiftKey) {
        event.preventDefault()
        void handleUndo()
        return
      }
      if ((key === 'z' && event.shiftKey) || key === 'y') {
        event.preventDefault()
        void handleRedo()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [clipboardGraph, graphHistoryFuture, graphHistoryPast, liveSnapshot, projectId, selectedEdgeIds, selectedNodeIds])

  const counts = liveSnapshot ? globalArtifactCounts(liveSnapshot) : { ready: 0, stale: 0, pending: 0 }
  const activeRun = liveSnapshot ? currentRun(liveSnapshot) : null
  const runningNodeId = liveSnapshot ? activeRunNodeId(liveSnapshot, activeRun) : null
  const queuedNodeIds = liveSnapshot ? queuedRunNodeIds(liveSnapshot, activeRun) : []
  const completedNodeIds = liveSnapshot
    ? liveSnapshot.graph.nodes
      .filter((node) => node.orchestrator_state?.status === 'succeeded')
      .map((node) => node.id)
    : []
  const serverNowMs = serverClock.serverNowMs
  const clientNowAnchorMs = serverClock.clientAnchorMs

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
      <div className="floating-actions floating-panel">
        <div className="topbar-actions">
          <button className="secondary small" onClick={() => void handleUndo()} disabled={!graphHistoryPast.length}>Undo</button>
          <button className="secondary small" onClick={() => void handleRedo()} disabled={!graphHistoryFuture.length}>Redo</button>
          <button className="secondary icon-pill" onClick={() => setShowSettings(true)} aria-label="Editor settings"><Palette width={18} height={18} /></button>
          <button className="secondary icon-pill" onClick={() => setShowProjectInfo(true)} disabled={!projectId} aria-label="Project info"><Info width={18} height={18} /></button>
          {activeRun ? (
            <button className="danger" onClick={handleCancelRun}>Stop run</button>
          ) : (
            <button className="play-action" onClick={handleRunAll} disabled={!projectId} aria-label="Run pipeline" title="Run pipeline"><Play width={22} height={22} /></button>
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
              <label className="toggle-row toggle-switch-row">
                <input
                  type="checkbox"
                  checked={showHiddenTemplates}
                  onChange={(event) => setShowHiddenTemplates(event.target.checked)}
                />
                <span className="toggle-switch" aria-hidden="true"><span /></span>
                <span>Show hidden templates</span>
              </label>
              <BlockPalette
                entries={paletteEntries}
                groupTemplatesByProvider={groupTemplatesByProvider}
                searchActive={paletteSearch.trim().length > 0}
                onCreate={handleCreateFromPalette}
                onInspectEntry={(entry) => setPaletteInfoEntry(entry)}
                onDragStart={handlePaletteDragStart}
                onDragEnd={handlePaletteDragEnd}
                previewScale={paletteViewport?.zoom ?? 1}
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
              selectedNodeIds={selectedNodeIds}
              selectedEdgeIds={selectedEdgeIds}
              activeRunNodeId={runningNodeId}
              queuedRunNodeIds={queuedNodeIds}
              completedRunNodeIds={completedNodeIds}
              onConnect={handleConnect}
              onEdgesChange={handleEdgeChanges}
                onSelectionChange={(nodeIds, edgeIds, options) => {
                  const pending = pendingClickSelectionRef.current
                  if (pending) {
                    if (selectionMatches(nodeIds, pending.nodeIds) && selectionMatches(edgeIds, pending.edgeIds)) {
                      pendingClickSelectionRef.current = null
                      applySelection(nodeIds, edgeIds, { openInspector: options?.additive ? false : undefined })
                      return
                    }
                    return
                  }
                  applySelection(nodeIds, edgeIds, { openInspector: options?.additive ? false : undefined })
                }}
                onNodeSelect={handleNodeSelection}
                onEdgeSelect={handleEdgeSelection}
                onNodeContextMenu={(nodeId, position) => {
                  const menuNodeIds = selectedNodeIds.includes(nodeId) && selectedNodeIds.length + selectedEdgeIds.length > 1
                    ? selectedNodeIds
                    : [nodeId]
                  openSelectedNodeActionMenu(position, menuNodeIds)
                }}
                onSelectionContextMenu={(position) => {
                  openSelectedNodeActionMenu(position)
                }}
              onPortContextMenu={(nodeId, portName, side, position) => {
                  const clamped = clampContextMenuPosition(position)
                  setNodeActionMenu(null)
                  setPortActionMenu({ nodeId, portName, side, x: clamped.x, y: clamped.y })
                }}
              onEditFileNode={openFileNodeEdit}
              onEditOrganizerNode={openOrganizerNodeEdit}
              onEditAreaNode={openAreaNodeEdit}
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
                  applySelection([], [], { openInspector: false })
                  setNodeActionMenu(null)
                  setPortActionMenu(null)
                }}
                onNodeMove={handleNodeMove}
                onNodeResize={handleNodeResize}
                onNodesDelete={handleNodesDelete}
                draggedBlock={draggedPaletteEntry ? { title: draggedPaletteEntry.title, kind: draggedPaletteEntry.kind } : null}
                onBlockDrop={handleBlockDrop}
                onViewportChange={handlePaletteViewportChange}
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
                applySelection([], [], { openInspector: false })
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
                nodeActions={nodeActionsForNode(selectedNode)}
                onUploadFile={handleUploadFile}
                onOpenTemplate={openTemplateInfo}
              />
            ) : null}
          </div>
        </aside>
      </section>

      {nodeActionMenu && nodeActionMenuNode.length > 0 ? (
        <div
          ref={nodeActionMenuRef}
          className="node-action-menu"
          style={{ left: nodeActionMenu.x, top: nodeActionMenu.y }}
          onClick={(event) => event.stopPropagation()}
        >
          {nodeActionMenu.grouped ? (
            <div className="context-menu-label">
              {nodeActionMenuNode.length} selected block{nodeActionMenuNode.length === 1 ? '' : 's'}
            </div>
          ) : nodeActionMenuNode.length > 1 ? (
            <div className="context-menu-label">
              {nodeActionMenuNode.length} selected blocks
            </div>
          ) : primaryNodeActionMenuNode ? (
            <div className="context-menu-label">
              block: {primaryNodeActionMenuNode.id}
            </div>
          ) : null}
          <ActionButtons
            actions={nodeActionsForMenu(nodeActionMenuNode.map((node) => node.id), { dismissMenu: () => setNodeActionMenu(null) })}
            itemClassName="secondary menu-item"
          />
        </div>
      ) : null}

      {portActionMenu && portActionMenuNode ? (
        <div ref={portActionMenuRef} className="node-action-menu" style={{ left: portActionMenu.x, top: portActionMenu.y }} onClick={(event) => event.stopPropagation()}>
          <div className="context-menu-label">
            {portActionMenu.side} port: {portActionMenuNode.id}/{portActionMenu.portName}
            <br />
            {portActionArtifact
              ? `artifact: ${portActionArtifact.nodeId}/${portActionArtifact.artifactName}`
              : 'artifact: not connected'}
          </div>
          <button
            className="secondary menu-item"
            disabled={!portActionArtifact || !portActionHead || portActionHead.current_version_id === null || portActionHead.state === 'stale' || Boolean(portActionMutationBlockedReason)}
            title={portActionMutationBlockedReason}
            onClick={() => {
              if (!portActionArtifact) {
                return
              }
              setPortActionMenu(null)
              void handleSetArtifactStateAction(portActionArtifact.nodeId, portActionArtifact.artifactName, 'stale')
            }}
          >
            Mark stale
          </button>
          <button
            className="secondary menu-item"
            disabled={
              !portActionArtifact
              || !portActionHead
              || portActionHead.current_version_id === null
              || portActionHead.state === 'ready'
              || !nodeInputsAreReady(portActionMenuNode)
              || Boolean(portActionMutationBlockedReason)
            }
            title={portActionMutationBlockedReason}
            onClick={() => {
              if (!portActionArtifact) {
                return
              }
              setPortActionMenu(null)
              setConfirmationState({
                kind: 'artifact-state',
                nodeId: portActionArtifact.nodeId,
                artifactName: portActionArtifact.artifactName,
                state: 'ready',
                title: 'Mark output ready?',
                message: `This bypasses consistency checks for ${portActionArtifact.nodeId}/${portActionArtifact.artifactName}.`,
              })
            }}
          >
            Mark ready
          </button>
          <button
            className="secondary menu-item"
            disabled={!portActionEdgeIds.length || Boolean(portDisconnectBlockedReason)}
            title={portDisconnectBlockedReason}
            onClick={() => void handleDisconnectPort(portActionMenu)}
          >
            Disconnect all
          </button>
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

      {paletteInfoEntry ? (
        <Modal title={paletteInfoTemplate?.title ?? paletteInfoEntry.title} onClose={() => setPaletteInfoEntry(null)} contentClassName="template-info-dialog-card">
          <div className="template-info-stack">
            {paletteInfoEntry.kind === 'pipeline' ? (
              <>
                {paletteInfoTemplate?.documentation ? (
                  <SimpleMarkdown text={paletteInfoTemplate.documentation} />
                ) : (
                  <p className="template-info-empty"><em>No documentation available.</em></p>
                )}
                <section className="template-reference-section">
                  <h4>Referenced templates</h4>
                  {paletteInfoPipelineReferences.length ? (
                    <ul className="template-reference-bullets">
                      {paletteInfoPipelineReferences.map((reference) => (
                        <li key={reference.ref}>
                          {reference.title} (<code>{reference.ref}</code>)
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="template-info-empty"><em>No referenced templates.</em></p>
                  )}
                </section>
              </>
            ) : paletteInfoEntry.kind === 'template' ? (
              paletteInfoTemplate?.documentation ? (
                <SimpleMarkdown text={paletteInfoTemplate.documentation} />
              ) : (
                <p className="template-info-empty"><em>No documentation available.</em></p>
              )
            ) : (
              <p className="template-info-copy">{paletteInfoEntry.description}</p>
            )}
          </div>
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

      {pendingOrganizerConnection ? (
        <CreateOrganizerPortDialog
          suggestedName={pendingOrganizerConnection.suggestedName}
          onClose={() => setPendingOrganizerConnection(null)}
          onCreate={handleConfirmCreateOrganizerLane}
        />
      ) : null}

      {pendingAreaCreation ? (
        <EditAreaDialog
          title="Create Area"
          initialTitle=""
          initialTitlePosition="top-left"
          initialColor="blue"
          initialFilled={true}
          submitLabel="Create area"
          allowUnchangedSubmit
          onClose={() => setPendingAreaCreation(null)}
          onSave={handleConfirmCreateArea}
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
          uploadDisabledMessage={fileNodeEdit.frozen ? 'This block is frozen. Unfreeze it before replacing the file.' : null}
          onClose={() => setFileNodeEdit(null)}
          onCreate={async (payload) => {
            setFileNodeEdit(null)
            if (!projectId) {
              return
            }
            const redo = {
              operations: [
                { type: 'update_node_title', node_id: fileNodeEdit.nodeId, title: payload.title } satisfies GraphPatchOperation,
              ],
            }
            await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
            if (payload.file) {
              await handleUploadFile(fileNodeEdit.nodeId, payload.file)
            }
          }}
        />
      ) : null}

      {organizerNodeEdit ? (
        <EditOrganizerDialog
          title={`Edit ${organizerNodeEdit.title}`}
          initialPorts={organizerNodeEdit.ports}
          saveDisabledMessage={organizerNodeEdit.frozen ? 'This block is frozen. Unfreeze it before editing lanes.' : null}
          onClose={() => setOrganizerNodeEdit(null)}
          onSave={handleSaveOrganizerEdit}
        />
      ) : null}

      {areaNodeEdit ? (
        <EditAreaDialog
          title={`Edit ${areaNodeEdit.title || 'Area'}`}
          initialTitle={areaNodeEdit.title}
          initialTitlePosition={areaNodeEdit.titlePosition}
          initialColor={areaNodeEdit.color}
          initialFilled={areaNodeEdit.filled}
          onClose={() => setAreaNodeEdit(null)}
          onSave={handleSaveAreaEdit}
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
          title="Refresh ancestor blocks?"
          message={confirmationState.message}
          confirmLabel="Refresh ancestors"
          confirmTone="success"
          alternateLabel="Run on stale inputs"
          alternateTone={confirmationState.useStaleDisabled ? 'default' : 'warning'}
          alternateDisabled={confirmationState.useStaleDisabled}
          alternateHelpText={confirmationState.useStaleDisabledReason}
          cancelLabel="Cancel"
          cancelTone="default"
          onClose={() => setConfirmationState(null)}
          onAlternate={() => {
            const pending = confirmationState
            setConfirmationState(null)
            if (pending.nodeIds?.length) {
              void confirmRunSelectionWithAction(pending.nodeIds, 'use_stale')
              return
            }
            if (pending.nodeId) {
              void confirmRunNodeWithAction(pending.nodeId, pending.mode, pending.scope, 'use_stale')
            }
          }}
          onConfirm={() => {
            const pending = confirmationState
            setConfirmationState(null)
            if (pending.nodeIds?.length) {
              void confirmRunSelectionWithAction(pending.nodeIds, 'run_upstream')
              return
            }
            if (pending.nodeId) {
              void confirmRunNodeWithAction(pending.nodeId, pending.mode, pending.scope, 'run_upstream')
            }
          }}
        />
      ) : null}

      {confirmationState?.kind === 'delete-nodes' ? (
        <ConfirmDialog
          title={confirmationState.title}
          message={confirmationState.message}
          confirmLabel="Delete"
          tone="danger"
          onClose={() => setConfirmationState(null)}
          onConfirm={() => {
            const pending = confirmationState
            setConfirmationState(null)
            void (async () => {
              if (pending.createCheckpoint) {
                await createCheckpoint()
              }
              await handleDeleteSelection(pending.nodeIds, pending.edgeIds)
            })()
          }}
        />
      ) : null}

      {confirmationState?.kind === 'node-outputs-state' ? (
        <ConfirmDialog
          title={confirmationState.title}
          message={confirmationState.message}
          confirmLabel={confirmationState.state === 'ready' ? 'Mark ready' : 'Confirm'}
          tone={confirmationState.state === 'ready' ? 'danger' : 'default'}
          onClose={() => setConfirmationState(null)}
          onConfirm={() => {
            const pending = confirmationState
            setConfirmationState(null)
            void handleSetNodeOutputsStateForNodes(
              pending.nodeIds,
              pending.state,
              pending.onlyCurrentState,
            )
          }}
        />
      ) : null}

      {confirmationState?.kind === 'artifact-state' ? (
        <ConfirmDialog
          title={confirmationState.title}
          message={confirmationState.message}
          confirmLabel={confirmationState.state === 'ready' ? 'Mark ready' : 'Confirm'}
          tone={confirmationState.state === 'ready' ? 'danger' : 'default'}
          onClose={() => setConfirmationState(null)}
          onConfirm={() => {
            const pending = confirmationState
            setConfirmationState(null)
            void handleSetArtifactStateAction(
              pending.nodeId,
              pending.artifactName,
              pending.state,
            )
          }}
        />
      ) : null}

      {confirmationState?.kind === 'node-frozen' ? (
        <ConfirmDialog
          title={confirmationState.title}
          message={confirmationState.message}
          confirmLabel={confirmationState.frozen ? 'Freeze' : 'Unfreeze'}
          onClose={() => setConfirmationState(null)}
          onConfirm={() => {
            const pending = confirmationState
            setConfirmationState(null)
            void handleSetNodesFrozen(pending.nodeIds, pending.frozen)
          }}
        />
      ) : null}

      <NoticeOverlay
        notices={overlayNotices}
        onDismiss={(notice) => void handleDismissNotice(notice)}
        onOpenNode={(nodeId) => selectSingleNode(nodeId)}
        onOpenEditor={(notice) => void handleOpenEditorNotice(notice)}
        onKillEditor={(notice) => void handleKillEditorNotice(notice)}
      />
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

export default App
