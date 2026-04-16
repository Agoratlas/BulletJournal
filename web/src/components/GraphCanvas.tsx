import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
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
  type Node,
  type NodeDragHandler,
  type OnConnectStartParams,
  type NodeProps,
  useReactFlow,
} from 'reactflow'

import { areaSettings } from '../lib/area'
import { artifactCounts, artifactFor, assetsForNode, badgeForNode, formatDurationSeconds, hiddenInputs, inputState, outputsForNode, visibleInputs } from '../lib/helpers'
import type { ArtifactState, NodeRecord, Port, ProjectSnapshot } from '../lib/types'
import { ArtifactCounts } from './ArtifactCounts'
import { Pencil, Play } from './Icons'
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
  onEditFileNode: (nodeId: string) => void
  onEditOrganizerNode: (nodeId: string) => void
  onEditAreaNode: (nodeId: string) => void
  onOpenEditor: (nodeId: string) => void
  onKillEditor: (nodeId: string) => void
  onRunNode: (nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run') => void
  onOpenArtifacts: (nodeId: string) => void
  onCanvasInteract: () => void
  onCanvasClear: () => void
  onNodeMove: (nodeId: string, x: number, y: number) => void
  onNodeResize: (nodeId: string, x: number, y: number, w: number, h: number) => void
  onNodesDelete: (nodes: Node[]) => void
  draggedBlock: { title: string; kind: string } | null
  onBlockDrop: (x: number, y: number) => void
  onViewportChange: (viewport: { center: { x: number; y: number }; zoom: number }) => void
}

const NON_RUNNABLE_NODE_KINDS = new Set(['file_input', 'organizer', 'area'])

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
  onEditFileNode: (nodeId: string) => void
  onEditOrganizerNode: (nodeId: string) => void
  onEditAreaNode: (nodeId: string) => void
  onOpenEditor: (nodeId: string) => void
  onKillEditor: (nodeId: string) => void
  onRunNode: (nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run') => void
  onOpenArtifacts: (nodeId: string) => void
  activeEditorNodeIds: string[]
  organizerGhostInsertIndex: number | null
  onNodeResizePreview: (nodeId: string, x: number, y: number, w: number, h: number) => void
  onNodeResize: (nodeId: string, x: number, y: number, w: number, h: number) => void
}

type ConnectionIntent = {
  nodeId: string
  handleId: string
  handleType: 'source' | 'target'
} | null

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

const PORT_TOP_OFFSET = 82
const PORT_STEP = 40

const STATE_COLORS: Record<ArtifactState | 'mixed', string> = {
  ready: '#2f855a',
  stale: '#c97c00',
  pending: '#98a2a3',
  mixed: '#2563eb',
}

