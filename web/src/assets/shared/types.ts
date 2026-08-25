import type { AssetFilter, AssetFilterKind, AssetHighlight, AssetRecord, AssetSort } from '../../lib/types'

export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const
export const DEFAULT_TABLE_PAGE_SIZE = 25
export const DEFAULT_DATAVIZ_TABLE_PAGE_SIZE = 10
export const DEFAULT_HISTOGRAM_CHART_HEIGHT = 600
export const DEFAULT_PIE_CHART_HEIGHT = 600
export const DEFAULT_SCATTER_PLOT_CHART_HEIGHT = 600
export const MIN_DATAVIZ_CHART_HEIGHT = 240
export const MAX_DATAVIZ_CHART_HEIGHT = 960
export const HISTOGRAM_BRUSH_SIGNAL_NAME = 'brush_selection_adjusted_start'

export type ModifierColumn = {
  id: string
  title: string
  dataType: string
  filterKinds: AssetFilterKind[]
}

export type TableState = {
  page: {
    index: number
    size: number
  }
  sort: AssetSort | null
  filters: AssetFilter[]
  highlights?: AssetHighlight[]
}

export type TimeHistogramGranularity = 'auto' | 'year' | 'month' | 'week' | 'day' | 'hour'

export type HistogramState = TableState & {
  binCount: number | null
  granularity: TimeHistogramGranularity | null
}

export type HistogramSelectionRange = {
  lower: number
  upper: number
}

export type ScatterPlotSelectionBounds = {
  x: HistogramSelectionRange
  y: HistogramSelectionRange
}

export type ScatterPlotLegendSelection = {
  field: 'shape' | 'size' | 'color'
  value: string | number | boolean
}

export type PieChartSelectionValue = string | number | boolean

export type PieChartDisplaySlice = {
  key: string
  label: string
  count: number
  share: number
  color: string
  rawValues: PieChartSelectionValue[]
  isMerged: boolean
}

export type AssetChartTheme = {
  axisDomainColor: string
  axisLabelColor: string
  axisTitleColor: string
  gridColor: string
  legendLabelColor: string
  legendTitleColor: string
  selectionColor: string
  fallbackPointColor: string
}

export type DatavizAxisScale = 'lin' | 'log'
export type ScatterPlotShapeStyle = 'outline' | 'filled'

export type ChartAxisOverrides = {
  labelSize: string
  label: string
  hideLabel: boolean
  tickCount: string
  tickSize: string
  showGridLines: boolean
  scale: DatavizAxisScale
}

export type ChartTitleOverrides = {
  size: string
  text: string
  hideTitle: boolean
  position: 'top' | 'bottom'
}

export type SharedChartOverrides = {
  xAxis: ChartAxisOverrides
  yAxis: ChartAxisOverrides
  title: ChartTitleOverrides
}

export type HistogramChartOverrides = SharedChartOverrides & {
  barWidth: number
  borderThickness: string
}

export type BarChartGroupMode = 'grouped' | 'stacked'

export type BarChartChartOverrides = HistogramChartOverrides & {
  groupMode: BarChartGroupMode
  groupNormalize: boolean
  groupSpacing: number
}

export type ScatterPlotChartOverrides = SharedChartOverrides & {
  minPointSize: string
  maxPointSize: string
  sizeScaling: number
  showLegend: boolean
  shapeStyle: ScatterPlotShapeStyle
}

export type PieChartChartOverrides = {
  innerRadius: string
  labelSize: string
  labelThreshold: string
  labelPosition: number
  mergeThreshold: string
  borderThickness: string
  mergedCategoryLabel: string
  showMergedCategory: boolean
  showPercentages: boolean
  title: ChartTitleOverrides
}

export type AssetPanelInfo = {
  panelId: string
  assetName: string
  assetTitle: string | null
  createdLabel: string
  runtimeType: string
}

export type AssetPanelFrameVariant = 'card' | 'inline'

export type PersistedAssetPanelState = {
  modifier_overrides: Record<string, unknown>
  override_schema_hash: string | null
  panel_height?: number | null
}

export type AssetPanelPrepareTarget = {
  nodeId: string
  assetName: string
  panelContext?: Record<string, unknown> | null
}

export type AssetPanelProps = {
  panelId?: string
  nodeId: string
  asset: AssetRecord
  prepareTarget?: AssetPanelPrepareTarget
  viewerMode?: 'notebook' | 'dashboard'
  persistedState?: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
  onReadyStateChange?: (ready: boolean) => void
  panelHeight?: number | null
  onPanelHeightChange?: (height: number) => void
  sectionId?: string
  frameVariant?: AssetPanelFrameVariant
}

export type SimpleAssetPanelProps = {
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  viewerMode?: 'notebook' | 'dashboard'
  onReadyStateChange?: (ready: boolean) => void
  sectionId?: string
  frameVariant?: AssetPanelFrameVariant
}

export type InteractiveAssetPanelProps = {
  nodeId: string
  asset: AssetRecord
  prepareTarget?: AssetPanelPrepareTarget
  viewerMode?: 'notebook' | 'dashboard'
  panelInfo: AssetPanelInfo
  persistedState: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
  onReadyStateChange?: (ready: boolean) => void
  sectionId?: string
  frameVariant?: AssetPanelFrameVariant
}

export type DatavizAssetPanelProps = InteractiveAssetPanelProps & {
  panelHeight: number | null
  onPanelHeightChange?: (height: number) => void
}
