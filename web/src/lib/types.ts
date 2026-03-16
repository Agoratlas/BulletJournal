export type ArtifactState = 'ready' | 'stale' | 'pending'
export type NodeState = 'ready' | 'stale' | 'pending' | 'idle' | 'error' | 'running' | 'mixed'
export type NoticeSeverity = 'error' | 'warning'

export type Port = {
  name: string
  data_type: string
  role: 'output' | 'asset' | null
  description: string | null
  default: unknown
  has_default: boolean
  kind: string
  direction: 'input' | 'output'
}

export type NoticeRecord = {
  issue_id: string
  node_id: string | null
  severity: NoticeSeverity
  code: string
  message: string
  details: Record<string, unknown>
  created_at: string
}

export type ValidationIssue = NoticeRecord

export type TemplateRef = {
  kind: string
  ref: string
  origin_revision: string | null
}

export type NodeRecord = {
  id: string
  kind: 'notebook' | 'file_input'
  title: string
  path?: string | null
  template?: TemplateRef | null
  template_status?: 'template' | 'modified' | null
  ui?: {
    hidden_inputs?: string[]
    artifact_name?: string
    origin?: 'constant_value' | null
  }
  interface?: {
    node_id: string
    source_hash: string
    inputs: Port[]
    outputs: Port[]
    assets: Port[]
    docs: string | null
    issues: ValidationIssue[]
  } | null
  state: NodeState
}

export type EdgeRecord = {
  id: string
  source_node: string
  source_port: string
  target_node: string
  target_port: string
}

export type LayoutRecord = {
  node_id: string
  x: number
  y: number
  w: number
  h: number
}

export type ArtifactPreview =
  | { kind: 'simple'; repr: string; truncated?: boolean }
  | { kind: 'dataframe'; rows: number; columns: number; column_names: string[]; sample: Array<Record<string, unknown>> }
  | { kind: 'series'; rows: number; sample: unknown[] }
  | { kind: 'file'; filename?: string; size_bytes?: number; extension?: string | null; mime_type?: string | null; image_inline?: boolean; original_filename?: string }
  | { kind: 'object'; repr: string }

export type ArtifactRecord = {
  node_id: string
  artifact_name: string
  current_version_id: number | null
  state: ArtifactState
  role: 'output' | 'asset' | null
  artifact_hash: string | null
  source_hash: string | null
  upstream_code_hash: string | null
  upstream_data_hash: string | null
  run_id: string | null
  lineage_mode: string | null
  created_at: string | null
  warnings: Array<Record<string, unknown>>
  storage_kind: string | null
  data_type: string | null
  size_bytes: number | null
  extension: string | null
  mime_type: string | null
  preview: ArtifactPreview | null
}

export type RunRecord = {
  run_id: string
  project_id: string
  mode: string
  status: string
  target_json: Record<string, unknown>
  graph_version: number
  source_snapshot_json: Record<string, unknown>
  started_at: string | null
  ended_at: string | null
  failure_json: Record<string, unknown> | null
}

export type CheckpointRecord = {
  checkpoint_id: string
  created_at: string
  graph_version: number
  path: string
  restored_at: string | null
}

export type TemplateRecord = {
  kind: string
  ref: string
  title: string
  source: string
  source_text?: string
  source_hash?: string
}

export type ProjectSnapshot = {
  project: {
    project_id: string
    title: string
    created_at: string
    root: string
  }
  graph: {
    meta: {
      schema_version: number
      project_id: string
      graph_version: number
      updated_at: string
    }
    nodes: NodeRecord[]
    edges: EdgeRecord[]
    layout: LayoutRecord[]
  }
  validation_issues: ValidationIssue[]
  notices: NoticeRecord[]
  artifacts: ArtifactRecord[]
  runs: RunRecord[]
  checkpoints: CheckpointRecord[]
  templates: TemplateRecord[]
}

export type GraphPatchResponse = {
  meta: {
    schema_version: number
    project_id: string
    graph_version: number
    updated_at: string
  }
  nodes: NodeRecord[]
  edges: EdgeRecord[]
  layout: LayoutRecord[]
  interrupted_run?: {
    run_id: string
    node_id: string | null
    node_ids: string[]
  } | null
}

export type ProjectOpenResponse = ProjectSnapshot

export type GraphPatchOperation =
  | { type: 'add_notebook_node'; node_id: string; title: string; x?: number; y?: number; w?: number; h?: number; template_ref?: string; source_text?: string; ui?: { origin?: 'constant_value' | null } }
  | { type: 'add_file_input_node'; node_id: string; title: string; artifact_name?: string; x?: number; y?: number; w?: number; h?: number }
  | { type: 'add_edge'; source_node: string; source_port: string; target_node: string; target_port: string }
  | { type: 'remove_edge'; edge_id: string }
  | { type: 'update_node_layout'; node_id: string; x: number; y: number; w?: number; h?: number }
  | { type: 'update_node_title'; node_id: string; title: string }
  | { type: 'update_node_hidden_inputs'; node_id: string; hidden_inputs: string[] }
  | { type: 'delete_node'; node_id: string }

export type SessionRecord = {
  session_id: string
  node_id: string
  run_id: string
  url: string
  ready?: boolean
}

export type SseEvent = {
  id: number
  event_type: string
  project_id: string
  graph_version: number
  timestamp: string
  payload: Record<string, unknown>
}
