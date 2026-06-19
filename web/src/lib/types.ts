export type ArtifactState = 'ready' | 'stale' | 'pending'
export type NodeState = 'ready' | 'stale' | 'pending' | 'idle' | 'error' | 'running' | 'queued' | 'mixed'
export type NoticeSeverity = 'error' | 'warning'

export type Port = {
  name: string
  label?: string | null
  data_type: string
  role: 'output' | null
  description: string | null
  default: unknown
  has_default: boolean
  kind: string
  direction: 'input' | 'output'
  declaration_index?: number | null
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
  kind: 'notebook' | 'pipeline'
  provider: string
  name: string
  ref: string
  origin_revision: string | null
}

export type NodeRecord = {
  id: string
  kind: 'notebook' | 'constant' | 'file_input' | 'organizer' | 'area' | 'dashboard'
  title: string
  path?: string | null
  template?: TemplateRef | null
  template_status?: 'template' | 'modified' | null
  ui?: {
    artifact_name?: string
    data_type?: string
    frozen?: boolean
    organizer_ports?: Array<{
      key: string
      name: string
      data_type: string
    }>
    title_position?: string
    area_color?: string
    area_filled?: boolean
    source_count?: number
    panel_count?: number
    asset_counts?: {
      pending: number
      stale: number
      ready: number
    }
  }
  interface?: {
    node_id: string
    source_hash: string
    inputs: Port[]
    outputs: Port[]
    docs: string | null
    issues: ValidationIssue[]
  } | null
  execution_meta?: {
    node_id: string
    run_id: string
    status: 'running' | 'succeeded' | 'failed' | 'cancelled'
    started_at: string
    ended_at: string | null
    duration_seconds: number | null
    total_cells: number | null
    last_completed_cell_number: number | null
    current_cell: {
      cell_id: string
      cell_number: number | null
      total_cells: number | null
      cell_code: string | null
    } | null
    stdout: ExecutionLogSummary | null
    stderr: ExecutionLogSummary | null
    updated_at: string
  } | null
  orchestrator_state?: {
    node_id: string
    run_id: string
    status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
    started_at: string | null
    completed_at: string | null
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
  | { kind: 'empty' }
  | { kind: 'simple'; repr: string; truncated?: boolean; compact_repr?: string }
  | { kind: 'dataframe'; rows: number; columns: number; column_names: string[]; sample: Array<Record<string, unknown>> }
  | { kind: 'series'; rows: number; sample: unknown[] }
  | { kind: 'graph'; directed: boolean; node_count: number; edge_count: number }
  | { kind: 'file'; filename?: string; size_bytes?: number; extension?: string | null; mime_type?: string | null; image_inline?: boolean; original_filename?: string }
  | { kind: 'object'; repr: string }

export type ExecutionLogSummary = {
  text: string
  truncated: boolean
  size_bytes: number
}

export type ArtifactRecord = {
  node_id: string
  artifact_name: string
  current_version_id: number | null
  state: ArtifactState
  role: 'output' | null
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

export type AssetObjectRecord = {
  object_role: string
  object_index: number
  artifact_hash: string
  metadata: Record<string, unknown> | null
}

export type AssetRecord = {
  node_id: string
  asset_name: string
  title: string | null
  description: string | null
  declared_asset_type: string | null
  declaration_index: number | null
  current_asset_version_id: number | null
  state: ArtifactState
  asset_type: string | null
  interactive: boolean | null
  source_hash: string | null
  upstream_code_hash: string | null
  upstream_data_hash: string | null
  run_id: string | null
  lineage_mode: string | null
  definition: Record<string, unknown> | null
  modifier_schema: Array<Record<string, unknown>>
  default_modifiers: Record<string, unknown>
  override_schema_hash: string | null
  warnings: Array<Record<string, unknown>>
  created_at: string | null
  objects: AssetObjectRecord[]
}

export type AssetSortDirection = 'asc' | 'desc'

export type AssetSort = {
  column: string
  direction: AssetSortDirection
}

export type AssetFilterKind = 'range' | 'value' | 'regex'

export type AssetRangeFilter = {
  kind: 'range'
  column: string
  value_type?: string
  lower?: string | number | null
  upper?: string | number | null
}

export type AssetValueFilter = {
  kind: 'value'
  column: string
  value_type?: string
  values: Array<string | number | boolean>
  include_null?: boolean
}

export type AssetRegexFilter = {
  kind: 'regex'
  column: string
  pattern: string
  case_sensitive?: boolean
}

export type AssetFilter = AssetRangeFilter | AssetValueFilter | AssetRegexFilter

export type PreparedTableColumn = {
  id: string
  title: string
  data_type: string
  sortable: boolean
  filter_kinds?: AssetFilterKind[]
}

export type PreparedTablePayload = {
  kind: 'table'
  rows_total: number
  columns: PreparedTableColumn[]
  page: {
    index: number
    size: number
  }
  sort: AssetSort[]
  rows: Array<Record<string, unknown>>
}

export type PreparedHistogramBin = {
  index: number
  start: number
  end: number
  count: number
  label?: string
}

export type PreparedHistogramPayload = {
  kind: 'histogram'
  x_column: string
  rows_total: number
  non_null_rows: number
  bin_count: number
  domain: {
    min: number
    max: number
  } | null
  bins: PreparedHistogramBin[]
  x_value_kind?: 'numeric' | 'temporal'
  time_granularity?: 'year' | 'month' | 'week' | 'day' | 'hour'
}

export type PreparedScatterPlotPoint = {
  row_index: number
  x: number
  y: number
  label?: string | number | boolean | null
  shape?: string | number | boolean | null
  size?: string | number | boolean | null
  color?: string | number | boolean | null
}

export type PreparedScatterPlotPayload = {
  kind: 'scatter_plot'
  x_column: string
  y_column: string
  label_column: string | null
  shape_column: string | null
  size_column: string | null
  size_kind: 'quantitative' | 'nominal' | null
  size_domain: {
    min: number
    max: number
  } | null
  color_column: string | null
  color_kind: 'quantitative' | 'nominal' | null
  rows_total: number
  non_null_rows: number
  plotted_rows: number
  sampled: boolean
  domain: {
    x: {
      min: number
      max: number
    }
    y: {
      min: number
      max: number
    }
  } | null
  points: PreparedScatterPlotPoint[]
}

export type PreparedBarChartBar = {
  value: string | number | boolean
  label: string
  aggregate_value: number
  color: string
  group?: string | number | boolean
  group_label?: string
  group_proportion?: number
  category_index?: number
  group_index?: number
}

export type PreparedBarChartPayload = {
  kind: 'bar_chart'
  category_column: string
  value_column: string
  aggregation: string
  rows_total: number
  non_null_rows: number
  bars: PreparedBarChartBar[]
  group_column?: string
}

export type PreparedPieChartSlice = {
  value: string | number | boolean
  label: string
  count: number
  share: number
  color: string
}

export type PreparedPieChartPayload = {
  kind: 'pie_chart'
  category_column: string
  rows_total: number
  non_null_rows: number
  slices: PreparedPieChartSlice[]
}

export type AssetPrepareResponse = {
  asset_version_id: number
  state: ArtifactState
  resolved_modifiers: {
    page?: {
      index: number
      size: number
    }
    sort?: AssetSort[]
    filters?: AssetFilter[]
    bin_count?: number
    granularity?: string
    [key: string]: unknown
  }
  override_schema_hash: string | null
  payloads: {
    main?: PreparedBarChartPayload | PreparedHistogramPayload | PreparedPieChartPayload | PreparedScatterPlotPayload
    table?: PreparedTablePayload
  }
  errors: Array<{
    code: string
    message: string
  }>
}

export type DashboardSourceRecord = {
  node_id: string
}

export type DashboardPanelRecord = {
  panel_id: string
  node_id: string
  asset_name: string
  visible: boolean
  position: number
  panel_height: number | null
  modifier_overrides: Record<string, unknown>
  override_schema_hash: string | null
}

export type DashboardRecord = {
  schema_version: number
  dashboard_id: string
  version: number
  title: string
  created_at: string
  updated_at: string
  sources: DashboardSourceRecord[]
  panels: DashboardPanelRecord[]
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
  provider: string
  kind: 'notebook' | 'pipeline'
  name: string
  ref: string
  origin_revision: string
  hidden?: boolean
  title: string
  source: string
  documentation?: string
  source_text?: string
  source_hash?: string
  definition?: {
      title?: string
      documentation?: string
      nodes?: Array<{
        id: string
        kind: 'notebook' | 'constant' | 'file_input' | 'organizer' | 'area' | 'dashboard'
        title: string
        template_ref?: string
        data_type?: string
        value?: unknown
        artifact_name?: string
        dashboard?: {
          sources?: DashboardSourceRecord[]
          panels?: DashboardPanelRecord[]
        }
        ui?: {
          artifact_name?: string
          data_type?: string
          organizer_ports?: Array<{
            key: string
            name: string
            data_type: string
          }>
        }
      }>
    edges?: Array<{
      source_node: string
      source_port: string
      target_node: string
      target_port: string
    }>
    layout?: Array<{
      node_id: string
      x: number
      y: number
      w: number
      h: number
    }>
  }
}

export type ProjectSnapshot = {
  server_time: string
  project: {
    project_id: string
    title: string | null
    created_at: string
    root: string
    project_root: string
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

export type GraphPatchResponse = ProjectSnapshot & {
  interrupted_run?: {
    run_id: string
    node_id: string | null
    node_ids: string[]
  } | null
}

export type ProjectOpenResponse = ProjectSnapshot

export type GraphPatchOperation =
  | { type: 'add_notebook_node'; node_id: string; title: string; x?: number; y?: number; w?: number; h?: number; template_ref?: string; source_text?: string; ui?: { frozen?: boolean } }
  | { type: 'add_constant_node'; node_id: string; title?: string; data_type: string; value?: unknown; value_json?: string; ui?: { artifact_name?: string; data_type?: string; frozen?: boolean }; x?: number; y?: number; w?: number; h?: number }
  | { type: 'add_file_input_node'; node_id: string; title: string; artifact_name?: string; ui?: { frozen?: boolean }; x?: number; y?: number; w?: number; h?: number }
  | { type: 'add_organizer_node'; node_id: string; title?: string; ui?: { frozen?: boolean; organizer_ports?: Array<{ key: string; name: string; data_type: string }> }; x?: number; y?: number; w?: number; h?: number }
  | { type: 'add_area_node'; node_id: string; title?: string; ui?: { frozen?: boolean; title_position?: string; area_color?: string; area_filled?: boolean }; x?: number; y?: number; w?: number; h?: number }
  | { type: 'add_dashboard_node'; node_id: string; title?: string; ui?: { source_count?: number; panel_count?: number }; x?: number; y?: number; w?: number; h?: number }
  | { type: 'add_pipeline_template'; template_ref: string; x?: number; y?: number; node_id_prefix?: string | null }
  | { type: 'add_edge'; source_node: string; source_port: string; target_node: string; target_port: string }
  | { type: 'remove_edge'; edge_id: string }
  | { type: 'update_node_layout'; node_id: string; x: number; y: number; w?: number; h?: number }
  | { type: 'update_node_title'; node_id: string; title: string }
  | { type: 'rename_node'; node_id: string; new_node_id: string; title: string }
  | { type: 'update_constant_node'; node_id: string; data_type: string }
  | { type: 'update_organizer_ports'; node_id: string; ports: Array<{ key: string; name: string; data_type: string }> }
  | { type: 'update_area_style'; node_id: string; title_position: string; color: string; filled: boolean }
  | { type: 'update_node_frozen'; node_id: string; frozen: boolean }
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
