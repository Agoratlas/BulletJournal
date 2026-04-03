import type { EdgeRecord, GraphPatchOperation, LayoutRecord, NodeRecord, NoticeRecord, ProjectSnapshot } from './lib/types'

export type PaletteEntry = {
  key: string
  title: string
  description: string
  kind: 'empty' | 'value_input' | 'file_input' | 'template' | 'pipeline'
  templateRef?: string
}

export type PortActionMenuState = {
  nodeId: string
  portName: string
  side: 'input' | 'output'
  x: number
  y: number
}

export type ConstantValueType = 'int' | 'float' | 'bool' | 'str' | 'list' | 'dict' | 'object'

export type AppNotice = NoticeRecord & {
  origin: 'snapshot' | 'client'
}

export type GraphMutationPlan = {
  operations: GraphPatchOperation[]
  followUpOperations?: GraphPatchOperation[]
}

export type GraphHistoryEntry = {
  undo: GraphMutationPlan
  redo: GraphMutationPlan
}

export type ClipboardNodeRecord = {
  node: NodeRecord
  layout: LayoutRecord
  sourceText: string | null
}

export type ClipboardGraph = {
  nodes: ClipboardNodeRecord[]
  edges: EdgeRecord[]
}

export type NodeActionItem = {
  key: string
  label: string
  href?: string
  tone?: 'default' | 'danger'
  disabled?: boolean
  title?: string
  onClick?: () => void
}

export type OptimisticGraphState = {
  snapshot: ProjectSnapshot
  clearSelection?: boolean
  clearArtifacts?: boolean
}

export type SnapshotLike = Pick<ProjectSnapshot, 'project' | 'graph' | 'validation_issues' | 'notices' | 'artifacts' | 'runs' | 'checkpoints' | 'templates'>
