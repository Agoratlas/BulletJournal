import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import ReactFlow, {
  Background,
  ConnectionMode,
  Panel,
  Handle,
  MarkerType,
  NodeResizeControl,
  Position,
  ResizeControlVariant,
  SelectionMode,
  type NodeChange,
  useStore,
  useStoreApi,
  useUpdateNodeInternals,
  type Connection,
  type Edge,
  type EdgeChange,
  getViewportForBounds,
  type Node,
  type NodeDragHandler,
  type OnConnectStartParams,
  type NodeProps,
  type Viewport,
  useReactFlow,
} from 'reactflow'

import { areaSettings } from '../lib/area'
import { CONSTANT_NODE_PORT_CENTER_OFFSET, CONSTANT_NODE_WIDTH, ORGANIZER_NODE_PORT_CENTER_OFFSET, PORT_ROW_HEIGHT, STANDARD_NODE_PORT_CENTER_OFFSET, artifactCounts, artifactFor, artifactIsEmpty, badgeForNode, formatDurationSeconds, inputBindingSource, inputState, inputsForNode, outputsForNode } from '../lib/helpers'
import type { ArtifactState, NodeRecord, Port, ProjectSnapshot } from '../lib/types'
import { ArtifactCounts } from './ArtifactCounts'
import { Eye, Pencil, Play } from './Icons'
import { PortLabel, TYPE_COLORS, displayPortName } from './PortLabel'

