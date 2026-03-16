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

import { artifactCounts, artifactFor, assetsForNode, badgeForNode, formatType, hiddenInputs, inputState, outputsForNode, visibleInputs } from '../lib/helpers'
import type { ArtifactState, NodeRecord, Port, ProjectSnapshot } from '../lib/types'
import { ArtifactCounts } from './ArtifactCounts'
import { ChevronDown } from './Icons'

type GraphCanvasProps = {
  snapshot: ProjectSnapshot
  onConnect: (connection: Connection) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  onNodeSelect: (nodeId: string) => void
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

type BulletJournalNodeData = {
  node: NodeRecord
  snapshot: ProjectSnapshot
  onSelect: (nodeId: string) => void
  onRunNode: (nodeId: string, mode: 'run_stale' | 'run_all' | 'edit_run') => void
  onOpenArtifacts: (nodeId: string) => void
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
  const { node, snapshot, onSelect, onRunNode, onOpenArtifacts } = data
  const visible = visibleInputs(node)
  const hidden = hiddenInputs(node)
  const outputs = outputsForNode(node)
  const assets = assetsForNode(node)
  const counts = artifactCounts(snapshot, node.id)
  const totalArtifacts = counts.ready + counts.stale + counts.pending
  const progressPercent = totalArtifacts > 0 ? (counts.ready / totalArtifacts) * 100 : 0
  const badge = badgeForNode(snapshot, node)
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

  return (
    <div className={`rf-node state-${node.state} ${selected ? 'is-selected' : ''}`} onClick={() => onSelect(node.id)}>
      <div className="rf-node-header">
        <div className={`rf-badge tone-${badge.tone}`} title={badge.title}>{badge.label}</div>
        <div className="rf-node-titles">
          <h4>{node.title}</h4>
          <span>{node.id}</span>
        </div>
        <button className="icon-button" title="View notebook docs" onClick={(event) => {
          event.stopPropagation()
          onSelect(node.id)
        }}>i</button>
      </div>
      <div className="rf-node-progress-track" aria-hidden="true">
        <div
          className="rf-node-progress"
          style={{
            width: `${progressPercent}%`,
          }}
        />
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
            <div className="split-button" ref={menuRef}>
              <button onClick={(event) => {
                event.stopPropagation()
                onRunNode(node.id, 'run_stale')
              }}>Run</button>
              <button className="split-toggle" onClick={(event) => {
                event.stopPropagation()
                setMenuOpen((current) => !current)
              }} aria-label="More run options"><ChevronDown width={16} height={16} /></button>
              {menuOpen ? (
                <div className="split-menu" onClick={(event) => event.stopPropagation()}>
                  <button className="secondary menu-item" onClick={() => {
                    setMenuOpen(false)
                    onRunNode(node.id, 'run_stale')
                  }}>Run</button>
                  <button className="secondary menu-item" onClick={() => {
                    setMenuOpen(false)
                    onRunNode(node.id, 'run_all')
                  }}>Run all</button>
                  {node.kind === 'notebook' ? (
                    <button className="secondary menu-item" onClick={() => {
                      setMenuOpen(false)
                      onRunNode(node.id, 'edit_run')
                    }}>Edit</button>
                  ) : null}
                </div>
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

export function GraphCanvas({ snapshot, onConnect, onEdgesChange, onNodeSelect, onRunNode, onOpenArtifacts, onCanvasInteract, onCanvasClear, onNodeMove, onNodesDelete, draggedBlock, onBlockDrop }: GraphCanvasProps) {
  const { fitView, screenToFlowPosition } = useReactFlow()
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<string[]>([])
  const [nodes, setNodes] = useState<Node<BulletJournalNodeData>[]>([])
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
          onSelect: onNodeSelect,
          onRunNode,
          onOpenArtifacts,
        },
        position: { x: layout?.x ?? 80, y: layout?.y ?? 80 },
        style: { width: layout?.w ?? 360 },
      }
    })
  }, [snapshot])

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

  useEffect(() => {
    if (hasFitViewRef.current || !shouldAutoFitOnMountRef.current || snapshot.graph.nodes.length === 0) {
      return
    }
    hasFitViewRef.current = true
    fitView({ padding: 0.18 })
  }, [fitView, snapshot.graph.nodes.length])

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
            onRunNode(node.id, 'edit_run')
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
