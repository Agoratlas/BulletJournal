import type { EdgeRecord, GraphPatchOperation, LayoutRecord, NodeRecord, NoticeRecord, ProjectSnapshot } from './lib/types'

export type PalettePreviewBlock = {
  key: string
  title: string
  kind: 'notebook' | 'constant' | 'file_input' | 'organizer' | 'area'
  x: number
  y: number
  width: number
  height: number
}

export type PaletteEntry = {
  key: string
  title: string
  description?: string
  documentation?: string
  kind: 'empty' | 'constant' | 'organizer' | 'area' | 'template' | 'pipeline'
  templateRef?: string
  templateName?: string
  templateProvider?: string
  previewSize?: {
    width: number
    height: number
  }
  previewBlocks?: PalettePreviewBlock[]
}

export type PortActionMenuState = {
  nodeId: string
  portName: string
  side: 'input' | 'output'
  x: number
  y: number
}

export type ConstantValueType = 'int' | 'float' | 'bool' | 'str' | 'list' | 'dict' | 'file' | 'pandas.DataFrame'

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
  tone?: 'default' | 'danger' | 'success'
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