type GraphCanvasProps = {
  snapshot: ProjectSnapshot
  serverNowMs?: number
  serverNowClientAnchorMs?: number
  selectedNodeIds: string[]
  selectedEdgeIds: string[]
  activeRunNodeId?: string | null
  queuedRunNodeIds?: string[]
  completedRunNodeIds?: string[]
  activeEditorNodeIds?: string[]
  onConnect: (connection: Connection) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  onSelectionChange: (nodeIds: string[], edgeIds: string[], options?: { additive?: boolean }) => void
  onNodeSelect: (nodeId: string, options?: { additive?: boolean }) => void
  onEdgeSelect: (edgeId: string, options?: { additive?: boolean }) => void
  onNodeContextMenu: (nodeId: string, position: { x: number; y: number }) => void
  onSelectionContextMenu: (position: { x: number; y: number }) => void
  onPortContextMenu: (nodeId: string, portName: string, side: 'input' | 'output', position: { x: number; y: number }) => void
  onEditConstantNode: (nodeId: string) => void
  onEditFileNode: (nodeId: string) => void
  onEditOrganizerNode: (nodeId: string) => void
  onEditAreaNode: (nodeId: string) => void
  onOpenEditor: (nodeId: string) => void
  onOpenDashboard: (nodeId: string, options?: { newTab?: boolean }) => void
  onKillEditor: (nodeId: string) => void
  onRunNode: (nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run', scope?: 'node' | 'ancestors' | 'descendants') => void
  onOpenArtifacts: (nodeId: string) => void
  onCanvasInteract: () => void
  onCanvasClear: () => void
  onNodeMove: (nodeId: string, x: number, y: number) => void
  onNodeResize: (nodeId: string, x: number, y: number, w: number, h: number) => void
  onNodesDelete: (nodes: Node[]) => void
  draggedBlock: { title: string; kind: string } | null
  onBlockDrop: (x: number, y: number) => void
  onViewportChange: (viewport: { center: { x: number; y: number }; zoom: number }) => void
  dashboardPseudoLinks?: Array<{ sourceNodeId: string; dashboardNodeId: string }>
  selectedDashboardId?: string | null
  selectedDashboardSourceNodeIds?: string[]
  onToggleDashboardSource?: (nodeId: string) => void
  nodeNoticeSeverityById?: Record<string, 'error' | 'warning'>
  hoveredNoticeNodeId?: string | null
  focusedNotice?: { nodeId: string; token: number } | null
}

const NON_RUNNABLE_NODE_KINDS = new Set(['constant', 'file_input', 'organizer', 'area', 'dashboard'])
const GRAPH_MIN_ZOOM = 0.18
const GRAPH_MAX_ZOOM = 1.35
const GRAPH_DEFAULT_ZOOM = 0.78
const GRAPH_FIT_PADDING = 0.12
const CONNECTION_DRAG_ACTIVATION_DISTANCE = 4

function validationIssuesForNode(snapshot: ProjectSnapshot, nodeId: string) {
  return snapshot.validation_issues.filter((issue) => issue.node_id === nodeId)
}

type BulletJournalNodeData = {
  node: NodeRecord
  snapshot: ProjectSnapshot
  serverNowMs: number
  serverNowClientAnchorMs: number
  activeRunNodeId: string | null
  queuedRunNodeIds: string[]
  completedRunNodeIds: string[]
  onSelect: (nodeId: string, options?: { additive?: boolean }) => void
  onNodeContextMenu: (nodeId: string, position: { x: number; y: number }) => void
  onPortContextMenu: (nodeId: string, portName: string, side: 'input' | 'output', position: { x: number; y: number }) => void
  onEditConstantNode: (nodeId: string) => void
  onEditFileNode: (nodeId: string) => void
  onEditOrganizerNode: (nodeId: string) => void
  onEditAreaNode: (nodeId: string) => void
  onOpenEditor: (nodeId: string) => void
  onOpenDashboard: (nodeId: string, options?: { newTab?: boolean }) => void
  onKillEditor: (nodeId: string) => void
  onRunNode: (nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run', scope?: 'node' | 'ancestors' | 'descendants') => void
  onOpenArtifacts: (nodeId: string) => void
  activeEditorNodeIds: string[]
  selectedDashboardId: string | null
  selectedDashboardSourceNodeIds: string[]
  selectedDashboardEdgeNotebookIds: string[]
  selectedDashboardEdgeDashboardIds: string[]
  onToggleDashboardSource: (nodeId: string) => void
  organizerGhostInsertIndex: number | null
  connectionIntent: ConnectionIntent
  onNodeResizePreview: (nodeId: string, x: number, y: number, w: number, h: number) => void
  onNodeResize: (nodeId: string, x: number, y: number, w: number, h: number) => void
  activeNoticeSeverity: 'error' | 'warning' | null
  hoveredNotice: boolean
}

type ConnectionIntent = {
  nodeId: string
  handleId: string
  handleType: 'source' | 'target'
} | null


function isOrganizerGhostHandle(handleId: string | null | undefined): boolean {
  return Boolean(handleId && (handleId.startsWith('ghost-in:') || handleId.startsWith('ghost-out:')))
}

type FlowConnectionState = {
  connectionNodeId: string | null
  connectionHandleId: string | null
  connectionHandleType: 'source' | 'target' | null
}

type FlowSelectionState = {
  userSelectionRect: {
    x: number
    y: number
    width: number
    height: number
  } | null
  transform: [number, number, number]
}

function useConnectionIntent(): ConnectionIntent {
  const connectionNodeId = useStore((state: FlowConnectionState) => state.connectionNodeId)
  const connectionHandleId = useStore((state: FlowConnectionState) => state.connectionHandleId)
  const connectionHandleType = useStore((state: FlowConnectionState) => state.connectionHandleType)

  return useMemo(() => {
    if (!connectionNodeId || !connectionHandleId || !connectionHandleType) {
      return null
    }
    return {
      nodeId: connectionNodeId,
      handleId: connectionHandleId,
      handleType: connectionHandleType,
    } satisfies NonNullable<ConnectionIntent>
  }, [connectionHandleId, connectionHandleType, connectionNodeId])
}

const STATE_COLORS: Record<ArtifactState | 'mixed', string> = {
  ready: '#34b85a',
  stale: '#c97c00',
  pending: '#98a2a3',
  mixed: '#2563eb',
}
const EMPTY_OR_DEFAULT_COLOR = '#ffffff'
const MISSING_REQUIRED_INPUT_COLOR = '#d64545'

function handleBorderColor(fillColor: string): string {
  return `color-mix(in srgb, ${fillColor} 72%, rgba(15, 23, 42, 0.58) 28%)`
}

function pointInRect(x: number, y: number, rect: { left: number; top: number; right: number; bottom: number }) {
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
}

function clientPointFromConnectEvent(
  event:
    | { clientX: number; clientY: number }
    | { touches: ArrayLike<{ clientX: number; clientY: number }>; changedTouches: ArrayLike<{ clientX: number; clientY: number }> },
): { x: number; y: number } | null {
  if ('clientX' in event && 'clientY' in event) {
    return { x: event.clientX, y: event.clientY }
  }
  const touch = event.touches[0] ?? event.changedTouches[0]
  return touch ? { x: touch.clientX, y: touch.clientY } : null
}

function toggleIds(baseIds: string[], toggledIds: string[]): string[] {
  const next = new Set(baseIds)
  for (const id of toggledIds) {
    if (next.has(id)) {
      next.delete(id)
    } else {
      next.add(id)
    }
  }
  return Array.from(next)
}

function portLayoutMetrics(node: NodeRecord) {
  if (node.kind === 'organizer') {
    return {
      topOffset: ORGANIZER_NODE_PORT_CENTER_OFFSET,
      step: PORT_ROW_HEIGHT,
    }
  }
  if (node.kind === 'constant') {
    return {
      topOffset: CONSTANT_NODE_PORT_CENTER_OFFSET,
      step: PORT_ROW_HEIGHT,
    }
  }
  return {
    topOffset: STANDARD_NODE_PORT_CENTER_OFFSET,
    step: PORT_ROW_HEIGHT,
  }
}

function portAnchorForSelection(
  snapshot: ProjectSnapshot,
  node: NodeRecord,
  nodeDimensions: Record<string, { width: number; height: number }>,
  side: 'input' | 'output',
  portName: string,
): { x: number; y: number } | null {
  const layout = snapshot.graph.layout.find((entry) => entry.node_id === node.id)
  const width = nodeDimensions[node.id]?.width ?? layout?.w ?? (node.kind === 'constant' ? CONSTANT_NODE_WIDTH : 360)
  const inputs = inputsForNode(node)
  const outputs = outputsForNode(node)
  const ports = side === 'input' ? inputs : outputs
  const index = ports.findIndex((port) => port.name === portName)
  if (index === -1) {
    return null
  }
  const metrics = portLayoutMetrics(node)
  const x = (layout?.x ?? 80) + (side === 'output' ? width : 0)
  const y = (layout?.y ?? 80) + metrics.topOffset + index * metrics.step
  return { x, y }
}

function PortRow({
  node,
  snapshot,
  port,
  side,
  connectionIntent,
  index,
  onPortContextMenu,
}: {
  node: NodeRecord
  snapshot: ProjectSnapshot
  port: Port
  side: 'input' | 'output'
  connectionIntent: ConnectionIntent
  index: number
  onPortContextMenu: (nodeId: string, portName: string, side: 'input' | 'output', position: { x: number; y: number }) => void
}) {
  const source = side === 'input' ? inputBindingSource(snapshot, node.id, port.name) : null
  const upstreamArtifact = source ? artifactFor(snapshot, source.source_node, source.source_port) : null
  const ownArtifact = side === 'output' ? artifactFor(snapshot, node.id, port.name) : null
  const inputIsDisconnected = side === 'input' && !source
  const inputIsExplicitlyEmpty = side === 'input' && artifactIsEmpty(upstreamArtifact)
  const outputIsExplicitlyEmpty = side === 'output' && artifactIsEmpty(ownArtifact)
  const isEmptyOrDefault = side === 'input'
    ? Boolean(port.has_default && (inputIsDisconnected || inputIsExplicitlyEmpty))
    : outputIsExplicitlyEmpty
  const isMissingRequiredInput = side === 'input' && !port.has_default && (inputIsDisconnected || inputIsExplicitlyEmpty)
  const state =
    side === 'input'
      ? inputState(snapshot, node.id, port)
      : artifactFor(snapshot, node.id, port.name)?.state ?? 'pending'
  const typeColor = TYPE_COLORS[port.data_type] ?? TYPE_COLORS.object
  const stateColor = isMissingRequiredInput
    ? MISSING_REQUIRED_INPUT_COLOR
    : isEmptyOrDefault
      ? EMPTY_OR_DEFAULT_COLOR
      : STATE_COLORS[state]
  const isConnectionStart = connectionIntent?.nodeId === node.id
    && connectionIntent?.handleId === `${side === 'input' ? 'in' : 'out'}:${port.name}`
    && connectionIntent?.handleType === (side === 'input' ? 'target' : 'source')
  const isConnecting = Boolean(connectionIntent)
  const matchesConnectionIntent = connectionIntent ? isCompatibleWithIntent(snapshot, node, port, side, connectionIntent) : true
  const isCompatible = !connectionIntent || isConnectionStart || matchesConnectionIntent
  const isHighlighted = isConnectionStart || (isConnecting && matchesConnectionIntent)

  function handlePortCircleContextMenu(event: React.MouseEvent) {
    event.preventDefault()
    event.stopPropagation()
    onPortContextMenu(node.id, port.name, side, { x: event.clientX, y: event.clientY })
  }

  return (
    <div
      className={`rf-port-row ${side} ${isConnecting ? 'connecting' : ''} ${isCompatible ? '' : 'incompatible'} ${isEmptyOrDefault ? 'empty-or-default' : ''} ${isMissingRequiredInput ? 'missing-required' : ''}`}
      title={`${port.name} (${port.data_type})`}
    >
      {side === 'input' ? (
        <Handle
          type="target"
          id={`in:${port.name}`}
          position={Position.Left}
          className={`rf-handle ${isConnectionStart ? 'connection-start' : ''} ${isConnecting ? 'connecting' : ''} ${isHighlighted ? 'connection-highlight' : ''}`}
          style={{ color: typeColor, borderColor: handleBorderColor(stateColor), background: stateColor }}
          onContextMenu={handlePortCircleContextMenu}
        />
      ) : null}
      <PortLabel name={port.name} label={port.label} dataType={port.data_type} className="rf-port-copy" showTypeDot typeDotPosition={side === 'input' ? 'before' : 'after'} />
      {side === 'output' ? (
        <Handle
          type="source"
          id={`out:${port.name}`}
          position={Position.Right}
          className={`rf-handle ${isConnectionStart ? 'connection-start' : ''} ${isConnecting ? 'connecting' : ''} ${isHighlighted ? 'connection-highlight' : ''}`}
          style={{ color: typeColor, borderColor: handleBorderColor(stateColor), background: stateColor }}
          onContextMenu={handlePortCircleContextMenu}
        />
      ) : null}
    </div>
  )
}

function organizerGhostRows(
  node: NodeRecord,
  ghostInsertIndex: number | null,
  connecting: boolean,
): Array<{ kind: 'port'; port: Port } | { kind: 'ghost'; insertIndex: number }> {
  const ports = outputsForNode(node)
  if (!ports.length && !connecting) {
    return [{ kind: 'ghost', insertIndex: 0 }]
  }
  if (ghostInsertIndex === null) {
    return ports.map((port) => ({ kind: 'port', port }))
  }
  const rows: Array<{ kind: 'port'; port: Port } | { kind: 'ghost'; insertIndex: number }> = []
  ports.forEach((port, index) => {
    if (index === ghostInsertIndex) {
      rows.push({ kind: 'ghost', insertIndex: ghostInsertIndex })
    }
    rows.push({ kind: 'port', port })
  })
  if (ghostInsertIndex >= ports.length) {
    rows.push({ kind: 'ghost', insertIndex: ghostInsertIndex })
  }
  if (!rows.length) {
    rows.push({ kind: 'ghost', insertIndex: ghostInsertIndex })
  }
  return rows
}

function OrganizerLaneRow({
  node,
  snapshot,
  port,
  connectionIntent,
  onPortContextMenu,
}: {
  node: NodeRecord
  snapshot: ProjectSnapshot
  port: Port
  connectionIntent: ConnectionIntent
  onPortContextMenu: (nodeId: string, portName: string, side: 'input' | 'output', position: { x: number; y: number }) => void
}) {
  const inputArtifactState = inputState(snapshot, node.id, port)
  const outputArtifactState = artifactFor(snapshot, node.id, port.name)?.state ?? inputArtifactState
  const source = inputBindingSource(snapshot, node.id, port.name)
  const upstreamArtifact = source ? artifactFor(snapshot, source.source_node, source.source_port) : null
  const ownArtifact = artifactFor(snapshot, node.id, port.name)
  const inputIsDisconnected = !source
  const inputIsExplicitlyEmpty = artifactIsEmpty(upstreamArtifact)
  const outputIsEmpty = artifactIsEmpty(ownArtifact)
  const inputIsEmptyOrDefault = Boolean(port.has_default && (inputIsDisconnected || inputIsExplicitlyEmpty))
  const inputIsMissingRequired = !port.has_default && (inputIsDisconnected || inputIsExplicitlyEmpty)
  const typeColor = TYPE_COLORS[port.data_type] ?? TYPE_COLORS.object
  const sourceHandleId = `out:${port.name}`
  const targetHandleId = `in:${port.name}`
  const sourceStart = connectionIntent?.nodeId === node.id && connectionIntent.handleId === sourceHandleId && connectionIntent.handleType === 'source'
  const targetStart = connectionIntent?.nodeId === node.id && connectionIntent.handleId === targetHandleId && connectionIntent.handleType === 'target'
  const connecting = Boolean(connectionIntent)
  const connectionHandleType = connectionIntent?.handleType ?? null
  const inputCompatible = !connectionIntent || isCompatibleWithIntent(snapshot, node, port, 'input', connectionIntent)
  const outputCompatible = !connectionIntent || isCompatibleWithIntent(snapshot, node, port, 'output', connectionIntent)
  const highlightInput = targetStart || (connectionHandleType === 'source' && inputCompatible)
  const highlightOutput = sourceStart || (connectionHandleType === 'target' && outputCompatible)
  const rowCompatible = !connectionIntent || highlightInput || highlightOutput

  function handleInputContextMenu(event: React.MouseEvent) {
    event.preventDefault()
    event.stopPropagation()
    onPortContextMenu(node.id, port.name, 'input', { x: event.clientX, y: event.clientY })
  }

  function handleOutputContextMenu(event: React.MouseEvent) {
    event.preventDefault()
    event.stopPropagation()
    onPortContextMenu(node.id, port.name, 'output', { x: event.clientX, y: event.clientY })
  }

  return (
    <div className={`rf-organizer-row ${connecting ? 'connecting' : ''} ${rowCompatible ? '' : 'incompatible'} ${inputIsEmptyOrDefault || outputIsEmpty ? 'empty-or-default' : ''} ${inputIsMissingRequired ? 'missing-required' : ''}`} title={`${displayPortName(port)} (${port.data_type})`}>
      <Handle
        type="target"
        id={targetHandleId}
        position={Position.Left}
        className={`rf-handle ${targetStart ? 'connection-start' : ''} ${connecting ? 'connecting' : ''} ${highlightInput ? 'connection-highlight' : ''}`}
        style={{
          color: typeColor,
          borderColor: handleBorderColor(inputIsMissingRequired ? MISSING_REQUIRED_INPUT_COLOR : inputIsEmptyOrDefault ? EMPTY_OR_DEFAULT_COLOR : STATE_COLORS[inputArtifactState]),
          background: inputIsMissingRequired ? MISSING_REQUIRED_INPUT_COLOR : inputIsEmptyOrDefault ? EMPTY_OR_DEFAULT_COLOR : STATE_COLORS[inputArtifactState],
        }}
        onContextMenu={handleInputContextMenu}
      />
      <PortLabel name={port.name} label={port.label} dataType={port.data_type} className="rf-organizer-copy" showTypeDot />
      <Handle
        type="source"
        id={sourceHandleId}
        position={Position.Right}
        className={`rf-handle ${sourceStart ? 'connection-start' : ''} ${connecting ? 'connecting' : ''} ${highlightOutput ? 'connection-highlight' : ''}`}
        style={{
          color: typeColor,
          borderColor: handleBorderColor(outputIsEmpty ? EMPTY_OR_DEFAULT_COLOR : STATE_COLORS[outputArtifactState]),
          background: outputIsEmpty ? EMPTY_OR_DEFAULT_COLOR : STATE_COLORS[outputArtifactState],
        }}
        onContextMenu={handleOutputContextMenu}
      />
    </div>
  )
}

function OrganizerGhostRow({ insertIndex, connecting }: { insertIndex: number; connecting: boolean }) {
  return (
    <div className={`rf-organizer-row ghost ${connecting ? 'connecting' : ''}`}>
      <div className="rf-organizer-copy ghost-copy">
        <strong>New lane</strong>
      </div>
    </div>
  )
}

function OrganizerGhostHandleLayer({
  slotCount,
  connecting,
  visibleInsertIndex,
}: {
  slotCount: number
  connecting: boolean
  visibleInsertIndex: number | null
}) {
  const slotIndices = Array.from({ length: slotCount }, (_, index) => index)
  return (
    <div className="rf-organizer-slot-layer" aria-hidden="true">
      {slotIndices.map((insertIndex) => (
        <div
          key={`slot:${insertIndex}`}
          className={`rf-organizer-slot-row ${visibleInsertIndex === insertIndex ? 'visible-slot-row' : ''}`}
          style={{ top: insertIndex * 40 }}
        >
          <Handle
            type="target"
            id={`ghost-in:${insertIndex}`}
            position={Position.Left}
            className={`rf-handle ghost-handle organizer-slot-handle ${connecting ? 'connecting' : ''} ${visibleInsertIndex === insertIndex ? 'visible-slot-handle' : ''}`}
            isValidConnection={(connection) => Boolean(connection.source && connection.source !== connection.target)}
          />
          <Handle
            type="source"
            id={`ghost-out:${insertIndex}`}
            position={Position.Right}
            className={`rf-handle ghost-handle organizer-slot-handle ${connecting ? 'connecting' : ''} ${visibleInsertIndex === insertIndex ? 'visible-slot-handle' : ''}`}
            isValidConnection={(connection) => Boolean(connection.target && connection.source !== connection.target)}
          />
        </div>
      ))}
    </div>
  )
}

function compactConstantPreview(node: NodeRecord, snapshot: ProjectSnapshot): { text: string; fontSize: number } {
  const artifactName = outputsForNode(node)[0]?.name ?? node.ui?.artifact_name ?? 'value'
  const artifact = artifactFor(snapshot, node.id, artifactName)
  const preview = artifact?.preview
  const dataType = node.ui?.data_type ?? 'object'
  if (!preview || preview.kind === 'empty') {
    return constantPreviewLayout('-')
  }
  if (preview.kind === 'simple') {
    const inspectorText = 'inspector_text' in preview && typeof preview.inspector_text === 'string'
      ? preview.inspector_text
      : null
    const compactRepr = 'compact_repr' in preview && typeof preview.compact_repr === 'string'
      ? preview.compact_repr
      : null
    if (dataType === 'str') {
      if (inspectorText) {
        try {
          const parsed = JSON.parse(inspectorText)
          if (typeof parsed === 'string') {
            return constantPreviewLayout(parsed)
          }
        } catch {
        }
      }
      const raw = preview.repr
      const normalized = (raw.startsWith('"') && raw.endsWith('"')) || (raw.startsWith("'") && raw.endsWith("'"))
        ? raw.slice(1, -1)
        : raw
      return constantPreviewLayout(normalized.replace(/\\n/g, ' ').replace(/\\t/g, ' '))
    }
    if ((dataType === 'dict' || dataType === 'list') && compactRepr) {
      return constantPreviewLayout(compactRepr)
    }
    if (inspectorText) {
      return constantPreviewLayout(inspectorText)
    }
    return constantPreviewLayout(preview.repr)
  }
  if (preview.kind === 'file') {
    return constantPreviewLayout('file')
  }
  if (preview.kind === 'dataframe') {
    return constantPreviewLayout('df')
  }
  if (preview.kind === 'series') {
    return constantPreviewLayout('[...]')
  }
  if (preview.kind === 'graph') {
    return constantPreviewLayout(`${preview.node_count}n ${preview.edge_count}e`)
  }
  return constantPreviewLayout(node.ui?.data_type === 'list' ? '[...]' : '{...}')
}

function truncateConstantPreview(value: string, maxLength: number): string {
  const collapsed = value.replace(/\s+/g, ' ').trim()
  if (collapsed.length <= maxLength) {
    return collapsed || '-'
  }
  return `${collapsed.slice(0, Math.max(1, maxLength - 1))}…`
}

function constantPreviewLayout(value: string): { text: string; fontSize: number } {
  const collapsed = value.replace(/\s+/g, ' ').trim() || '-'
  const text = truncateConstantPreview(collapsed, 12)
  const length = collapsed.length
  if (length <= 3) {
    return { text, fontSize: 22 }
  }
  if (length <= 5) {
    return { text, fontSize: 18 }
  }
  if (length <= 7) {
    return { text, fontSize: 15 }
  }
  return { text, fontSize: 11 }
}

const BulletJournalNodeCard = memo(({ data, selected }: NodeProps<BulletJournalNodeData>) => {
  const { node, snapshot, onSelect, onNodeContextMenu, onPortContextMenu, onEditConstantNode, onEditFileNode, onEditOrganizerNode, onEditAreaNode, onOpenEditor, onKillEditor, onRunNode, onOpenArtifacts } = data
  const inputs = inputsForNode(node)
  const outputs = outputsForNode(node)
  const counts = artifactCounts(snapshot, node.id)
  const badge = badgeForNode(snapshot, node)
  const validationIssues = validationIssuesForNode(snapshot, node.id)
  const blockingValidationIssues = validationIssues.filter((issue) => issue.severity === 'error')
  const hasBlockingValidationIssues = blockingValidationIssues.length > 0
  const validationSummary = blockingValidationIssues.map((issue) => issue.message).join('\n')
  const hasActiveEditor = data.activeEditorNodeIds.includes(node.id)
  const isExecutionActive = data.activeRunNodeId === node.id
  const isExecutionQueued = data.queuedRunNodeIds.includes(node.id)
  const isEditorBlockedByExecution = !hasActiveEditor && (isExecutionActive || isExecutionQueued)
  const isExecutionComplete = data.completedRunNodeIds.includes(node.id)
  const editorBlockedReason = isExecutionActive
    ? 'Cannot open the editor while this notebook is executing.'
    : isExecutionQueued
      ? 'Cannot open the editor while this notebook is queued for execution.'
      : undefined
  const executionMeta = node.execution_meta
  const serverNowMs = data.serverNowMs
  const [now, setNow] = useState(() => Date.now())
  const [editorMenuOpen, setEditorMenuOpen] = useState(false)
  const [runMenuOpen, setRunMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const connectionIntent = data.connectionIntent
  const noticeClassName = data.activeNoticeSeverity ? `has-active-notice-${data.activeNoticeSeverity}` : ''
  const hoveredNoticeClassName = data.hoveredNotice ? 'notice-hovered' : ''
  const dashboardSourceSelected = data.selectedDashboardSourceNodeIds.includes(node.id)
  const showDashboardSourcePort = node.kind === 'notebook' && (data.selectedDashboardId !== null || data.selectedDashboardEdgeNotebookIds.includes(node.id))
  const showDashboardTargetPort = node.kind === 'dashboard' && (data.selectedDashboardId === node.id || data.selectedDashboardEdgeDashboardIds.includes(node.id))

  useEffect(() => {
    if (!isExecutionActive) {
      return
    }
    const interval = window.setInterval(() => setNow(Date.now()), 100)
    return () => window.clearInterval(interval)
  }, [isExecutionActive])

  useEffect(() => {
    if (!editorMenuOpen && !runMenuOpen) {
      return
    }
    function handlePointerDown(event: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as globalThis.Node)) {
        setEditorMenuOpen(false)
        setRunMenuOpen(false)
      }
    }
    window.addEventListener('pointerdown', handlePointerDown)
    return () => window.removeEventListener('pointerdown', handlePointerDown)
  }, [editorMenuOpen, runMenuOpen])

  const shouldShowExecutionTimer = Boolean(executionMeta) && (isExecutionActive || executionMeta?.status === 'succeeded')
  const shouldShowExecutionProgress = !hasActiveEditor && Boolean(executionMeta)
  const usesExecutionFreshnessHead = node.kind === 'notebook' && outputs.length === 0
  const playButtonIsExecuting = node.kind === 'notebook' && (isExecutionActive || isExecutionQueued)
  const playButtonNeedsAttention = node.kind === 'notebook' && (
    counts.stale > 0
    || counts.pending > 0
    || node.state === 'stale'
    || node.state === 'pending'
    || (usesExecutionFreshnessHead && (isExecutionQueued || isExecutionActive))
  )
  const approxServerNowMs = serverNowMs + (now - data.serverNowClientAnchorMs)
  const totalCells = executionMeta?.total_cells ?? null
  const runningCellNumber = executionMeta?.current_cell?.cell_number ?? null
  const completedCells = executionMeta?.status === 'succeeded'
    ? (totalCells ?? 0)
    : isExecutionActive && typeof runningCellNumber === 'number' && runningCellNumber > 1
      ? runningCellNumber - 1
      : (executionMeta?.last_completed_cell_number ?? 0)
  const completedProgressPercent = totalCells && totalCells > 0
    ? Math.min((completedCells / totalCells) * 100, 100)
    : 0
  const runningSegmentPercent = totalCells && totalCells > 0 && isExecutionActive
    ? 100 / totalCells
    : 0
  const runningSegmentLeftPercent = totalCells && totalCells > 0 && runningCellNumber && runningCellNumber > 0
    ? Math.min(((runningCellNumber - 1) / totalCells) * 100, 100)
    : completedProgressPercent
  let executionTimerLabel: string | null = null
  if (executionMeta && shouldShowExecutionTimer) {
    if (isExecutionActive) {
      const startedAt = Date.parse(executionMeta.started_at)
      if (!Number.isNaN(startedAt)) {
        executionTimerLabel = formatDurationSeconds((approxServerNowMs - startedAt) / 1000)
      }
    } else if (typeof executionMeta.duration_seconds === 'number') {
      executionTimerLabel = formatDurationSeconds(executionMeta.duration_seconds)
    }
  }

  if (node.kind === 'organizer') {
    const organizerRows = organizerGhostRows(node, data.organizerGhostInsertIndex, Boolean(connectionIntent))
    const organizerSlotCount = Math.max(1, outputsForNode(node).length + 1)
    const visibleGhostInsertIndex = data.organizerGhostInsertIndex ?? (outputs.length === 0 ? 0 : null)
    return (
        <div
          className={`rf-node organizer-node state-${node.state} ${node.ui?.frozen ? 'is-frozen' : ''} ${selected ? 'is-selected' : ''} ${hasBlockingValidationIssues ? 'has-validation-error' : ''} ${noticeClassName} ${hoveredNoticeClassName}`}
          title={validationSummary || undefined}
        onDoubleClick={(event) => {
          event.stopPropagation()
          onEditOrganizerNode(node.id)
        }}
        onContextMenu={(event) => {
          event.preventDefault()
          event.stopPropagation()
          onNodeContextMenu(node.id, { x: event.clientX, y: event.clientY })
        }}
      >
        <div className="rf-organizer-body">
          <OrganizerGhostHandleLayer
            slotCount={organizerSlotCount}
            connecting={Boolean(connectionIntent)}
            visibleInsertIndex={visibleGhostInsertIndex}
          />
          {organizerRows.map((row) => row.kind === 'port'
            ? <OrganizerLaneRow key={row.port.name} node={node} snapshot={snapshot} port={row.port} connectionIntent={connectionIntent} onPortContextMenu={onPortContextMenu} />
            : <OrganizerGhostRow key={`ghost-${row.insertIndex}`} insertIndex={row.insertIndex} connecting={Boolean(connectionIntent)} />)}
        </div>
      </div>
    )
  }

  if (node.kind === 'area') {
    const area = areaSettings(node)
    const title = node.title.trim()
    return (
      <div
        className={`rf-area-node area-color-${area.color} ${area.filled ? 'filled' : 'transparent'} ${selected ? 'is-selected' : ''}`}
        data-title-position={area.titlePosition}
        onDoubleClick={(event) => {
          event.stopPropagation()
          onEditAreaNode(node.id)
        }}
        onContextMenu={(event) => {
          event.preventDefault()
          event.stopPropagation()
          onNodeContextMenu(node.id, { x: event.clientX, y: event.clientY })
        }}
      >
        {selected ? (
          <>
            {(['top-left', 'top-right', 'bottom-left', 'bottom-right'] as const).map((position) => (
              <NodeResizeControl
                key={position}
                position={position}
                variant={ResizeControlVariant.Handle}
                className="area-resize-handle"
                minWidth={160}
                minHeight={120}
                onResize={(_event, params) => {
                  data.onNodeResizePreview(node.id, params.x, params.y, params.width, params.height)
                }}
                onResizeEnd={(_event, params) => {
                  data.onNodeResizePreview(node.id, params.x, params.y, params.width, params.height)
                  data.onNodeResize(node.id, params.x, params.y, params.width, params.height)
                }}
              />
            ))}
          </>
        ) : null}
        {title ? <div className="rf-area-title">{title}</div> : null}
      </div>
    )
  }

  if (node.kind === 'dashboard') {
    return (
      <div
        className={`rf-node rf-dashboard-node state-${node.state} ${selected ? 'is-selected' : ''} ${noticeClassName} ${hoveredNoticeClassName}`}
        onDoubleClick={(event) => {
          event.stopPropagation()
          data.onOpenDashboard(node.id, { newTab: true })
        }}
        onContextMenu={(event) => {
          event.preventDefault()
          event.stopPropagation()
          onNodeContextMenu(node.id, { x: event.clientX, y: event.clientY })
        }}
      >
        <Handle
          type="target"
          id="dashboard-link:bottom"
          position={Position.Bottom}
          className={showDashboardTargetPort ? 'rf-dashboard-target-port' : 'rf-dashboard-hidden-handle'}
        />
        <div className="rf-dashboard-copy">
          <div className="rf-node-header rf-dashboard-header">
            <div className="rf-node-titles">
              <h4>{node.title}</h4>
              <span>{node.id}</span>
            </div>
          </div>
          <div className="rf-node-footer rf-dashboard-footer">
            <button
              type="button"
              className="round-node-action dashboard-open"
              aria-label="Open dashboard"
              title="Open dashboard"
              onClick={(event) => {
                event.stopPropagation()
                data.onOpenDashboard(node.id, { newTab: true })
              }}
            >
              <Eye className="dashboard-open-icon" width={21} height={21} strokeWidth={2.35} />
            </button>
            <div className="artifact-button dashboard-asset-button">
              Assets
              <ArtifactCounts
                counts={node.ui?.asset_counts ?? { pending: 0, stale: 0, ready: 0 }}
                compact
              />
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (node.kind === 'constant') {
    const outputPort = outputs[0] ?? null
    const ownArtifact = outputPort ? artifactFor(snapshot, node.id, outputPort.name) : null
    const outputIsExplicitlyEmpty = artifactIsEmpty(ownArtifact)
    const outputArtifactState = ownArtifact?.state ?? 'pending'
    const typeColor = TYPE_COLORS[outputPort?.data_type ?? node.ui?.data_type ?? 'object'] ?? TYPE_COLORS.object
    const handleColor = outputIsExplicitlyEmpty ? EMPTY_OR_DEFAULT_COLOR : STATE_COLORS[outputArtifactState]
    const isConnectionStart = connectionIntent?.nodeId === node.id
      && connectionIntent?.handleId === `out:${outputPort?.name ?? 'value'}`
      && connectionIntent?.handleType === 'source'
    const isConnecting = Boolean(connectionIntent)
    const matchesConnectionIntent = connectionIntent && outputPort
      ? isCompatibleWithIntent(snapshot, node, outputPort, 'output', connectionIntent)
      : true
    const preview = compactConstantPreview(node, snapshot)

    function handleOutputContextMenu(event: React.MouseEvent) {
      if (!outputPort) {
        return
      }
      event.preventDefault()
      event.stopPropagation()
      onPortContextMenu(node.id, outputPort.name, 'output', { x: event.clientX, y: event.clientY })
    }

    return (
      <div
        className={`rf-node constant-node state-${node.state} ${node.ui?.frozen ? 'is-frozen' : ''} ${selected ? 'is-selected' : ''} ${hasBlockingValidationIssues ? 'has-validation-error' : ''} ${noticeClassName} ${hoveredNoticeClassName}`}
        title={validationSummary || `${preview.text} (${outputPort?.data_type ?? node.ui?.data_type ?? 'object'})`}
        style={{ '--constant-type-color': typeColor } as CSSProperties}
        onDoubleClick={(event) => {
          event.stopPropagation()
          onEditConstantNode(node.id)
        }}
        onContextMenu={(event) => {
          event.preventDefault()
          event.stopPropagation()
          onNodeContextMenu(node.id, { x: event.clientX, y: event.clientY })
        }}
      >
        <div className={`rf-constant-content ${isConnecting && !isConnectionStart && !matchesConnectionIntent ? 'incompatible' : ''}`}>
          <span className="rf-constant-preview" style={{ fontSize: `${preview.fontSize}px` }}>{preview.text}</span>
          {outputPort ? (
            <Handle
              type="source"
              id={`out:${outputPort.name}`}
              position={Position.Right}
              className={`rf-handle rf-constant-handle ${isConnectionStart ? 'connection-start' : ''} ${isConnecting && matchesConnectionIntent ? 'connection-highlight' : ''}`}
              style={{
                color: typeColor,
                borderColor: handleBorderColor(handleColor),
                background: handleColor,
              }}
              onContextMenu={handleOutputContextMenu}
            />
          ) : null}
        </div>
      </div>
    )
  }

  return (
    <div
      className={`rf-node state-${node.state} ${node.ui?.frozen ? 'is-frozen' : ''} ${selected ? 'is-selected' : ''} ${hasBlockingValidationIssues ? 'has-validation-error' : ''} ${isExecutionActive ? 'execution-active' : ''} ${isExecutionQueued ? 'execution-queued' : ''} ${isExecutionComplete ? 'execution-complete' : ''} ${noticeClassName} ${hoveredNoticeClassName}`}
      title={validationSummary || undefined}
      onDoubleClick={(event) => {
        event.stopPropagation()
        if (node.kind === 'notebook') {
          if (isEditorBlockedByExecution) {
            return
          }
          onOpenEditor(node.id)
          return
        }
        if (node.kind === 'file_input') {
          onEditFileNode(node.id)
        }
        if (node.kind === 'constant') {
          onEditConstantNode(node.id)
        }
      }}
      onContextMenu={(event) => {
        event.preventDefault()
        event.stopPropagation()
        onNodeContextMenu(node.id, { x: event.clientX, y: event.clientY })
      }}
    >
      {node.kind === 'notebook' ? (
        <Handle
          type="source"
          id="dashboard-link:top"
          position={Position.Top}
          className={showDashboardSourcePort ? `rf-dashboard-source-port${dashboardSourceSelected ? ' selected' : ''}` : 'rf-dashboard-hidden-handle'}
          role="button"
          tabIndex={showDashboardSourcePort ? 0 : -1}
          aria-pressed={dashboardSourceSelected}
          aria-label={dashboardSourceSelected ? 'Remove notebook from dashboard' : 'Add notebook to dashboard'}
          title={dashboardSourceSelected ? 'Remove notebook from dashboard' : 'Add notebook to dashboard'}
          onClick={(event) => {
            if (!showDashboardSourcePort) {
              return
            }
            event.preventDefault()
            event.stopPropagation()
            data.onToggleDashboardSource(node.id)
          }}
          onKeyDown={(event) => {
            if (!showDashboardSourcePort || (event.key !== 'Enter' && event.key !== ' ')) {
              return
            }
            event.preventDefault()
            event.stopPropagation()
            data.onToggleDashboardSource(node.id)
          }}
        >
          {dashboardSourceSelected ? <Eye className="rf-dashboard-source-port-icon" width={13} height={13} /> : null}
        </Handle>
      ) : null}
      <div className="rf-node-header">
        <div className={`rf-badge tone-${badge.tone}`} title={badge.title}>{badge.label}</div>
        <div className="rf-node-titles">
          <h4>{node.title}</h4>
          <span>{node.id}</span>
        </div>
        {node.ui?.frozen ? <div className="rf-node-freeze-pill">Frozen</div> : null}
        {hasBlockingValidationIssues ? <div className="rf-node-issue-pill" title={validationSummary}>{blockingValidationIssues.length} error{blockingValidationIssues.length === 1 ? '' : 's'}</div> : null}
        {executionTimerLabel ? <div className={`rf-node-timer ${isExecutionActive ? 'running' : 'complete'}`} title={isExecutionActive ? 'Current orchestrated run time' : 'Most recent orchestrated run time'}>{executionTimerLabel}</div> : null}
        {shouldShowExecutionProgress ? (
          <div className="rf-node-progress-track" aria-hidden="true">
            <div
              className="rf-node-progress"
              style={{
                width: `${completedProgressPercent}%`,
              }}
            />
            {isExecutionActive && runningSegmentPercent > 0 ? (
              <div
                className="rf-node-progress-current"
                style={{
                  left: `${runningSegmentLeftPercent}%`,
                  width: `${runningSegmentPercent}%`,
                }}
              />
            ) : null}
          </div>
        ) : null}
      </div>
      <div className="rf-node-body">
        <div className="rf-port-column">
          {inputs.map((port, index) => (
            <PortRow key={`in-${port.name}`} node={node} snapshot={snapshot} port={port} side="input" connectionIntent={connectionIntent} index={index} onPortContextMenu={onPortContextMenu} />
          ))}
        </div>
        <div className="rf-port-column output">
          {outputs.map((port, index) => (
            <PortRow key={`out-${port.name}`} node={node} snapshot={snapshot} port={port} side="output" connectionIntent={connectionIntent} index={index} onPortContextMenu={onPortContextMenu} />
          ))}
        </div>
      </div>
      <div className="rf-node-footer">
        <div className="rf-actions">
          {!NON_RUNNABLE_NODE_KINDS.has(node.kind) ? (
            <div className="round-action-group" ref={menuRef}>
              <button className={`round-node-action play ${playButtonIsExecuting ? 'is-running' : playButtonNeedsAttention ? 'needs-run' : 'is-ready'}`} onClick={(event) => {
                event.stopPropagation()
                setRunMenuOpen(false)
                onRunNode(node.id, 'run_stale', 'node')
              }} onContextMenu={(event) => {
                event.preventDefault()
                event.stopPropagation()
                setEditorMenuOpen(false)
                setRunMenuOpen((current) => !current)
              }} aria-label="Run notebook"><Play width={20} height={20} /></button>
              {runMenuOpen ? (
                <div className="split-menu run-menu" onClick={(event) => event.stopPropagation()}>
                  <button className="secondary menu-item success-text" onClick={() => {
                    setRunMenuOpen(false)
                    onRunNode(node.id, 'run_stale', 'node')
                  }}>Run</button>
                  <button className="secondary menu-item" onClick={() => {
                    setRunMenuOpen(false)
                    onRunNode(node.id, 'run_all', 'ancestors')
                  }}>← with ancestors</button>
                  <button className="secondary menu-item" onClick={() => {
                    setRunMenuOpen(false)
                    onRunNode(node.id, 'run_all', 'descendants')
                  }}>with descendants →</button>
                </div>
              ) : null}
              {node.kind === 'notebook' ? (
                <>
                  <button className={`round-node-action editor ${hasActiveEditor ? 'active-editor' : ''}`} onClick={(event) => {
                    event.stopPropagation()
                    if (hasActiveEditor) {
                      setRunMenuOpen(false)
                      setEditorMenuOpen((current) => !current)
                      return
                    }
                    onOpenEditor(node.id)
                  }} aria-label={hasActiveEditor ? 'Editor actions' : 'Open editor'} disabled={isEditorBlockedByExecution} title={editorBlockedReason}><Pencil width={20} height={20} style={{ transform: 'translate(0.5px, 0.5px)' }} /></button>
                  {editorMenuOpen ? (
                    <div className="split-menu editor-menu" onClick={(event) => event.stopPropagation()}>
                      <button className="secondary menu-item" disabled={Boolean(editorBlockedReason)} title={editorBlockedReason} onClick={() => {
                        setEditorMenuOpen(false)
                        onOpenEditor(node.id)
                      }}>Open editor</button>
                      <button className="secondary menu-item" onClick={() => {
                        setEditorMenuOpen(false)
                        onKillEditor(node.id)
                      }}>Kill editor</button>
                    </div>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : null}
        </div>
        <button className="artifact-button" onClick={(event) => {
          event.stopPropagation()
          onOpenArtifacts(node.id)
        }}>
          Artifacts
          <ArtifactCounts counts={counts} compact />
        </button>
      </div>
    </div>
  )
})

const nodeTypes = {
  bulletJournalNode: BulletJournalNodeCard,
}

function isCompatibleWithIntent(snapshot: ProjectSnapshot, node: NodeRecord, port: Port, side: 'input' | 'output', intent: NonNullable<ConnectionIntent>) {
  if (intent.handleType === 'source') {
    if (side !== 'input' || intent.nodeId === node.id) {
      return false
    }
    const sourcePortName = intent.handleId.replace('out:', '')
    const sourceNode = snapshot.graph.nodes.find((item) => item.id === intent.nodeId)
    const sourcePort = outputsForNode(sourceNode ?? node).find((item) => item.name === sourcePortName)
    return sourcePort?.data_type === port.data_type
  }
  if (side !== 'output' || intent.nodeId === node.id) {
    return false
  }
  const targetPortName = intent.handleId.replace('in:', '')
  const targetNode = snapshot.graph.nodes.find((item) => item.id === intent.nodeId)
  const targetPort = inputsForNode(targetNode ?? node).find((item) => item.name === targetPortName)
  return targetPort?.data_type === port.data_type
}

function isGhostHandle(handleId: string | null | undefined): boolean {
  return Boolean(handleId && (handleId.startsWith('ghost-in:') || handleId.startsWith('ghost-out:')))
}

function fixedNodeHeight(node: NodeRecord, layoutHeight: number | undefined): number | undefined {
  if (node.kind === 'area') {
    return layoutHeight ?? 220
  }
  if (node.kind === 'constant') {
    return 40
  }
  return undefined
}

export function GraphCanvas({ snapshot, serverNowMs = Date.now(), serverNowClientAnchorMs = Date.now(), selectedNodeIds, selectedEdgeIds, activeRunNodeId = null, queuedRunNodeIds = [], completedRunNodeIds = [], activeEditorNodeIds = [], onConnect, onEdgesChange, onSelectionChange, onNodeSelect, onEdgeSelect, onNodeContextMenu, onSelectionContextMenu, onPortContextMenu, onEditConstantNode, onEditFileNode, onEditOrganizerNode, onEditAreaNode, onOpenEditor, onOpenDashboard, onKillEditor, onRunNode, onOpenArtifacts, onCanvasInteract, onCanvasClear, onNodeMove, onNodeResize, onNodesDelete, draggedBlock, onBlockDrop, onViewportChange, dashboardPseudoLinks = [], selectedDashboardId = null, selectedDashboardSourceNodeIds = [], onToggleDashboardSource = () => undefined, nodeNoticeSeverityById = {}, hoveredNoticeNodeId = null, focusedNotice = null }: GraphCanvasProps) {
  const { screenToFlowPosition, setCenter, setViewport } = useReactFlow()
  const store = useStoreApi()
  const updateNodeInternals = useUpdateNodeInternals()
  const shellRef = useRef<HTMLDivElement | null>(null)
  const pendingLayoutRef = useRef<Record<string, { x: number; y: number; w?: number; h?: number }>>({})
  const selectionStateRef = useRef<{ additive: boolean; baseNodeIds: string[]; baseEdgeIds: string[] } | null>(null)
  const suppressNativeSelectionRef = useRef(false)
  const initializedViewportProjectIdRef = useRef<string | null>(null)
  const connectionStartPointRef = useRef<{ x: number; y: number } | null>(null)
  const [pointerFlowPosition, setPointerFlowPosition] = useState<{ x: number; y: number } | null>(null)
  const userSelectionRect = useStore((state: FlowSelectionState) => state.userSelectionRect)
  const transform = useStore((state: FlowSelectionState) => state.transform)
  const rawConnectionIntent = useConnectionIntent()
  const [connectionDragActive, setConnectionDragActive] = useState(false)
  const [pendingLayoutVersion, setPendingLayoutVersion] = useState(0)
  const [nodeDimensions, setNodeDimensions] = useState<Record<string, { width: number; height: number }>>({})
  const lastHandleSignatureRef = useRef<Record<string, string>>({})
  const lastFocusedNoticeTokenRef = useRef<number | null>(null)
  const connectionIntent = connectionDragActive ? rawConnectionIntent : null

  useEffect(() => {
    if (rawConnectionIntent) {
      return
    }
    connectionStartPointRef.current = null
    setPointerFlowPosition(null)
    setConnectionDragActive(false)
  }, [rawConnectionIntent])

  const organizerGhostByNodeId = useMemo(() => {
    const previews: Record<string, number | null> = {}
    if (!connectionIntent || !pointerFlowPosition) {
      return previews
    }
    const sourceNodeId = connectionIntent.nodeId
    const sourceHandleId = connectionIntent.handleId
    const sourceNode = snapshot.graph.nodes.find((node) => node.id === sourceNodeId) ?? null
    const startedFromOrganizerGhost = isOrganizerGhostHandle(sourceHandleId)
    const startedFromOrganizer = sourceNode?.kind === 'organizer'
    let nearest: { nodeId: string; insertIndex: number; distance: number } | null = null
    for (const node of snapshot.graph.nodes) {
      if (node.kind !== 'organizer') {
        continue
      }
      if ((startedFromOrganizer || startedFromOrganizerGhost) && node.id === sourceNodeId) {
        continue
      }
      const layout = snapshot.graph.layout.find((entry) => entry.node_id === node.id)
      if (!layout) {
        continue
      }
      const width = nodeDimensions[node.id]?.width ?? layout.w ?? 160
      const height = nodeDimensions[node.id]?.height ?? layout.h ?? 140
      const dx = Math.max(layout.x - pointerFlowPosition.x, 0, pointerFlowPosition.x - (layout.x + width))
      const dy = Math.max(layout.y - pointerFlowPosition.y, 0, pointerFlowPosition.y - (layout.y + height))
      const distance = Math.hypot(dx, dy)
      if (distance > 80) {
        continue
      }
      const portCount = outputsForNode(node).length
      const insertIndex = Math.max(0, Math.min(portCount, Math.round((pointerFlowPosition.y - layout.y - ORGANIZER_NODE_PORT_CENTER_OFFSET) / PORT_ROW_HEIGHT)))
      if (!nearest || distance < nearest.distance) {
        nearest = { nodeId: node.id, insertIndex, distance }
      }
    }
    if (nearest) {
      previews[nearest.nodeId] = nearest.insertIndex
    }
    return previews
  }, [connectionIntent, nodeDimensions, pointerFlowPosition, snapshot.graph.layout, snapshot.graph.nodes])
  const organizerGhostSignature = useMemo(
    () => JSON.stringify(Object.entries(organizerGhostByNodeId).sort(([left], [right]) => left.localeCompare(right))),
    [organizerGhostByNodeId],
  )
  const selectedDashboardPseudoEdgeNotebookIds = useMemo(
    () => Array.from(new Set(
      dashboardPseudoLinks
        .filter((edge) => selectedEdgeIds.includes(`dashboard:${edge.sourceNodeId}__${edge.dashboardNodeId}`))
        .map((edge) => edge.sourceNodeId),
    )),
    [dashboardPseudoLinks, selectedEdgeIds],
  )
  const selectedDashboardPseudoEdgeDashboardIds = useMemo(
    () => Array.from(new Set(
      dashboardPseudoLinks
        .filter((edge) => selectedEdgeIds.includes(`dashboard:${edge.sourceNodeId}__${edge.dashboardNodeId}`))
        .map((edge) => edge.dashboardNodeId),
    )),
    [dashboardPseudoLinks, selectedEdgeIds],
  )

  const mappedNodes = useMemo<Node<BulletJournalNodeData>[]>(() => {
    const layoutByNode = Object.fromEntries(snapshot.graph.layout.map((entry) => [entry.node_id, entry]))
    return snapshot.graph.nodes.map((node) => {
      const layout = layoutByNode[node.id]
      const height = fixedNodeHeight(node, layout?.h)
      return {
        id: node.id,
        type: 'bulletJournalNode',
        data: {
          node,
          snapshot,
          serverNowMs,
          serverNowClientAnchorMs,
          activeRunNodeId: activeRunNodeId ?? null,
          queuedRunNodeIds: queuedRunNodeIds ?? [],
          completedRunNodeIds: completedRunNodeIds ?? [],
          activeEditorNodeIds,
          onSelect: onNodeSelect,
          onNodeContextMenu,
          onPortContextMenu,
          onEditConstantNode,
          onEditFileNode,
          onEditOrganizerNode,
          onEditAreaNode,
          onOpenEditor,
          onOpenDashboard,
          onKillEditor,
          onRunNode,
          onOpenArtifacts,
          selectedDashboardId,
          selectedDashboardSourceNodeIds,
          selectedDashboardEdgeNotebookIds: selectedDashboardPseudoEdgeNotebookIds,
          selectedDashboardEdgeDashboardIds: selectedDashboardPseudoEdgeDashboardIds,
          onToggleDashboardSource,
          organizerGhostInsertIndex: organizerGhostByNodeId[node.id] ?? null,
          connectionIntent,
          onNodeResizePreview: previewNodeResize,
          onNodeResize,
          activeNoticeSeverity: nodeNoticeSeverityById[node.id] ?? null,
          hoveredNotice: hoveredNoticeNodeId === node.id,
        },
        position: { x: layout?.x ?? 80, y: layout?.y ?? 80 },
        style: {
          width: layout?.w ?? 360,
          ...(height === undefined ? {} : { height }),
        },
        width: nodeDimensions[node.id]?.width,
        height: nodeDimensions[node.id]?.height,
        selected: selectedNodeIds.includes(node.id),
        draggable: node.kind !== 'area' || selectedNodeIds.includes(node.id),
        connectable: node.kind !== 'area',
        zIndex: node.kind === 'area' ? -1 : 0,
      }
    })
  }, [snapshot, serverNowMs, serverNowClientAnchorMs, selectedNodeIds, activeRunNodeId, queuedRunNodeIds, completedRunNodeIds, activeEditorNodeIds, onNodeContextMenu, onPortContextMenu, onEditConstantNode, onEditFileNode, onEditOrganizerNode, onEditAreaNode, onKillEditor, onNodeResize, onNodeSelect, onOpenArtifacts, onOpenDashboard, onOpenEditor, onRunNode, onToggleDashboardSource, selectedDashboardId, selectedDashboardSourceNodeIds, selectedDashboardPseudoEdgeNotebookIds, selectedDashboardPseudoEdgeDashboardIds, nodeDimensions, organizerGhostByNodeId, connectionIntent, pendingLayoutVersion, nodeNoticeSeverityById, hoveredNoticeNodeId])

  useEffect(() => {
    const currentNodeIds = new Set(snapshot.graph.nodes.map((node) => node.id))
    setNodeDimensions((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(([nodeId]) => currentNodeIds.has(nodeId)),
      )
      return Object.keys(next).length === Object.keys(current).length ? current : next
    })
  }, [snapshot.graph.nodes])

  useLayoutEffect(() => {
    if (!shellRef.current) {
      return
    }
    if (initializedViewportProjectIdRef.current === snapshot.project.project_id) {
      return
    }
    const shell = shellRef.current
    const frame = window.requestAnimationFrame(() => {
      const rect = shell.getBoundingClientRect()
      if (!rect.width || !rect.height) {
        return
      }
      if (!snapshot.graph.nodes.length) {
        const emptyViewport: Viewport = { x: 0, y: 0, zoom: GRAPH_DEFAULT_ZOOM }
        initializedViewportProjectIdRef.current = snapshot.project.project_id
        void setViewport(emptyViewport)
        return
      }

      const layoutByNodeId = new Map(snapshot.graph.layout.map((entry) => [entry.node_id, entry]))
      let minX = Number.POSITIVE_INFINITY
      let minY = Number.POSITIVE_INFINITY
      let maxX = Number.NEGATIVE_INFINITY
      let maxY = Number.NEGATIVE_INFINITY

      for (const node of snapshot.graph.nodes) {
        const layout = layoutByNodeId.get(node.id)
        const width = nodeDimensions[node.id]?.width ?? layout?.w ?? 360
        const height = nodeDimensions[node.id]?.height ?? layout?.h ?? 220
        const x = layout?.x ?? 80
        const y = layout?.y ?? 80
        minX = Math.min(minX, x)
        minY = Math.min(minY, y)
        maxX = Math.max(maxX, x + width)
        maxY = Math.max(maxY, y + height)
      }

      if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
        return
      }

      initializedViewportProjectIdRef.current = snapshot.project.project_id
      void setViewport(
        getViewportForBounds(
          {
            x: minX,
            y: minY,
            width: Math.max(maxX - minX, 1),
            height: Math.max(maxY - minY, 1),
          },
          rect.width,
          rect.height,
          GRAPH_MIN_ZOOM,
          GRAPH_DEFAULT_ZOOM,
          GRAPH_FIT_PADDING,
        ),
      )
    })
    return () => window.cancelAnimationFrame(frame)
  }, [nodeDimensions, setViewport, snapshot.graph.layout, snapshot.graph.nodes, snapshot.project.project_id])

  useLayoutEffect(() => {
    const nextSignatureById = Object.fromEntries(
      snapshot.graph.nodes.map((node) => [
        node.id,
        JSON.stringify({
          inputs: (node.interface?.inputs ?? []).map((port) => [port.name, port.data_type, port.declaration_index ?? null]),
          outputs: (node.interface?.outputs ?? []).map((port) => [port.name, port.data_type, port.declaration_index ?? null]),
          organizerGhostInsertIndex: node.kind === 'organizer' ? (organizerGhostByNodeId[node.id] ?? null) : null,
          dashboardSourcePort: node.kind === 'notebook' && (selectedDashboardId !== null || selectedDashboardPseudoEdgeNotebookIds.includes(node.id)),
          dashboardSourceSelected: selectedDashboardSourceNodeIds.includes(node.id),
          dashboardTargetPort: selectedDashboardId === node.id || selectedDashboardPseudoEdgeDashboardIds.includes(node.id),
        }),
      ]),
    )
    const changedNodeIds = Object.entries(nextSignatureById)
      .filter(([nodeId, signature]) => lastHandleSignatureRef.current[nodeId] !== signature)
      .map(([nodeId]) => nodeId)
    lastHandleSignatureRef.current = nextSignatureById
    if (!changedNodeIds.length) {
      return
    }
    updateNodeInternals(changedNodeIds)
  }, [snapshot.graph.nodes, organizerGhostByNodeId, organizerGhostSignature, selectedDashboardId, selectedDashboardSourceNodeIds, selectedDashboardPseudoEdgeNotebookIds, selectedDashboardPseudoEdgeDashboardIds, updateNodeInternals])

  const nodes = useMemo(() => {
    let changed = false
    const nextNodes = mappedNodes.map((node) => {
      const pendingLayout = pendingLayoutRef.current[node.id]
      if (!pendingLayout) {
        return node
      }
      const snapshotCaughtUp = node.position.x === pendingLayout.x
        && node.position.y === pendingLayout.y
        && (pendingLayout.w === undefined || node.style?.width === pendingLayout.w)
        && (pendingLayout.h === undefined || node.style?.height === pendingLayout.h)
      if (snapshotCaughtUp) {
        changed = true
        delete pendingLayoutRef.current[node.id]
        return node
      }
      return {
        ...node,
        position: { x: pendingLayout.x, y: pendingLayout.y },
        style: {
          ...node.style,
          width: pendingLayout.w ?? node.style?.width,
          height: pendingLayout.h ?? node.style?.height,
        },
      }
    })
    if (changed) {
      window.setTimeout(() => setPendingLayoutVersion((current) => current + 1), 0)
    }
    return nextNodes
  }, [mappedNodes, pendingLayoutVersion])

  function previewNodeResize(nodeId: string, x: number, y: number, w: number, h: number) {
    pendingLayoutRef.current[nodeId] = { x, y, w, h }
    setPendingLayoutVersion((current) => current + 1)
  }

  const edges = useMemo<Edge[]>(() => {
    const nodeById = new Map(snapshot.graph.nodes.map((node) => [node.id, node]))
    const executionEdges = snapshot.graph.edges.map((edge) => {
      const isSelected = selectedEdgeIds.includes(edge.id)
      const isFrozen = Boolean(nodeById.get(edge.source_node)?.ui?.frozen && nodeById.get(edge.target_node)?.ui?.frozen)
      const stroke = isSelected ? '#1d8f78' : isFrozen ? 'var(--freeze-edge)' : '#75858a'
      const className = [isSelected ? 'rf-edge-selected' : null, isFrozen ? 'rf-edge-frozen' : null]
        .filter(Boolean)
        .join(' ') || undefined
      return {
        id: edge.id,
        source: edge.source_node,
        target: edge.target_node,
        sourceHandle: `out:${edge.source_port}`,
        targetHandle: `in:${edge.target_port}`,
        className,
        selected: isSelected,
        animated: false,
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
        style: { strokeWidth: isSelected ? 3.6 : isFrozen ? 2.8 : 2.2, stroke },
      }
    })
    const pseudoEdges = dashboardPseudoLinks
      .filter((edge) => nodeById.has(edge.sourceNodeId) && nodeById.has(edge.dashboardNodeId))
      .map((edge) => {
        const id = `dashboard:${edge.sourceNodeId}__${edge.dashboardNodeId}`
        const isSelected = selectedEdgeIds.includes(id)
        return {
          id,
          source: edge.sourceNodeId,
          target: edge.dashboardNodeId,
          sourceHandle: 'dashboard-link:top',
          targetHandle: 'dashboard-link:bottom',
          className: isSelected ? 'rf-dashboard-edge rf-edge-selected' : 'rf-dashboard-edge',
          selected: isSelected,
          animated: false,
          markerEnd: { type: MarkerType.ArrowClosed, color: isSelected ? '#1d8f78' : '#2563eb' },
          style: {
            strokeWidth: 3.6,
            stroke: isSelected ? '#1d8f78' : '#2563eb',
            opacity: 0.9,
          },
        }
      })
    return [...executionEdges, ...pseudoEdges]
  }, [dashboardPseudoLinks, snapshot.graph.edges, snapshot.graph.nodes, selectedEdgeIds])

  const handleNodeDragStop: NodeDragHandler = (_event, node) => {
    onCanvasInteract()
    pendingLayoutRef.current[node.id] = { x: node.position.x, y: node.position.y }
    setPendingLayoutVersion((current) => current + 1)
    onNodeMove(node.id, node.position.x, node.position.y)
  }

  useEffect(() => {
    const selectionState = selectionStateRef.current
    if (!selectionState || !userSelectionRect) {
      return
    }

    const [translateX, translateY, zoom] = transform
    const left = (userSelectionRect.x - translateX) / zoom
    const top = (userSelectionRect.y - translateY) / zoom
    const right = left + userSelectionRect.width / zoom
    const bottom = top + userSelectionRect.height / zoom
    const layoutByNodeId = new Map(snapshot.graph.layout.map((entry) => [entry.node_id, entry]))

    const rectSelectedNodeIds = snapshot.graph.nodes
      .filter((node) => {
        const layout = layoutByNodeId.get(node.id)
        const width = nodeDimensions[node.id]?.width ?? layout?.w ?? 360
        const height = nodeDimensions[node.id]?.height ?? layout?.h ?? 220
        const x = layout?.x ?? 80
        const y = layout?.y ?? 80
        return x >= left && y >= top && x + width <= right && y + height <= bottom
      })
      .map((node) => node.id)

    const rectSelectedEdgeIds = snapshot.graph.edges
      .filter((edge) => {
        const sourceNode = snapshot.graph.nodes.find((node) => node.id === edge.source_node)
        const targetNode = snapshot.graph.nodes.find((node) => node.id === edge.target_node)
        if (!sourceNode || !targetNode) {
          return false
        }
        const sourceAnchor = portAnchorForSelection(snapshot, sourceNode, nodeDimensions, 'output', edge.source_port)
        const targetAnchor = portAnchorForSelection(snapshot, targetNode, nodeDimensions, 'input', edge.target_port)
        if (!sourceAnchor || !targetAnchor) {
          return false
        }
        const rect = { left, top, right, bottom }
        return pointInRect(sourceAnchor.x, sourceAnchor.y, rect) && pointInRect(targetAnchor.x, targetAnchor.y, rect)
      })
      .map((edge) => edge.id)

    if (selectionState.additive) {
      onSelectionChange(
        toggleIds(selectionState.baseNodeIds, rectSelectedNodeIds),
        toggleIds(selectionState.baseEdgeIds, rectSelectedEdgeIds),
        { additive: true },
      )
      return
    }

    onSelectionChange(rectSelectedNodeIds, rectSelectedEdgeIds)
  }, [nodeDimensions, onSelectionChange, snapshot.graph.edges, snapshot.graph.layout, snapshot.graph.nodes, transform, userSelectionRect])

  useEffect(() => {
    if (!shellRef.current) {
      return
    }
    const rect = shellRef.current.getBoundingClientRect()
    if (!rect.width || !rect.height) {
      return
    }
    onViewportChange({
      center: screenToFlowPosition({
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      }),
      zoom: transform[2] ?? 1,
    })
  }, [onViewportChange, screenToFlowPosition, transform])

  useEffect(() => {
    if (!focusedNotice) {
      return
    }
    if (lastFocusedNoticeTokenRef.current === focusedNotice.token) {
      return
    }
    const layout = snapshot.graph.layout.find((entry) => entry.node_id === focusedNotice.nodeId)
    if (!layout) {
      return
    }
    lastFocusedNoticeTokenRef.current = focusedNotice.token
    const width = nodeDimensions[focusedNotice.nodeId]?.width ?? layout.w ?? 360
    const height = nodeDimensions[focusedNotice.nodeId]?.height ?? layout.h ?? 220
    void setCenter(
      layout.x + width / 2,
      layout.y + height / 2,
      {
        zoom: GRAPH_DEFAULT_ZOOM,
        duration: 220,
      },
    )
  }, [focusedNotice, nodeDimensions, setCenter, snapshot.graph.layout])

  return (
    <div
      className="graph-canvas-shell"
      ref={shellRef}
      onPointerMove={(event) => {
        if (!rawConnectionIntent) {
          return
        }
        if (!connectionDragActive) {
          const startPoint = connectionStartPointRef.current
          if (!startPoint) {
            setConnectionDragActive(true)
          } else {
            const distance = Math.hypot(event.clientX - startPoint.x, event.clientY - startPoint.y)
            if (distance < CONNECTION_DRAG_ACTIVATION_DISTANCE) {
              return
            }
            setConnectionDragActive(true)
          }
        }
        setPointerFlowPosition(screenToFlowPosition({ x: event.clientX, y: event.clientY }))
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        minZoom={GRAPH_MIN_ZOOM}
        maxZoom={GRAPH_MAX_ZOOM}
        defaultViewport={{ x: 0, y: 0, zoom: GRAPH_DEFAULT_ZOOM }}
        zoomOnDoubleClick={false}
        connectionMode={ConnectionMode.Strict}
        connectionRadius={26}
        snapToGrid
        snapGrid={[20, 20]}
        nodesDraggable
        nodesConnectable
        elementsSelectable
        selectionOnDrag
        selectionMode={SelectionMode.Full}
        selectionKeyCode={['Shift']}
        multiSelectionKeyCode={['Shift']}
        elevateNodesOnSelect={false}
        deleteKeyCode={null}
        onNodesChange={(changes: NodeChange[]) => {
          let positionChanged = false
          for (const change of changes) {
            if (change.type !== 'position' || !change.position) {
              continue
            }
            const previous = pendingLayoutRef.current[change.id]
            if (previous?.x === change.position.x && previous?.y === change.position.y) {
              continue
            }
            pendingLayoutRef.current[change.id] = {
              x: change.position.x,
              y: change.position.y,
              w: previous?.w,
              h: previous?.h,
            }
            positionChanged = true
          }
          const dimensionChanges = changes.filter(
            (change): change is NodeChange & { type: 'dimensions'; dimensions: { width: number; height: number } } => {
              return change.type === 'dimensions'
                && typeof change.dimensions?.width === 'number'
                && typeof change.dimensions?.height === 'number'
            },
          )
          if (dimensionChanges.length) {
            setNodeDimensions((current) => {
              const next = { ...current }
              let changed = false
              for (const change of dimensionChanges) {
                const previous = current[change.id]
                if (previous?.width === change.dimensions.width && previous?.height === change.dimensions.height) {
                  continue
                }
                next[change.id] = {
                  width: change.dimensions.width,
                  height: change.dimensions.height,
                }
                changed = true
              }
              return changed ? next : current
            })
          }
          if (positionChanged) {
            setPendingLayoutVersion((current) => current + 1)
          }
        }}
        onEdgesChange={(changes) => {
          onEdgesChange(changes)
        }}
        onEdgeClick={(_event, edge) => {
          onCanvasInteract()
          onEdgeSelect(edge.id, { additive: _event.shiftKey })
        }}
        onEdgeContextMenu={(event, edge) => {
          if (!(selectedEdgeIds.includes(edge.id) && selectedNodeIds.length > 0 && selectedNodeIds.length + selectedEdgeIds.length > 1)) {
            return
          }
          event.preventDefault()
          event.stopPropagation()
          onSelectionContextMenu({ x: event.clientX, y: event.clientY })
        }}
        onNodeClick={(event, node) => {
          onCanvasInteract()
          onNodeSelect(node.id, { additive: event.shiftKey })
        }}
        onNodesDelete={onNodesDelete}
        onConnect={onConnect}
        isValidConnection={(connection) => {
          if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) {
            return false
          }
          const sourceNode = snapshot.graph.nodes.find((item) => item.id === connection.source)
          const targetNode = snapshot.graph.nodes.find((item) => item.id === connection.target)
          if (!sourceNode || !targetNode || sourceNode.id === targetNode.id) {
            return false
          }
          const dashboardLinkConnection = sourceNode.kind !== targetNode.kind
            && ((sourceNode.kind === 'dashboard' && targetNode.kind === 'notebook') || (sourceNode.kind === 'notebook' && targetNode.kind === 'dashboard'))
            && connection.sourceHandle.startsWith('dashboard-link:')
            && connection.targetHandle.startsWith('dashboard-link:')
          if (dashboardLinkConnection) {
            return true
          }
          const sourceGhost = isGhostHandle(connection.sourceHandle)
          const targetGhost = isGhostHandle(connection.targetHandle)
          if (sourceGhost && targetGhost) {
            return false
          }
          if (sourceGhost || targetGhost) {
            return true
          }
          const sourcePortName = connection.sourceHandle.replace('out:', '')
          const targetPortName = connection.targetHandle.replace('in:', '')
          const sourcePort = sourceGhost
            ? inputsForNode(targetNode).find((item) => item.name === targetPortName)
            : outputsForNode(sourceNode).find((item) => item.name === sourcePortName)
          const targetPort = targetGhost
            ? outputsForNode(sourceNode).find((item) => item.name === sourcePortName)
            : inputsForNode(targetNode).find((item) => item.name === targetPortName)
          return Boolean(sourcePort && targetPort && sourcePort.data_type === targetPort.data_type)
        }}
        onNodeDragStop={handleNodeDragStop}
        onPaneClick={(event) => {
          if (event.shiftKey) {
            return
          }
          onCanvasInteract()
          onCanvasClear()
        }}
        onPaneContextMenu={(event) => {
          if (selectedNodeIds.length === 0 || selectedNodeIds.length + selectedEdgeIds.length <= 1) {
            return
          }
          event.preventDefault()
          onSelectionContextMenu({ x: event.clientX, y: event.clientY })
        }}
        onSelectionContextMenu={(event) => {
          if (selectedNodeIds.length === 0 || selectedNodeIds.length + selectedEdgeIds.length <= 1) {
            return
          }
          event.preventDefault()
          onSelectionContextMenu({ x: event.clientX, y: event.clientY })
        }}
        onSelectionStart={(event) => {
          selectionStateRef.current = {
            additive: event.shiftKey,
            baseNodeIds: selectedNodeIds,
            baseEdgeIds: selectedEdgeIds,
          }
        }}
        onSelectionEnd={() => {
          selectionStateRef.current = null
          store.setState({ nodesSelectionActive: false })
          suppressNativeSelectionRef.current = true
          window.requestAnimationFrame(() => {
            suppressNativeSelectionRef.current = false
          })
        }}
        onSelectionChange={({ nodes: selectedNodes, edges: selectedEdges }) => {
          if (suppressNativeSelectionRef.current || (selectionStateRef.current && userSelectionRect)) {
            return
          }
          const selectionState = selectionStateRef.current
          const nextNodeIds = selectedNodes.map((node) => node.id)
          const nextEdgeIds = selectedEdges.map((edge) => edge.id)
          if (selectionState?.additive) {
            onSelectionChange(
              toggleIds(selectionState.baseNodeIds, nextNodeIds),
              toggleIds(selectionState.baseEdgeIds, nextEdgeIds),
              { additive: true },
            )
            return
          }
          onSelectionChange(nextNodeIds, nextEdgeIds)
        }}
        onMoveStart={onCanvasInteract}
        onNodeDragStart={onCanvasInteract}
        onConnectStart={(event, _params: OnConnectStartParams) => {
          onCanvasInteract()
          connectionStartPointRef.current = clientPointFromConnectEvent(event)
          setConnectionDragActive(false)
          setPointerFlowPosition(null)
        }}
        onConnectEnd={() => {
          connectionStartPointRef.current = null
          setConnectionDragActive(false)
          setPointerFlowPosition(null)
        }}
        defaultEdgeOptions={{ markerEnd: { type: MarkerType.ArrowClosed } }}
        onDragOver={(event) => {
          if (!draggedBlock) {
            return
          }
          event.preventDefault()
          event.dataTransfer.dropEffect = 'copy'
        }}
        onDragEnter={(event) => {
          if (!draggedBlock) {
            return
          }
          event.preventDefault()
        }}
        onDrop={(event) => {
          if (!draggedBlock) {
            return
          }
          event.preventDefault()
          const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
          onBlockDrop(position.x, position.y)
        }}
      >
        <Panel position="top-left" className="graph-panel-note">Drag nodes, connect ports, or inspect blocks.</Panel>
        <Background color="rgba(24, 53, 43, 0.24)" gap={20} size={2.2} />
      </ReactFlow>
    </div>
  )
}
