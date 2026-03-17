import { memo, useEffect, useMemo, useRef, useState } from 'react'
import ReactFlow, {
  Background,
  ConnectionMode,
  Panel,
  Handle,
  MarkerType,
  Position,
  applyNodeChanges,
  useStore,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type Node,
  type NodeDragHandler,
  type OnConnectStartParams,
  type NodeProps,
  useReactFlow,
} from 'reactflow'

import { artifactCounts, artifactFor, assetsForNode, badgeForNode, formatDurationSeconds, formatType, hiddenInputs, inputState, outputsForNode, visibleInputs } from '../lib/helpers'
import type { ArtifactState, NodeRecord, Port, ProjectSnapshot } from '../lib/types'
import { ArtifactCounts } from './ArtifactCounts'
import { Pencil, Play } from './Icons'

type GraphCanvasProps = {
  snapshot: ProjectSnapshot
  serverNowMs?: number
  serverNowClientAnchorMs?: number
  activeRunNodeId?: string | null
  queuedRunNodeIds?: string[]
  completedRunNodeIds?: string[]
  activeEditorNodeIds?: string[]
  onConnect: (connection: Connection) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  onNodeSelect: (nodeId: string) => void
  onNodeContextMenu: (nodeId: string, position: { x: number; y: number }) => void
  onEditFileNode: (nodeId: string) => void
  onOpenEditor: (nodeId: string) => void
  onKillEditor: (nodeId: string) => void
  onRunNode: (nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run') => void
  onOpenArtifacts: (nodeId: string) => void
  onCanvasInteract: () => void
  onCanvasClear: () => void
  onNodeMove: (nodeId: string, x: number, y: number) => void
  onNodesDelete: (nodes: Node[]) => void
  draggedBlock: { title: string; kind: string } | null
  onBlockDrop: (x: number, y: number) => void
}

const NON_RUNNABLE_NODE_KINDS = new Set(['file_input'])

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
  onSelect: (nodeId: string) => void
  onNodeContextMenu: (nodeId: string, position: { x: number; y: number }) => void
  onEditFileNode: (nodeId: string) => void
  onOpenEditor: (nodeId: string) => void
  onKillEditor: (nodeId: string) => void
  onRunNode: (nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run') => void
  onOpenArtifacts: (nodeId: string) => void
  activeEditorNodeIds: string[]
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

const PORT_TOP_OFFSET = 82
const PORT_STEP = 40

const TYPE_COLORS: Record<string, string> = {
  int: '#bf6a02',
  float: '#d97706',
  bool: '#2f855a',
  str: '#0f766e',
  list: '#2563eb',
  dict: '#4f46e5',
  file: '#7c3aed',
  object: '#6b7280',
  'pandas.DataFrame': '#0f766e',
  'pandas.Series': '#2563eb',
  'networkx.Graph': '#b45309',
  'networkx.DiGraph': '#92400e',
}

const STATE_COLORS: Record<ArtifactState | 'mixed', string> = {
  ready: '#2f855a',
  stale: '#c97c00',
  pending: '#98a2a3',
  mixed: '#2563eb',
}

function PortRow({
  node,
  snapshot,
  port,
  side,
  connectionIntent,
  index,
}: {
  node: NodeRecord
  snapshot: ProjectSnapshot
  port: Port
  side: 'input' | 'output'
  connectionIntent: ConnectionIntent
  index: number
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

  return (
    <div className={`rf-port-row ${side} ${isConnecting ? 'connecting' : ''} ${isCompatible ? '' : 'incompatible'}`} title={`${port.name} (${port.data_type})`}>
      {side === 'input' ? (
        <Handle
          type="target"
          id={`in:${port.name}`}
          position={Position.Left}
          className={`rf-handle ${isConnectionStart ? 'connection-start' : ''} ${isConnecting ? 'connecting' : ''}`}
          style={{ borderColor: typeColor, background: fillColor, top: PORT_TOP_OFFSET + index * PORT_STEP }}
        />
      ) : null}
      <div className="rf-port-copy">
        <strong>{port.name}</strong>
        <span>{formatType(port.data_type)}</span>
      </div>
      {side === 'output' ? (
        <Handle
          type="source"
          id={`out:${port.name}`}
          position={Position.Right}
          className={`rf-handle ${isConnectionStart ? 'connection-start' : ''} ${isConnecting ? 'connecting' : ''}`}
          style={{ borderColor: typeColor, background: fillColor, top: PORT_TOP_OFFSET + index * PORT_STEP }}
        />
      ) : null}
    </div>
  )
}

const BulletJournalNodeCard = memo(({ data, selected }: NodeProps<BulletJournalNodeData>) => {
  const { node, snapshot, onSelect, onNodeContextMenu, onEditFileNode, onOpenEditor, onKillEditor, onRunNode, onOpenArtifacts } = data
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
  const isExecutionComplete = data.completedRunNodeIds.includes(node.id)
  const executionMeta = node.execution_meta
  const serverNowMs = data.serverNowMs
  const [now, setNow] = useState(() => Date.now())
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const connectionIntent = useStore((state: FlowConnectionState) => {
    if (!state.connectionNodeId || !state.connectionHandleId || !state.connectionHandleType) {
      return null
    }
    return {
      nodeId: state.connectionNodeId,
      handleId: state.connectionHandleId,
      handleType: state.connectionHandleType,
    } as NonNullable<ConnectionIntent>
  })

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

  return (
    <div
      className={`rf-node state-${node.state} ${selected ? 'is-selected' : ''} ${hasBlockingValidationIssues ? 'has-validation-error' : ''} ${isExecutionActive ? 'execution-active' : ''} ${isExecutionQueued ? 'execution-queued' : ''} ${isExecutionComplete ? 'execution-complete' : ''}`}
      title={validationSummary || undefined}
      onClick={() => onSelect(node.id)}
      onContextMenu={(event) => {
        event.preventDefault()
        onSelect(node.id)
        onNodeContextMenu(node.id, { x: event.clientX, y: event.clientY })
      }}
    >
      <div className="rf-node-header">
        <div className={`rf-badge tone-${badge.tone}`} title={badge.title}>{badge.label}</div>
        <div className="rf-node-titles">
          <h4>{node.title}</h4>
          <span>{node.id}</span>
        </div>
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
            <PortRow key={`in-${port.name}`} node={node} snapshot={snapshot} port={port} side="input" connectionIntent={connectionIntent} index={index} />
          ))}
          {hidden.length ? <div className="rf-hidden-inputs">+ {hidden.length} hidden inputs</div> : null}
        </div>
        <div className="rf-port-column output">
          {outputs.map((port, index) => (
            <PortRow key={`out-${port.name}`} node={node} snapshot={snapshot} port={port} side="output" connectionIntent={connectionIntent} index={index} />
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
                  }} aria-label={hasActiveEditor ? 'Editor actions' : 'Open editor'}><Pencil width={18} height={18} /></button>
                  {menuOpen ? (
                    <div className="split-menu editor-menu" onClick={(event) => event.stopPropagation()}>
                      <button className="secondary menu-item" onClick={() => {
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

export function GraphCanvas({ snapshot, serverNowMs = Date.now(), serverNowClientAnchorMs = Date.now(), activeRunNodeId = null, queuedRunNodeIds = [], completedRunNodeIds = [], activeEditorNodeIds = [], onConnect, onEdgesChange, onNodeSelect, onNodeContextMenu, onEditFileNode, onOpenEditor, onKillEditor, onRunNode, onOpenArtifacts, onCanvasInteract, onCanvasClear, onNodeMove, onNodesDelete, draggedBlock, onBlockDrop }: GraphCanvasProps) {
  const { fitView, screenToFlowPosition } = useReactFlow()
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<string[]>([])
  const pendingPositionsRef = useRef<Record<string, { x: number; y: number }>>({})
  const hasFitViewRef = useRef(false)
  const shouldAutoFitOnMountRef = useRef(snapshot.graph.nodes.length > 0)

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
            onEditFileNode,
            onOpenEditor,
            onKillEditor,
            onRunNode,
            onOpenArtifacts,
        },
        position: { x: layout?.x ?? 80, y: layout?.y ?? 80 },
        style: { width: layout?.w ?? 360 },
      }
    })
  }, [snapshot, serverNowMs, serverNowClientAnchorMs, activeRunNodeId, queuedRunNodeIds, completedRunNodeIds, activeEditorNodeIds, onNodeContextMenu, onEditFileNode, onKillEditor, onNodeSelect, onOpenArtifacts, onOpenEditor, onRunNode])

  const [nodes, setNodes] = useState<Node<BulletJournalNodeData>[]>(mappedNodes)

  useEffect(() => {
    setNodes((current) => {
      const currentById = new Map(current.map((node) => [node.id, node]))
      return mappedNodes.map((node) => {
        const currentNode = currentById.get(node.id)
        const pendingPosition = pendingPositionsRef.current[node.id]

        if (pendingPosition) {
          const snapshotCaughtUp = node.position.x === pendingPosition.x && node.position.y === pendingPosition.y
          if (snapshotCaughtUp) {
            delete pendingPositionsRef.current[node.id]
          } else {
            return {
              ...node,
              position: pendingPosition,
              dragging: currentNode?.dragging,
            }
          }
        }

        if (currentNode?.dragging) {
          return {
            ...node,
            position: currentNode.position,
            dragging: true,
          }
        }

        return node
      })
    })
  }, [mappedNodes])

  const edges = useMemo<Edge[]>(() => {
    return snapshot.graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source_node,
      target: edge.target_node,
      sourceHandle: `out:${edge.source_port}`,
      targetHandle: `in:${edge.target_port}`,
      className: selectedEdgeIds.includes(edge.id) ? 'rf-edge-selected' : undefined,
      selected: selectedEdgeIds.includes(edge.id),
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed, color: selectedEdgeIds.includes(edge.id) ? '#1d8f78' : '#75858a' },
      style: { strokeWidth: selectedEdgeIds.includes(edge.id) ? 3.6 : 2.2, stroke: selectedEdgeIds.includes(edge.id) ? '#1d8f78' : '#75858a' },
    }))
  }, [snapshot.graph.edges, selectedEdgeIds])

  const handleNodeDragStop: NodeDragHandler = (_event, node) => {
    onCanvasInteract()
    pendingPositionsRef.current[node.id] = { x: node.position.x, y: node.position.y }
    onNodeMove(node.id, node.position.x, node.position.y)
  }

  return (
    <div className="graph-canvas-shell">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        minZoom={0.18}
        maxZoom={1.35}
        zoomOnDoubleClick={false}
        connectionMode={ConnectionMode.Strict}
        snapToGrid
        snapGrid={[20, 20]}
        nodesDraggable
        nodesConnectable
        elementsSelectable
        deleteKeyCode={['Backspace', 'Delete']}
        onNodesChange={(changes: NodeChange[]) => {
          setNodes((current) => applyNodeChanges(changes, current))
        }}
        onEdgesChange={(changes) => {
          onEdgesChange(changes)
        }}
        onEdgeClick={(_event, edge) => {
          onCanvasInteract()
          onNodeSelect('')
          setSelectedEdgeIds([edge.id])
        }}
        onNodeClick={(_event, node) => {
          onCanvasInteract()
          onNodeSelect(node.id)
          setSelectedEdgeIds([])
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
          const sourcePortName = connection.sourceHandle.replace('out:', '')
          const targetPortName = connection.targetHandle.replace('in:', '')
          const sourcePort = [...outputsForNode(sourceNode), ...assetsForNode(sourceNode)].find((item) => item.name === sourcePortName)
          const targetPort = [...visibleInputs(targetNode), ...hiddenInputs(targetNode)].find((item) => item.name === targetPortName)
          return Boolean(sourcePort && targetPort && sourcePort.data_type === targetPort.data_type)
        }}
        onNodeDoubleClick={(_event, node) => {
          const current = snapshot.graph.nodes.find((item) => item.id === node.id)
          if (current?.kind === 'notebook') {
            onOpenEditor(node.id)
          } else if (current?.kind === 'file_input') {
            onEditFileNode(node.id)
          }
        }}
        onNodeDragStop={handleNodeDragStop}
        onPaneClick={() => {
          onCanvasInteract()
          onCanvasClear()
          setSelectedEdgeIds([])
        }}
        onSelectionChange={({ nodes: selectedNodes, edges: selectedEdges }) => {
          setSelectedEdgeIds(selectedEdges.map((edge) => edge.id))
        }}
        onMoveStart={onCanvasInteract}
        onNodeDragStart={onCanvasInteract}
        onConnectStart={(_event, _params: OnConnectStartParams) => {
          onCanvasInteract()
        }}
        defaultEdgeOptions={{ markerEnd: { type: MarkerType.ArrowClosed } }}
        onInit={() => {
          if (hasFitViewRef.current || !shouldAutoFitOnMountRef.current) {
            return
          }
          const frame = window.requestAnimationFrame(() => {
            fitView({ padding: 0.18, minZoom: 0.18, duration: 0 })
            hasFitViewRef.current = true
          })
          return () => window.cancelAnimationFrame(frame)
        }}
        onDragOver={(event) => {
          if (!draggedBlock) {
            return
          }
          event.preventDefault()
          event.dataTransfer.dropEffect = 'move'
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