function pointInRect(x: number, y: number, rect: { left: number; top: number; right: number; bottom: number }) {
  return x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom
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
      topOffset: 20,
      step: 40,
    }
  }
  return {
    topOffset: PORT_TOP_OFFSET,
    step: PORT_STEP,
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
  const width = nodeDimensions[node.id]?.width ?? layout?.w ?? 360
  const inputs = visibleInputs(node)
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
  const state =
    side === 'input'
      ? inputState(snapshot, node.id, port)
      : artifactFor(snapshot, node.id, port.name)?.state ?? 'pending'
  const typeColor = TYPE_COLORS[port.data_type] ?? TYPE_COLORS.object
  const fillColor = STATE_COLORS[state]
  const isConnectionStart = connectionIntent?.nodeId === node.id
    && connectionIntent?.handleId === `${side === 'input' ? 'in' : 'out'}:${port.name}`
    && connectionIntent?.handleType === (side === 'input' ? 'target' : 'source')
  const isConnecting = Boolean(connectionIntent)
  const isCompatible = !connectionIntent || isCompatibleWithIntent(snapshot, node, port, side, connectionIntent)

  function handlePortCircleContextMenu(event: React.MouseEvent) {
    event.preventDefault()
    event.stopPropagation()
    onPortContextMenu(node.id, port.name, side, { x: event.clientX, y: event.clientY })
  }

  return (
    <div
      className={`rf-port-row ${side} ${isConnecting ? 'connecting' : ''} ${isCompatible ? '' : 'incompatible'}`}
      title={`${port.name} (${port.data_type})`}
    >
      {side === 'input' ? (
        <Handle
          type="target"
          id={`in:${port.name}`}
          position={Position.Left}
          className={`rf-handle ${isConnectionStart ? 'connection-start' : ''} ${isConnecting ? 'connecting' : ''}`}
          style={{ borderColor: typeColor, background: fillColor, top: PORT_TOP_OFFSET + index * PORT_STEP }}
          onContextMenu={handlePortCircleContextMenu}
        />
      ) : null}
      <PortLabel name={port.name} label={port.label} dataType={port.data_type} className="rf-port-copy" />
      {side === 'output' ? (
        <Handle
          type="source"
          id={`out:${port.name}`}
          position={Position.Right}
          className={`rf-handle ${isConnectionStart ? 'connection-start' : ''} ${isConnecting ? 'connecting' : ''}`}
          style={{ borderColor: typeColor, background: fillColor, top: PORT_TOP_OFFSET + index * PORT_STEP }}
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
  const typeColor = TYPE_COLORS[port.data_type] ?? TYPE_COLORS.object
  const sourceHandleId = `out:${port.name}`
  const targetHandleId = `in:${port.name}`
  const sourceStart = connectionIntent?.nodeId === node.id && connectionIntent.handleId === sourceHandleId && connectionIntent.handleType === 'source'
  const targetStart = connectionIntent?.nodeId === node.id && connectionIntent.handleId === targetHandleId && connectionIntent.handleType === 'target'
  const connecting = Boolean(connectionIntent)
  const inputCompatible = !connectionIntent || isCompatibleWithIntent(snapshot, node, port, 'input', connectionIntent)
  const outputCompatible = !connectionIntent || isCompatibleWithIntent(snapshot, node, port, 'output', connectionIntent)

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
    <div className={`rf-organizer-row ${connecting ? 'connecting' : ''} ${!inputCompatible || !outputCompatible ? 'incompatible' : ''}`} title={`${displayPortName(port)} (${port.data_type})`}>
      <Handle
        type="target"
        id={targetHandleId}
        position={Position.Left}
        className={`rf-handle ${targetStart ? 'connection-start' : ''} ${connecting ? 'connecting' : ''}`}
        style={{ borderColor: typeColor, background: STATE_COLORS[inputArtifactState], top: 20 }}
        onContextMenu={handleInputContextMenu}
      />
      <PortLabel name={port.name} label={port.label} dataType={port.data_type} className="rf-organizer-copy" />
      <Handle
        type="source"
        id={sourceHandleId}
        position={Position.Right}
        className={`rf-handle ${sourceStart ? 'connection-start' : ''} ${connecting ? 'connecting' : ''}`}
        style={{ borderColor: typeColor, background: STATE_COLORS[outputArtifactState], top: 20 }}
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

const BulletJournalNodeCard = memo(({ data, selected }: NodeProps<BulletJournalNodeData>) => {
  const { node, snapshot, onSelect, onNodeContextMenu, onPortContextMenu, onEditFileNode, onEditOrganizerNode, onEditAreaNode, onOpenEditor, onKillEditor, onRunNode, onOpenArtifacts } = data
  const visible = visibleInputs(node)
  const hidden = hiddenInputs(node)
  const outputs = outputsForNode(node)
  const assets = assetsForNode(node)
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
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const connectionIntent = useConnectionIntent()

  useEffect(() => {
    if (!isExecutionActive) {
      return
    }
    const interval = window.setInterval(() => setNow(Date.now()), 100)
    return () => window.clearInterval(interval)
  }, [isExecutionActive])

  useEffect(() => {
    if (!menuOpen) {
      return
    }
    function handlePointerDown(event: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as globalThis.Node)) {
        setMenuOpen(false)
      }
    }
    window.addEventListener('pointerdown', handlePointerDown)
    return () => window.removeEventListener('pointerdown', handlePointerDown)
  }, [menuOpen])

  const shouldShowExecutionTimer = Boolean(executionMeta) && (isExecutionActive || executionMeta?.status === 'succeeded')
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
        className={`rf-node organizer-node state-${node.state} ${node.ui?.frozen ? 'is-frozen' : ''} ${selected ? 'is-selected' : ''} ${hasBlockingValidationIssues ? 'has-validation-error' : ''}`}
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

  return (
    <div
      className={`rf-node state-${node.state} ${node.ui?.frozen ? 'is-frozen' : ''} ${selected ? 'is-selected' : ''} ${hasBlockingValidationIssues ? 'has-validation-error' : ''} ${isExecutionActive ? 'execution-active' : ''} ${isExecutionQueued ? 'execution-queued' : ''} ${isExecutionComplete ? 'execution-complete' : ''}`}
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
      }}
      onContextMenu={(event) => {
        event.preventDefault()
        event.stopPropagation()
        onNodeContextMenu(node.id, { x: event.clientX, y: event.clientY })
      }}
    >
      <div className="rf-node-header">
        <div className={`rf-badge tone-${badge.tone}`} title={badge.title}>{badge.label}</div>
        <div className="rf-node-titles">
          <h4>{node.title}</h4>
          <span>{node.id}</span>
        </div>
        {node.ui?.frozen ? <div className="rf-node-freeze-pill">Frozen</div> : null}
        {hasBlockingValidationIssues ? <div className="rf-node-issue-pill" title={validationSummary}>{blockingValidationIssues.length} error{blockingValidationIssues.length === 1 ? '' : 's'}</div> : null}
        {executionTimerLabel ? <div className={`rf-node-timer ${isExecutionActive ? 'running' : 'complete'}`} title={isExecutionActive ? 'Current orchestrated run time' : 'Most recent orchestrated run time'}>{executionTimerLabel}</div> : null}
      </div>
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
      <div className="rf-node-body">
        <div className="rf-port-column">
          {visible.map((port, index) => (
            <PortRow key={`in-${port.name}`} node={node} snapshot={snapshot} port={port} side="input" connectionIntent={connectionIntent} index={index} onPortContextMenu={onPortContextMenu} />
          ))}
          {hidden.length ? <div className="rf-hidden-inputs">+ {hidden.length} hidden inputs</div> : null}
        </div>
        <div className="rf-port-column output">
          {outputs.map((port, index) => (
            <PortRow key={`out-${port.name}`} node={node} snapshot={snapshot} port={port} side="output" connectionIntent={connectionIntent} index={index} onPortContextMenu={onPortContextMenu} />
          ))}
          {assets.length ? <div className="rf-asset-note">+ {assets.length} asset{assets.length === 1 ? '' : 's'}</div> : null}
        </div>
      </div>
      <div className="rf-node-footer">
        <div className="rf-actions">
          {!NON_RUNNABLE_NODE_KINDS.has(node.kind) ? (
            <div className="round-action-group" ref={menuRef}>
              <button className="round-node-action play" onClick={(event) => {
                event.stopPropagation()
                onRunNode(node.id, 'run_stale')
              }} aria-label="Run notebook"><Play width={18} height={18} /></button>
              {node.kind === 'notebook' ? (
                <>
                  <button className={`round-node-action editor ${hasActiveEditor ? 'active-editor' : ''}`} onClick={(event) => {
                    event.stopPropagation()
                    if (hasActiveEditor) {
                      setMenuOpen((current) => !current)
                      return
                    }
                    onOpenEditor(node.id)
                  }} aria-label={hasActiveEditor ? 'Editor actions' : 'Open editor'} disabled={isEditorBlockedByExecution} title={editorBlockedReason}><Pencil width={18} height={18} /></button>
                  {menuOpen ? (
                    <div className="split-menu editor-menu" onClick={(event) => event.stopPropagation()}>
                      <button className="secondary menu-item" disabled={Boolean(editorBlockedReason)} title={editorBlockedReason} onClick={() => {
                        setMenuOpen(false)
                        onOpenEditor(node.id)
                      }}>Open editor</button>
                      <button className="secondary menu-item" onClick={() => {
                        setMenuOpen(false)
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
      ?? assetsForNode(sourceNode ?? node).find((item) => item.name === sourcePortName)
    return sourcePort?.data_type === port.data_type
  }
  if (side !== 'output' || intent.nodeId === node.id) {
    return false
  }
  const targetPortName = intent.handleId.replace('in:', '')
  const targetNode = snapshot.graph.nodes.find((item) => item.id === intent.nodeId)
  const targetPort = visibleInputs(targetNode ?? node).find((item) => item.name === targetPortName)
    ?? hiddenInputs(targetNode ?? node).find((item) => item.name === targetPortName)
  return targetPort?.data_type === port.data_type
}

function isGhostHandle(handleId: string | null | undefined): boolean {
  return Boolean(handleId && (handleId.startsWith('ghost-in:') || handleId.startsWith('ghost-out:')))
}

export function GraphCanvas({ snapshot, serverNowMs = Date.now(), serverNowClientAnchorMs = Date.now(), selectedNodeIds, selectedEdgeIds, activeRunNodeId = null, queuedRunNodeIds = [], completedRunNodeIds = [], activeEditorNodeIds = [], onConnect, onEdgesChange, onSelectionChange, onNodeSelect, onEdgeSelect, onNodeContextMenu, onSelectionContextMenu, onPortContextMenu, onEditFileNode, onEditOrganizerNode, onEditAreaNode, onOpenEditor, onKillEditor, onRunNode, onOpenArtifacts, onCanvasInteract, onCanvasClear, onNodeMove, onNodeResize, onNodesDelete, draggedBlock, onBlockDrop, onViewportChange }: GraphCanvasProps) {
  const { screenToFlowPosition } = useReactFlow()
  const store = useStoreApi()
  const updateNodeInternals = useUpdateNodeInternals()
  const shellRef = useRef<HTMLDivElement | null>(null)
  const pendingLayoutRef = useRef<Record<string, { x: number; y: number; w?: number; h?: number }>>({})
  const selectionStateRef = useRef<{ additive: boolean; baseNodeIds: string[]; baseEdgeIds: string[] } | null>(null)
  const suppressNativeSelectionRef = useRef(false)
  const [pointerFlowPosition, setPointerFlowPosition] = useState<{ x: number; y: number } | null>(null)
  const userSelectionRect = useStore((state: FlowSelectionState) => state.userSelectionRect)
  const transform = useStore((state: FlowSelectionState) => state.transform)
  const connectionIntent = useConnectionIntent()
  const [pendingLayoutVersion, setPendingLayoutVersion] = useState(0)
  const [nodeDimensions, setNodeDimensions] = useState<Record<string, { width: number; height: number }>>({})
  const lastHandleSignatureRef = useRef<Record<string, string>>({})

  const organizerGhostByNodeId = useMemo(() => {
    const previews: Record<string, number | null> = {}
    if (!connectionIntent || !pointerFlowPosition) {
      return previews
    }
    let nearest: { nodeId: string; insertIndex: number; distance: number } | null = null
    for (const node of snapshot.graph.nodes) {
      if (node.kind !== 'organizer') {
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
      const insertIndex = Math.max(0, Math.min(portCount, Math.round((pointerFlowPosition.y - layout.y - 20) / 40)))
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

  const mappedNodes = useMemo<Node<BulletJournalNodeData>[]>(() => {
    const layoutByNode = Object.fromEntries(snapshot.graph.layout.map((entry) => [entry.node_id, entry]))
    return snapshot.graph.nodes.map((node) => {
      const layout = layoutByNode[node.id]
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
          onEditFileNode,
          onEditOrganizerNode,
          onEditAreaNode,
          onOpenEditor,
          onKillEditor,
          onRunNode,
          onOpenArtifacts,
          organizerGhostInsertIndex: organizerGhostByNodeId[node.id] ?? null,
          onNodeResizePreview: previewNodeResize,
          onNodeResize,
        },
        position: { x: layout?.x ?? 80, y: layout?.y ?? 80 },
        style: {
          width: layout?.w ?? 360,
          height: node.kind === 'area' ? (layout?.h ?? 220) : undefined,
        },
        width: nodeDimensions[node.id]?.width,
        height: nodeDimensions[node.id]?.height,
        selected: selectedNodeIds.includes(node.id),
        connectable: node.kind !== 'area',
        zIndex: node.kind === 'area' ? -1 : 0,
      }
    })
  }, [snapshot, serverNowMs, serverNowClientAnchorMs, selectedNodeIds, activeRunNodeId, queuedRunNodeIds, completedRunNodeIds, activeEditorNodeIds, onNodeContextMenu, onPortContextMenu, onEditFileNode, onEditOrganizerNode, onEditAreaNode, onKillEditor, onNodeResize, onNodeSelect, onOpenArtifacts, onOpenEditor, onRunNode, nodeDimensions, organizerGhostByNodeId, pendingLayoutVersion])

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
    const nextSignatureById = Object.fromEntries(
      snapshot.graph.nodes.map((node) => [
        node.id,
        JSON.stringify({
          inputs: (node.interface?.inputs ?? []).map((port) => [port.name, port.data_type, port.declaration_index ?? null]),
          outputs: (node.interface?.outputs ?? []).map((port) => [port.name, port.data_type, port.declaration_index ?? null]),
          assets: (node.interface?.assets ?? []).map((port) => [port.name, port.data_type, port.declaration_index ?? null]),
          organizerGhostInsertIndex: node.kind === 'organizer' ? (organizerGhostByNodeId[node.id] ?? null) : null,
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
  }, [snapshot.graph.nodes, organizerGhostByNodeId, organizerGhostSignature, updateNodeInternals])

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
    return snapshot.graph.edges.map((edge) => {
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
  }, [snapshot.graph.edges, snapshot.graph.nodes, selectedEdgeIds])

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

  return (
    <div
      className="graph-canvas-shell"
      ref={shellRef}
      onPointerMove={(event) => {
        if (!connectionIntent) {
          return
        }
        setPointerFlowPosition(screenToFlowPosition({ x: event.clientX, y: event.clientY }))
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        minZoom={0.18}
        maxZoom={1.35}
        defaultViewport={{ x: 0, y: 0, zoom: 1 }}
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
            ? [...visibleInputs(targetNode), ...hiddenInputs(targetNode)].find((item) => item.name === targetPortName)
            : [...outputsForNode(sourceNode), ...assetsForNode(sourceNode)].find((item) => item.name === sourcePortName)
          const targetPort = targetGhost
            ? [...outputsForNode(sourceNode), ...assetsForNode(sourceNode)].find((item) => item.name === sourcePortName)
            : [...visibleInputs(targetNode), ...hiddenInputs(targetNode)].find((item) => item.name === targetPortName)
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
        onConnectStart={(_event, _params: OnConnectStartParams) => {
          onCanvasInteract()
          setPointerFlowPosition(null)
        }}
        onConnectEnd={() => {
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
