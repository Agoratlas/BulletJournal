import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Connection, EdgeChange, Node } from 'reactflow'

import { appUrl, cancelRun, createCheckpoint, currentProject, dismissNotice, downloadNotebookSource, getSnapshot, listSessions, notebookDownloadUrl, patchGraph, restoreCheckpoint, runAll, runNode, setArtifactState, setNodeOutputsState, stopSession, uploadFile } from './lib/api'
import { activeRunNodeId, artifactFor, artifactsForDisplay, currentRun, formatTimestamp, globalArtifactCounts, hiddenInputNames, inputState, queuedRunNodeIds, templateByRef } from './lib/helpers'
import type { ArtifactRecord, GraphPatchOperation, LayoutRecord, NodeRecord, ProjectSnapshot, TemplateRecord } from './lib/types'
import type { AppNotice, ClipboardGraph, ClipboardNodeRecord, ConstantValueType, GraphHistoryEntry, GraphMutationPlan, NodeActionItem, OptimisticGraphState, PaletteEntry, PortActionMenuState } from './appTypes'
import { applyOptimisticGraphOperations, artifactTargetForPort, blockCreateMode, buildConstantValueNotebookSource, clampContextMenuPosition, cloneSnapshot, copiedTitle, createClientNotice, edgeIdForPorts, edgeIdsForPort, editorSessionDetails, expandMutationPlan, fileInputAddOperationForNode, freezeBlockMessage, frozenBlockBlockersForDelete, frozenBlockBlockersForRemovedEdges, frozenBlockBlockersForStaleRoots, isEditableTarget, isEditorOpenConflict, isFreezeConflict, isManagedRunFailure, mergeGraphIntoSnapshot, normalizeNodeId, notebookAddOperationForNode, pipelineDefinitionNodeIds, pipelineTemplateNodeRecords, pipelineTopLeftForCenter, runFailureMessage, SNAPSHOT_REFRESH_EVENTS, SNAPSHOT_REFRESH_THROTTLE_MS, snapToGrid, uniqueCopiedNodeId } from './lib/appHelpers'
import { ArtifactCard } from './components/ArtifactCard'
import { ArtifactCounts } from './components/ArtifactCounts'
import { BlockPalette } from './components/BlockPalette'
import { ActionButtons } from './components/ActionButtons'
import { ConfirmDialog, CreateConstantValueDialog, CreateFileDialog, CreateNotebookDialog, CreatePipelineDialog, Modal } from './components/Dialogs'
import { GraphCanvas } from './components/GraphCanvas'
import { Info, Palette, Play, Plus } from './components/Icons'
import { NodeInspector } from './components/NodeInspector'
import { NoticeOverlay } from './components/NoticeOverlay'
import { SessionLoadingScreen } from './components/SessionLoadingScreen'

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

const NEW_NODE_WIDTH = 360
const NEW_NODE_HEIGHT = 220

