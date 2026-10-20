import type { Connection } from 'reactflow'
import { outputsForNode, PORT_ROW_HEIGHT } from './helpers.ts'
import type { GraphPatchOperation, NodeRecord, Port, ProjectSnapshot } from './types'

export const NOTEBOOK_DRAFT_WIDTH = 360
export const NOTEBOOK_DRAFT_HEADER_HEIGHT = 60

export type NotebookDraftInput = {
  key: string
  name: string
  dataType: string
  description: string
  defaultToNone: boolean
  sourceNode: string | null
  sourcePort: string | null
}

export type NotebookDraft = {
  id: string
  nodeId: string
  title: string
  x: number
  y: number
  inputs: NotebookDraftInput[]
  busy?: boolean
  error?: string | null
}

export type NotebookDraftInputEdit = {
  draftId: string
  input: NotebookDraftInput
  insertIndex: number
}

export function draftInputPort(input: NotebookDraftInput, index: number): Port {
  return {
    name: input.key, label: input.name, data_type: input.dataType,
    role: null, description: input.description, default: null,
    has_default: input.defaultToNone, kind: input.dataType === 'file' ? 'file' : 'value',
    direction: 'input', declaration_index: index,
  }
}

export function draftNode(draft: NotebookDraft): NodeRecord {
  return {
    id: draft.id, kind: 'notebook', title: draft.title, state: 'idle',
    interface: { node_id: draft.id, source_hash: '', inputs: draft.inputs.map(draftInputPort), outputs: [], docs: null, issues: [] },
  }
}

export function draftEdgeId(draftId: string, key: string): string {
  return `${draftId}:${key}`
}

export function snapshotWithNotebookDrafts(snapshot: ProjectSnapshot, drafts: NotebookDraft[]): ProjectSnapshot {
  if (!drafts.length) return snapshot
  const edges = drafts.flatMap((draft) => draft.inputs.flatMap((input) => {
    const source = snapshot.graph.nodes.find((node) => node.id === input.sourceNode)
    if (!source || !outputsForNode(source).some((port) => port.name === input.sourcePort && port.data_type === input.dataType)) return []
    return [{ id: draftEdgeId(draft.id, input.key), source_node: source.id, source_port: input.sourcePort!, target_node: draft.id, target_port: input.key }]
  }))
  return {
    ...snapshot,
    graph: {
      ...snapshot.graph,
      nodes: [...snapshot.graph.nodes, ...drafts.map(draftNode)],
      layout: [...snapshot.graph.layout, ...drafts.map((draft) => ({ node_id: draft.id, x: draft.x, y: draft.y, w: NOTEBOOK_DRAFT_WIDTH, h: 160 + draft.inputs.length * PORT_ROW_HEIGHT }))],
      edges: [...snapshot.graph.edges, ...edges],
    },
  }
}

export function draftInputFromConnection(snapshot: ProjectSnapshot, drafts: NotebookDraft[], connection: Connection): NotebookDraftInputEdit | null {
  const draft = drafts.find((item) => item.id === connection.target)
  if (!draft || draft.busy || !connection.sourceHandle?.startsWith('out:') || !connection.targetHandle?.startsWith('ghost-in:')) return null
  const source = snapshot.graph.nodes.find((node) => node.id === connection.source)
  const port = source && outputsForNode(source).find((item) => item.name === connection.sourceHandle!.slice(4))
  if (!source || !port) return null
  const insertIndex = Number(connection.targetHandle.slice('ghost-in:'.length))
  if (!Number.isInteger(insertIndex) || insertIndex < 0 || insertIndex > draft.inputs.length) return null
  return {
    draftId: draft.id, insertIndex,
    input: {
      key: crypto.randomUUID(), name: port.name,
      dataType: port.data_type, description: '', defaultToNone: false,
      sourceNode: source.id, sourcePort: port.name,
    },
  }
}

export function notebookDraftOperations(draft: NotebookDraft, snapshot: ProjectSnapshot): GraphPatchOperation[] {
  if (!draft.title.trim() || !draft.nodeId || snapshot.graph.nodes.some((node) => node.id === draft.nodeId)) {
    throw new Error('Choose a notebook name and an unused node ID.')
  }
  const names = draft.inputs.map((input) => input.name)
  if (names.some((name) => !/^[a-z0-9_]+$/.test(name)) || new Set(names).size !== names.length) {
    throw new Error('Each input needs a unique artifact ID using lowercase letters, digits, and underscores.')
  }
  const edges: GraphPatchOperation[] = draft.inputs.flatMap((input) => {
    if (!input.sourceNode || !input.sourcePort) return []
    const source = snapshot.graph.nodes.find((node) => node.id === input.sourceNode)
    const port = source && outputsForNode(source).find((port) => port.name === input.sourcePort && port.data_type === input.dataType)
    if (!port) throw new Error(`The source for “${input.name}” is no longer available. Reconnect or remove this input.`)
    return [{ type: 'add_edge', source_node: input.sourceNode, source_port: input.sourcePort, target_node: draft.nodeId, target_port: input.name }]
  })
  return [{
    type: 'add_notebook_node', node_id: draft.nodeId, title: draft.title.trim(), x: draft.x, y: draft.y,
    w: NOTEBOOK_DRAFT_WIDTH, h: 220,
    inputs: draft.inputs.map((input) => ({ name: input.name, data_type: input.dataType, description: input.description, default_to_none: input.defaultToNone })),
  }, ...edges]
}