type ArtifactMutationState = 'ready' | 'stale'

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
  const [templateRefView, setTemplateRefView] = useState<string | null>(null)
  const [showProjectInfo, setShowProjectInfo] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [templatesCollapsed, setTemplatesCollapsed] = useState(true)
  const [showHiddenTemplates, setShowHiddenTemplates] = useState(false)
  const [paletteSearch, setPaletteSearch] = useState('')
  const [draggedPaletteEntry, setDraggedPaletteEntry] = useState<PaletteEntry | null>(null)
  const [pendingBlockCreation, setPendingBlockCreation] = useState<PendingBlockCreation | null>(null)
  const [pendingPipelineCreation, setPendingPipelineCreation] = useState<PendingPipelineCreation | null>(null)
  const [fileNodeEdit, setFileNodeEdit] = useState<FileNodeEditState | null>(null)
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

  function applySelection(nodeIds: string[], edgeIds: string[], options: { openInspector?: boolean } = {}) {
    setSelectedNodeIds(nodeIds)
    setSelectedEdgeIds(edgeIds)
    const singleNodeId = nodeIds.length === 1 && edgeIds.length === 0 ? nodeIds[0] : null
    setSelectedNodeId(singleNodeId)
    if (options.openInspector !== undefined) {
      setInspectorOpen(options.openInspector && Boolean(singleNodeId))
      return
    }
    setInspectorOpen(Boolean(singleNodeId))
  }

  function selectSingleNode(nodeId: string | null, options: { openInspector?: boolean } = {}) {
    applySelection(nodeId ? [nodeId] : [], [], { openInspector: options.openInspector ?? Boolean(nodeId) })
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

  async function handleDeleteNodesAction(nodeIds: string[]) {
    if (!projectId || !liveSnapshot || !nodeIds.length) {
      return
    }
    const nodes = nodeIds
      .map((nodeId) => liveSnapshot.graph.nodes.find((entry) => entry.id === nodeId) ?? null)
      .filter((node): node is NodeRecord => node !== null)
    if (!nodes.length) {
      return
    }
    const label = nodes.length === 1
      ? `block "${nodes[0].title}"`
      : `${nodes.length} selected blocks`
    if (!window.confirm(`Create a checkpoint and delete ${label}?`)) {
      return
    }
    await createCheckpoint()
    await handleNodesDelete(nodes.map((node) => ({ id: node.id } as Node)))
    await refreshSnapshot()
    setNodeActionMenu(null)
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

    if (node.kind === 'notebook') {
      actions.push({
        key: 'download-notebook',
        label: 'Download notebook',
        href: notebookDownloadUrl(node.id),
        onClick: () => dismissMenu(),
      })
    }

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

    actions.push({
      key: 'toggle-frozen',
      label: node.ui?.frozen ? 'Unfreeze block' : 'Freeze block',
      onClick: () => {
        dismissMenu()
        requestSetNodesFrozen([node.id], !node.ui?.frozen)
      },
    })

    actions.push({
      key: 'delete-node',
      label: 'Delete block',
      tone: 'danger',
      disabled: Boolean(deleteBlockedReason),
      title: deleteBlockedReason,
      onClick: () => {
        dismissMenu()
        void handleDeleteNodeAction(node.id)
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

    const freezableNodes = menuNodes
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
          void handleDeleteNodesAction(menuNodes.map((node) => node.id))
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
      .filter(
        (template) => template.kind === 'notebook'
          && template.ref !== 'builtin/value_input'
          && (showHiddenTemplates || !template.hidden),
      )
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
  }, [liveSnapshot, paletteSearch, showHiddenTemplates])

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
        case 'update_node_hidden_inputs': {
          const node = snapshotData.graph.nodes.find((entry) => entry.id === operation.node_id)
          if (!node) {
            return null
          }
          undoOperations.push({
            type: 'update_node_hidden_inputs',
            node_id: operation.node_id,
            hidden_inputs: [...(node.ui?.hidden_inputs ?? [])],
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

  async function mutateGraph(
    operations: GraphPatchOperation[],
    options: { history?: GraphHistoryEntry | null; onSuccess?: () => void } = {},
  ): Promise<boolean> {
    if (!liveSnapshot || !projectId) {
      return false
    }
    const rollbackSnapshot = liveSnapshot
    try {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: ['snapshot'], exact: true }),
        queryClient.cancelQueries({ queryKey: ['project-current'], exact: true }),
      ])
      const optimistic = applyOptimisticGraphOperations(liveSnapshot, operations as Array<Record<string, unknown>>)
      if (optimistic) {
        setOptimisticGraph(optimistic)
      }
      const response = await patchGraph(liveSnapshot.graph.meta.graph_version, operations)
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
    payload: { type: 'empty' | 'template' | 'file_input'; nodeId: string; title: string; templateRef?: string; sourceText?: string; origin?: 'constant_value' | null },
    placement?: { x: number; y: number },
  ) {
    const baseX = 120 + ((liveSnapshot?.graph.nodes.length ?? 0) % 4) * 420
    const baseY = 120 + Math.floor((liveSnapshot?.graph.nodes.length ?? 0) / 4) * 280
    const x = snapToGrid((placement?.x ?? baseX) - NEW_NODE_WIDTH / 2)
    const y = snapToGrid((placement?.y ?? baseY) - NEW_NODE_HEIGHT / 2)
    if (payload.type === 'file_input') {
      const redo = {
        operations: [
          { type: 'add_file_input_node', node_id: payload.nodeId, title: payload.title, x, y, w: NEW_NODE_WIDTH, h: NEW_NODE_HEIGHT } satisfies GraphPatchOperation,
        ],
      }
      await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
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
      await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
      return
    }
    const redo = {
      operations: [
        { type: 'add_notebook_node', node_id: payload.nodeId, title: payload.title, x, y, w: NEW_NODE_WIDTH, h: NEW_NODE_HEIGHT } satisfies GraphPatchOperation,
      ],
    }
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
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
    await mutateGraph(redo.operations, { history })
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
      await handleCreateNode(
        {
          type: 'template',
          nodeId: payload.nodeId,
          title: payload.title,
          templateRef: 'builtin/empty_notebook',
        },
        { x, y },
      )
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
    await mutateGraph(redo.operations, { history: liveSnapshot ? simpleHistoryEntryForPlan(liveSnapshot, redo) : null })
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
        if (!isManagedRunFailure(response)) {
          reportClientError(`run:${nodeId}:${mode}`, 'run_failed', runFailureMessage(response, 'Run failed.'), { nodeId, details: response })
        }
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
      if (isFreezeConflict(message)) {
        reportClientWarning(`run-frozen:${nodeId}:${mode}`, 'frozen_block', message, { nodeId })
      } else {
        reportClientError(`run:${nodeId}:${mode}`, 'run_failed', message, { nodeId })
      }
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
        if (!isManagedRunFailure(response)) {
          reportClientError(`run:${nodeId}:${mode}`, 'run_failed', runFailureMessage(response, 'Run failed.'), { nodeId, details: response })
        }
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
        if (!isManagedRunFailure(response)) {
          reportClientError('run-all', 'run_queue_failed', runFailureMessage(response, 'Run queue failed.'), { details: response })
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

  async function handleConnect(connection: Connection) {
    if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) {
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

  async function handleToggleHiddenInput(node: NodeRecord, inputName: string) {
    const currentHidden = hiddenInputNames(node)
    if (currentHidden.has(inputName)) {
      currentHidden.delete(inputName)
    } else {
      currentHidden.add(inputName)
    }
    const redo = {
      operations: [
        {
          type: 'update_node_hidden_inputs',
          node_id: node.id,
          hidden_inputs: Array.from(currentHidden),
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

  async function handleNodesDelete(nodes: Node[]) {
    if (!nodes.length) {
      return
    }
    const nodeIds = nodes.map((node) => node.id)
    const history = await deleteHistoryEntry(nodeIds)
    await stopEditorsForNodes(nodes.map((node) => node.id))
    const success = await mutateGraph(nodeIds.map((nodeId) => ({ type: 'delete_node', node_id: nodeId })), { history })
    if (!success) {
      return
    }
    setSelectedNodeId((current) => (current && nodes.some((node) => node.id === current) ? null : current))
    setSelectedNodeIds((current) => current.filter((nodeId) => !nodeIds.includes(nodeId)))
    setSelectedEdgeIds([])
    setInspectorOpen(false)
    setArtifactNodeId((current) => (current && nodes.some((node) => node.id === current) ? null : current))
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
    const operations: GraphPatchOperation[] = []

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
        operations.push(notebookAddOperationForNode(item.node, nextLayout, item.sourceText, nextNodeId, nextTitle))
      } else {
        operations.push(fileInputAddOperationForNode(item.node, nextLayout, nextNodeId, nextTitle))
      }
    }

    for (const edge of clipboardGraph.edges) {
      const sourceNode = nodeIdMap.get(edge.source_node)
      const targetNode = nodeIdMap.get(edge.target_node)
      if (!sourceNode || !targetNode) {
        continue
      }
      operations.push({
        type: 'add_edge',
        source_node: sourceNode,
        source_port: edge.source_port,
        target_node: targetNode,
        target_port: edge.target_port,
      })
    }

    const redo = { operations }
    const success = await mutateGraph(redo.operations, {
      history: simpleHistoryEntryForPlan(liveSnapshot, redo),
      onSuccess: () => {
        applySelection(nextNodeIds, [], { openInspector: nextNodeIds.length === 1 })
        setPasteSequence((current) => current + 1)
      },
    })
    if (!success) {
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
      const primaryModifier = event.metaKey || event.ctrlKey
      if (!primaryModifier) {
        return
      }
      const key = event.key.toLowerCase()
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
  }, [clipboardGraph, graphHistoryFuture, graphHistoryPast, selectedNodeIds])

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
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={showHiddenTemplates}
                  onChange={(event) => setShowHiddenTemplates(event.target.checked)}
                />
                <span>Show hidden templates</span>
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
                onToggleHiddenInput={handleToggleHiddenInput}
                onUploadFile={handleUploadFile}
                onOpenTemplate={setTemplateRefView}
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
