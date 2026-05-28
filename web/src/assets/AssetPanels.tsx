import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import embed, { type Result as VegaEmbedResult, type VisualizationSpec } from 'vega-embed'

import { prepareAsset } from '../lib/api'
import { formatTimestamp } from '../lib/helpers'
import type {
  AssetFilter,
  AssetFilterKind,
  AssetRecord,
  PreparedPieChartPayload,
  AssetSort,
  PreparedHistogramPayload,
  PreparedScatterPlotPayload,
  PreparedTablePayload,
} from '../lib/types'
import { ChevronDown, ChevronUp, ChevronsUpDown, Cog, Funnel } from '../components/Icons'
import { SimpleMarkdown } from '../components/SimpleMarkdown'

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const
const DEFAULT_TABLE_PAGE_SIZE = 25
const DEFAULT_DATAVIZ_TABLE_PAGE_SIZE = 10
const DEFAULT_HISTOGRAM_CHART_HEIGHT = 600
const DEFAULT_PIE_CHART_HEIGHT = 600
const DEFAULT_SCATTER_PLOT_CHART_HEIGHT = 600
const MIN_DATAVIZ_CHART_HEIGHT = 240
const MAX_DATAVIZ_CHART_HEIGHT = 960
const HISTOGRAM_BRUSH_SIGNAL_NAME = 'brush_selection_adjusted_start'

type ModifierColumn = {
  id: string
  title: string
  dataType: string
  filterKinds: AssetFilterKind[]
}

type TableState = {
  page: {
    index: number
    size: number
  }
  sort: AssetSort | null
  filters: AssetFilter[]
}

type HistogramState = TableState & {
  binCount: number
}

type HistogramSelectionRange = {
  lower: number
  upper: number
}

type ScatterPlotSelectionBounds = {
  x: HistogramSelectionRange
  y: HistogramSelectionRange
}

type ScatterPlotLegendSelection = {
  field: 'shape' | 'size' | 'color'
  value: string | number | boolean
}

type PieChartSelectionValue = string | number | boolean

type PieChartDisplaySlice = {
  key: string
  label: string
  count: number
  share: number
  color: string
  rawValues: PieChartSelectionValue[]
  isMerged: boolean
}

type AssetChartTheme = {
  axisDomainColor: string
  axisLabelColor: string
  axisTitleColor: string
  gridColor: string
  legendLabelColor: string
  legendTitleColor: string
  selectionColor: string
  fallbackPointColor: string
}

type DatavizAxisScale = 'lin' | 'log'
type ScatterPlotShapeStyle = 'outline' | 'filled'
type ChartAxisOverrides = {
  labelSize: string
  label: string
  hideLabel: boolean
  tickCount: string
  tickSize: string
  showGridLines: boolean
  scale: DatavizAxisScale
}

type ChartTitleOverrides = {
  size: string
  text: string
  hideTitle: boolean
  position: 'top' | 'bottom'
}

type SharedChartOverrides = {
  xAxis: ChartAxisOverrides
  yAxis: ChartAxisOverrides
  title: ChartTitleOverrides
}

type HistogramChartOverrides = SharedChartOverrides & {
  barWidth: number
  borderThickness: string
}

type ScatterPlotChartOverrides = SharedChartOverrides & {
  minPointSize: string
  maxPointSize: string
  showLegend: boolean
  shapeStyle: ScatterPlotShapeStyle
}

type PieChartChartOverrides = {
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

type AssetPanelInfo = {
  panelId: string
  assetName: string
  assetTitle: string | null
  createdLabel: string
  runtimeType: string
}

export type PersistedAssetPanelState = {
  modifier_overrides: Record<string, unknown>
  override_schema_hash: string | null
}

export function AssetPanel({
  panelId,
  nodeId,
  asset,
  persistedState,
  onPersistedStateChange,
  panelHeight,
  onPanelHeightChange,
  sectionId,
}: {
  panelId?: string
  nodeId: string
  asset: AssetRecord
  persistedState?: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
  panelHeight?: number | null
  onPanelHeightChange?: (height: number) => void
  sectionId?: string
}) {
  const createdLabel = asset.created_at ? formatTimestamp(asset.created_at) : 'Not produced yet'
  const runtimeType = asset.asset_type ?? asset.declared_asset_type ?? 'unknown'
  const resolvedPanelId = panelId ?? `${nodeId}/${asset.asset_name}`
  const panelInfo = {
    panelId: resolvedPanelId,
    assetName: asset.asset_name,
    assetTitle: asset.title,
    createdLabel,
    runtimeType,
  }
  const frameProps = {
    asset,
    panelInfo,
    sectionId,
  }

  if (asset.current_asset_version_id === null) {
    return (
      <AssetPanelFrame {...frameProps}>
        <div className="asset-panel-placeholder">
          <p>This asset is declared but has not been produced yet.</p>
        </div>
      </AssetPanelFrame>
    )
  }

  if (asset.asset_type === 'markdown') {
    return <MarkdownAssetPanel asset={asset} panelInfo={panelInfo} sectionId={sectionId} />
  }

  if (asset.asset_type === 'dataframe') {
    return (
      <DataFrameAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        sectionId={sectionId}
      />
    )
  }

  if (asset.asset_type === 'histogram') {
    return (
      <HistogramAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        sectionId={sectionId}
      />
    )
  }

  if (asset.asset_type === 'pie_chart') {
    return (
      <PieChartAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        sectionId={sectionId}
      />
    )
  }

  if (asset.asset_type === 'scatter_plot') {
    return (
      <ScatterPlotAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        sectionId={sectionId}
      />
    )
  }

  return (
    <AssetPanelFrame {...frameProps}>
      <div className="asset-panel-placeholder">
        <p>Asset type <code>{runtimeType}</code> is not supported by this viewer yet.</p>
      </div>
    </AssetPanelFrame>
  )
}

function MarkdownAssetPanel({
  asset,
  panelInfo,
  sectionId,
}: {
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  sectionId?: string
}) {
  const markdownText = typeof asset.definition?.markdown_text === 'string' ? asset.definition.markdown_text : null

  if (!markdownText) {
    return (
      <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId}>
        <div className="asset-panel-placeholder">
          <p>This Markdown asset is missing its text payload.</p>
        </div>
      </AssetPanelFrame>
    )
  }

  return (
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId}>
      <div className="asset-markdown-panel">
        <SimpleMarkdown text={markdownText} />
      </div>
    </AssetPanelFrame>
  )
}

function DataFrameAssetPanel({
  nodeId,
  asset,
  panelInfo,
  persistedState,
  onPersistedStateChange,
  sectionId,
}: {
  nodeId: string
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  persistedState: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
  sectionId?: string
}) {
  const modifierColumns = useMemo(() => modifierColumnsFromSchema(asset.modifier_schema), [asset.modifier_schema])
  const persistedOverrideKey = useMemo(
    () => stableValueKey(persistedState?.modifier_overrides ?? {}),
    [persistedState?.modifier_overrides],
  )
  const initialTableState = useMemo(
    () => initialTableStateFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}),
    [asset.default_modifiers, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const [pageIndex, setPageIndex] = useState(initialTableState.page.index)
  const [pageSize, setPageSize] = useState(initialTableState.page.size)
  const [sort, setSort] = useState<AssetSort | null>(initialTableState.sort)
  const [filters, setFilters] = useState<AssetFilter[]>(initialTableState.filters)
  const [pageInput, setPageInput] = useState(String(initialTableState.page.index + 1))
  const overrideIncompatible = Boolean(
    persistedState
    && persistedState.override_schema_hash !== null
    && asset.override_schema_hash !== null
    && persistedState.override_schema_hash !== asset.override_schema_hash,
  )
  const isApplyingPersistedStateRef = useRef(false)
  const filtersKey = JSON.stringify(filters)
  const externalStateKey = useMemo(
    () => tableStateKey(initialTableState),
    [initialTableState.filters, initialTableState.page.index, initialTableState.page.size, initialTableState.sort?.column, initialTableState.sort?.direction],
  )
  const localStateKey = tableStateKey({
    page: { index: pageIndex, size: pageSize },
    sort,
    filters,
  })

  useEffect(() => {
    if (localStateKey === externalStateKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setPageIndex(initialTableState.page.index)
    setPageSize(initialTableState.page.size)
    setSort(initialTableState.sort)
    setFilters(initialTableState.filters)
    setPageInput(String(initialTableState.page.index + 1))
  }, [asset.current_asset_version_id, externalStateKey])

  useEffect(() => {
    if (localStateKey === externalStateKey) {
      isApplyingPersistedStateRef.current = false
    }
  }, [externalStateKey, localStateKey])

  useEffect(() => {
    if (overrideIncompatible) {
      return
    }
    if (isApplyingPersistedStateRef.current) {
      return
    }
    const modifierOverrides = buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
    }, asset.default_modifiers)
    const nextState = {
      modifier_overrides: modifierOverrides,
      override_schema_hash: asset.override_schema_hash,
    }
    if (
      persistedState
      && persistedState.override_schema_hash === nextState.override_schema_hash
      && stableValueKey(persistedState.modifier_overrides) === stableValueKey(nextState.modifier_overrides)
    ) {
      return
    }
    onPersistedStateChange?.(nextState)
  }, [asset.override_schema_hash, filters, onPersistedStateChange, overrideIncompatible, pageIndex, pageSize, persistedState, sort])

  const prepareQuery = useQuery({
    queryKey: [
      'asset-prepare',
      nodeId,
      asset.asset_name,
      asset.current_asset_version_id,
      pageIndex,
      pageSize,
      sort?.column ?? null,
      sort?.direction ?? null,
      filtersKey,
    ],
    queryFn: () => prepareAsset(nodeId, asset.asset_name, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: {
        page: { index: pageIndex, size: pageSize },
        sort: sort ? [sort] : [],
        filters,
      },
      transient_modifiers: {},
    }),
    enabled: asset.current_asset_version_id !== null && !overrideIncompatible,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const table = response?.payloads.table ?? null
  const resolvedPage = table?.page ?? { index: pageIndex, size: pageSize }
  const resolvedSort = table?.sort?.[0] ?? null
  const resolvedFilters = Array.isArray(response?.resolved_modifiers.filters) ? response.resolved_modifiers.filters : filters
  const availableColumns = modifierColumns.length
    ? modifierColumns
    : (table?.columns ?? []).map((column) => ({
      id: column.id,
      title: column.title,
      dataType: column.data_type,
      filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
    }))
  const totalRows = table?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const pageCount = Math.max(1, Math.ceil(totalRows / Math.max(resolvedPage.size, 1)))
  const canGoPrevious = resolvedPage.index > 0
  const canGoNext = resolvedPage.index + 1 < pageCount

  useEffect(() => {
    setPageInput(String(resolvedPage.index + 1))
  }, [resolvedPage.index])

  function commitPageInput() {
    const parsed = Number(pageInput.trim())
    if (!Number.isInteger(parsed)) {
      setPageInput(String(resolvedPage.index + 1))
      return
    }
    const clampedIndex = Math.min(Math.max(parsed - 1, 0), pageCount - 1)
    setPageIndex(clampedIndex)
    setPageInput(String(clampedIndex + 1))
  }

  function handleResetOverrides() {
    const resetState = initialTableStateFromModifiers(asset.default_modifiers, {})
    setPageIndex(resetState.page.index)
    setPageSize(resetState.page.size)
    setSort(resetState.sort)
    setFilters(resetState.filters)
    setPageInput(String(resetState.page.index + 1))
    onPersistedStateChange?.({
      modifier_overrides: {},
      override_schema_hash: asset.override_schema_hash,
    })
  }

  return (
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId}>
      <div className="asset-dataframe-panel">
      {overrideIncompatible ? (
        <div className="asset-panel-inline-notice error">
          <p>Saved panel overrides are no longer compatible with the current asset schema.</p>
          {onPersistedStateChange ? (
            <button type="button" className="secondary asset-inline-action" onClick={handleResetOverrides}>
              Reset panel overrides
            </button>
          ) : null}
        </div>
      ) : null}

      {response?.errors.length ? (
        <div className="asset-panel-inline-notice">
          {response.errors.map((error) => (
            <p key={error.code}>{error.message}</p>
          ))}
        </div>
      ) : null}

      {prepareQuery.isLoading && !table ? <div className="asset-panel-placeholder"><p>Preparing table view...</p></div> : null}

      {prepareQuery.isError ? (
        <div className="asset-panel-placeholder error">
          <p>{prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the table view.'}</p>
        </div>
      ) : null}

      {table ? (
        <div className={`asset-dataframe-shell${prepareQuery.isFetching ? ' is-refreshing' : ''}`}>
          <PreparedTable
            table={table}
            columns={availableColumns}
            activeSort={resolvedSort}
            activeFilters={resolvedFilters}
            disabled={overrideIncompatible || prepareQuery.isFetching}
            onToggleSort={(column) => {
              setPageIndex(0)
              setSort((current) => nextSortForColumn(current, column))
            }}
            onApplyFilter={(filter) => {
              setPageIndex(0)
              setFilters((current) => upsertFilter(current, filter))
            }}
            onRemoveFilter={(columnId) => {
              setPageIndex(0)
              setFilters((current) => removeFilter(current, columnId))
            }}
          />
          <div className="asset-dataframe-toolbar">
            <div className="asset-dataframe-stats">{formatCount(totalRows)} rows x {formatCount(columnCount)} cols</div>
            <div className="asset-dataframe-controls">
              <select
                className="asset-page-size-select"
                value={resolvedPage.size}
                onChange={(event) => {
                  setPageSize(Number(event.target.value))
                  setPageIndex(0)
                }}
                aria-label="Rows per page"
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>{size} / page</option>
                ))}
              </select>
              <div className="asset-dataframe-pagination">
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(0)}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<<'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<'}
                </button>
                <input
                  className="asset-page-input"
                  value={pageInput}
                  inputMode="numeric"
                  aria-label="Page number"
                  onChange={(event) => setPageInput(event.target.value.replace(/[^0-9]/g, ''))}
                  onBlur={commitPageInput}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      commitPageInput()
                    }
                    if (event.key === 'Escape') {
                      event.preventDefault()
                      setPageInput(String(resolvedPage.index + 1))
                    }
                  }}
                />
                <span className="asset-page-count-label">/ {pageCount}</span>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.min(pageCount - 1, current + 1))}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(pageCount - 1)}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>>'}
                </button>
              </div>
            </div>
          </div>
          {prepareQuery.isFetching ? (
            <div className="asset-dataframe-loading-overlay" aria-hidden="true">
              <div className="asset-dataframe-loading-spinner" />
            </div>
          ) : null}
        </div>
      ) : null}
      </div>
    </AssetPanelFrame>
  )
}

function HistogramAssetPanel({
  nodeId,
  asset,
  panelInfo,
  persistedState,
  onPersistedStateChange,
  panelHeight,
  onPanelHeightChange,
  sectionId,
}: {
  nodeId: string
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  persistedState: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
  panelHeight: number | null
  onPanelHeightChange?: (height: number) => void
  sectionId?: string
}) {
  const modifierColumns = useMemo(() => modifierColumnsFromSchema(asset.modifier_schema), [asset.modifier_schema])
  const chartOverrideDefaults = useMemo(
    () => defaultHistogramChartOverrides(asset.default_modifiers, asset.modifier_schema),
    [asset.default_modifiers, asset.modifier_schema],
  )
  const persistedOverrideKey = useMemo(
    () => stableValueKey(persistedState?.modifier_overrides ?? {}),
    [persistedState?.modifier_overrides],
  )
  const initialState = useMemo(
    () => initialHistogramStateFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}),
    [asset.default_modifiers, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const initialChartOverrides = useMemo(
    () => histogramChartOverridesFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}, asset.modifier_schema),
    [asset.default_modifiers, asset.modifier_schema, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const [pageIndex, setPageIndex] = useState(initialState.page.index)
  const [pageSize, setPageSize] = useState(initialState.page.size)
  const [sort, setSort] = useState<AssetSort | null>(initialState.sort)
  const [filters, setFilters] = useState<AssetFilter[]>(initialState.filters)
  const [binCount, setBinCount] = useState(initialState.binCount)
  const [binCountInput, setBinCountInput] = useState(String(initialState.binCount))
  const [chartOverrides, setChartOverrides] = useState<HistogramChartOverrides>(initialChartOverrides)
  const [selectedBarIndexes, setSelectedBarIndexes] = useState<number[]>([])
  const [pageInput, setPageInput] = useState(String(initialState.page.index + 1))
  const currentHistogramRef = useRef<PreparedHistogramPayload | null>(null)
  const overrideIncompatible = Boolean(
    persistedState
    && persistedState.override_schema_hash !== null
    && asset.override_schema_hash !== null
    && persistedState.override_schema_hash !== asset.override_schema_hash,
  )
  const isApplyingPersistedStateRef = useRef(false)
  const filtersKey = JSON.stringify(filters)
  const selectionKey = selectedBarIndexes.join(',')
  const externalStateKey = useMemo(
    () => histogramStateKey(initialState),
    [initialState.binCount, initialState.filters, initialState.page.index, initialState.page.size, initialState.sort?.column, initialState.sort?.direction],
  )
  const externalChartOverridesKey = useMemo(() => stableValueKey(initialChartOverrides), [initialChartOverrides])
  const localStateKey = histogramStateKey({
    page: { index: pageIndex, size: pageSize },
    sort,
    filters,
    binCount,
  })
  const localChartOverridesKey = stableValueKey(chartOverrides)

  useEffect(() => {
    if (localStateKey === externalStateKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setPageIndex(initialState.page.index)
    setPageSize(initialState.page.size)
    setSort(initialState.sort)
    setFilters(initialState.filters)
    setBinCount(initialState.binCount)
    setBinCountInput(String(initialState.binCount))
    setSelectedBarIndexes([])
    setPageInput(String(initialState.page.index + 1))
  }, [asset.current_asset_version_id, externalStateKey])

  useEffect(() => {
    if (localChartOverridesKey === externalChartOverridesKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setChartOverrides(initialChartOverrides)
  }, [asset.current_asset_version_id, chartOverrideDefaults, externalChartOverridesKey])

  useEffect(() => {
    if (localStateKey === externalStateKey && localChartOverridesKey === externalChartOverridesKey) {
      isApplyingPersistedStateRef.current = false
    }
  }, [externalChartOverridesKey, externalStateKey, localChartOverridesKey, localStateKey])

  useEffect(() => {
    if (overrideIncompatible) {
      return
    }
    if (isApplyingPersistedStateRef.current) {
      return
    }
    const modifierOverrides = buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
      bin_count: binCount,
      ...serializeHistogramChartModifierValues(chartOverrides),
    }, asset.default_modifiers)
    const nextState = {
      modifier_overrides: modifierOverrides,
      override_schema_hash: asset.override_schema_hash,
    }
    if (
      persistedState
      && persistedState.override_schema_hash === nextState.override_schema_hash
      && stableValueKey(persistedState.modifier_overrides) === stableValueKey(nextState.modifier_overrides)
    ) {
      return
    }
    onPersistedStateChange?.(nextState)
  }, [asset.override_schema_hash, binCount, chartOverrides, filters, onPersistedStateChange, overrideIncompatible, pageIndex, pageSize, persistedState, sort])

  useEffect(() => {
    setSelectedBarIndexes([])
  }, [asset.current_asset_version_id, binCount, filtersKey])

  const prepareQuery = useQuery({
    queryKey: [
      'asset-prepare',
      nodeId,
      asset.asset_name,
      asset.current_asset_version_id,
      pageIndex,
      pageSize,
      sort?.column ?? null,
      sort?.direction ?? null,
      filtersKey,
      binCount,
      selectionKey,
    ],
    queryFn: () => prepareAsset(nodeId, asset.asset_name, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: {
        page: { index: pageIndex, size: pageSize },
        sort: sort ? [sort] : [],
        filters,
        bin_count: binCount,
      },
      transient_modifiers: currentHistogramRef.current && selectedBarIndexes.length ? {
        selection_ranges: histogramSelectionRangesFromIndexes(currentHistogramRef.current, selectedBarIndexes),
      } : {},
    }),
    enabled: asset.current_asset_version_id !== null && !overrideIncompatible,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const mainPayload = response?.payloads.main ?? null
  const histogram = mainPayload?.kind === 'histogram' ? mainPayload : null
  currentHistogramRef.current = histogram
  const table = response?.payloads.table ?? null
  const resolvedPage = table?.page ?? { index: pageIndex, size: pageSize }
  const resolvedSort = table?.sort?.[0] ?? null
  const resolvedFilters = Array.isArray(response?.resolved_modifiers.filters) ? response.resolved_modifiers.filters : filters
  const resolvedBinCount = typeof response?.resolved_modifiers.bin_count === 'number' ? response.resolved_modifiers.bin_count : binCount
  const availableColumns = modifierColumns.length
    ? modifierColumns
    : (table?.columns ?? []).map((column) => ({
      id: column.id,
      title: column.title,
      dataType: column.data_type,
      filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
    }))
  const totalRows = histogram?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const linkedRows = table?.rows_total ?? totalRows
  const pageCount = Math.max(1, Math.ceil(linkedRows / Math.max(resolvedPage.size, 1)))
  const canGoPrevious = resolvedPage.index > 0
  const canGoNext = resolvedPage.index + 1 < pageCount
  const resolvedPanelHeight = normalizePanelHeight(panelHeight) ?? DEFAULT_HISTOGRAM_CHART_HEIGHT
  const defaultBinCount = binCountFromValue(modifierDefaultValue(asset.default_modifiers, asset.modifier_schema, 'bin_count')) ?? initialState.binCount
  const hasSettingsOverrides = Object.keys(buildModifierOverridesRecord({
    bin_count: binCount,
    ...serializeHistogramChartModifierValues(chartOverrides),
  }, asset.default_modifiers)).length > 0

  useEffect(() => {
    setPageInput(String(resolvedPage.index + 1))
  }, [resolvedPage.index])

  useEffect(() => {
    setBinCountInput(String(resolvedBinCount))
  }, [resolvedBinCount])

  function commitPageInput() {
    const parsed = Number(pageInput.trim())
    if (!Number.isInteger(parsed)) {
      setPageInput(String(resolvedPage.index + 1))
      return
    }
    const clampedIndex = Math.min(Math.max(parsed - 1, 0), pageCount - 1)
    setPageIndex(clampedIndex)
    setPageInput(String(clampedIndex + 1))
  }

  function commitBinCountInput() {
    const parsed = Number(binCountInput.trim())
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 100) {
      setBinCountInput(String(resolvedBinCount))
      return
    }
    setPageIndex(0)
    setBinCount(parsed)
    setBinCountInput(String(parsed))
  }

  function handleResetOverrides() {
    const resetState = initialHistogramStateFromModifiers(asset.default_modifiers, {})
    setPageIndex(resetState.page.index)
    setPageSize(resetState.page.size)
    setSort(resetState.sort)
    setFilters(resetState.filters)
    setBinCount(resetState.binCount)
    setBinCountInput(String(resetState.binCount))
    setChartOverrides(chartOverrideDefaults)
    setSelectedBarIndexes([])
    setPageInput(String(resetState.page.index + 1))
    onPersistedStateChange?.({
      modifier_overrides: {},
      override_schema_hash: asset.override_schema_hash,
    })
  }

  function handleResetSettingsOverrides() {
    setPageIndex(0)
    setBinCount(defaultBinCount)
    setBinCountInput(String(defaultBinCount))
    setChartOverrides(chartOverrideDefaults)
  }

  const settingsBody = (
    <>
      <div className="asset-dataviz-settings-actions">
        <button type="button" className="secondary asset-dataviz-settings-reset" onClick={handleResetSettingsOverrides} disabled={!hasSettingsOverrides}>
          Reset to default
        </button>
      </div>

      <PanelSettingsSection title="Histogram">
        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(binCount, defaultBinCount))}>{modifierTitle(asset.modifier_schema, 'bin_count', 'Bin count')}</span>
          <input
            value={binCountInput}
            inputMode="numeric"
            aria-label="Histogram bin count"
            onChange={(event) => setBinCountInput(event.target.value.replace(/[^0-9]/g, ''))}
            onBlur={commitBinCountInput}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commitBinCountInput()
              }
              if (event.key === 'Escape') {
                event.preventDefault()
                setBinCountInput(String(resolvedBinCount))
              }
            }}
            disabled={overrideIncompatible || prepareQuery.isFetching}
          />
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.barWidth, chartOverrideDefaults.barWidth))}>{modifierTitle(asset.modifier_schema, 'bar_width', 'Bar width')}</span>
          <div className="asset-dataviz-slider-field">
            <input
              type="range"
              min={0}
              max={100}
              value={chartOverrides.barWidth}
              onChange={(event) => setChartOverrides((current) => ({
                ...current,
                barWidth: clampPercentage(Number(event.target.value), current.barWidth),
              }))}
            />
            <strong>{chartOverrides.barWidth}%</strong>
          </div>
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.borderThickness, chartOverrideDefaults.borderThickness))}>{modifierTitle(asset.modifier_schema, 'border_thickness', 'Border thickness')}</span>
          <DeferredModifierInput
            displayValue={chartOverrides.borderThickness}
            inputMode="decimal"
            isValid={(value) => optionalNumberFromInput(value) !== undefined}
            fallbackValue={chartOverrideDefaults.borderThickness}
            onValidChange={(nextValue) => setChartOverrides((current) => ({
              ...current,
              borderThickness: nextValue,
            }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({
              ...current,
              borderThickness: nextValue,
            }))}
          />
        </label>
      </PanelSettingsSection>

      <AxisOverridesSection
        title={modifierTitle(asset.modifier_schema, 'x_axis', 'X axis')}
        overrides={chartOverrides.xAxis}
        defaultOverrides={chartOverrideDefaults.xAxis}
        defaultLabel={chartOverrideDefaults.xAxis.label}
        onChange={(next) => setChartOverrides((current) => ({ ...current, xAxis: next }))}
      />

      <AxisOverridesSection
        title={modifierTitle(asset.modifier_schema, 'y_axis', 'Y axis')}
        overrides={chartOverrides.yAxis}
        defaultOverrides={chartOverrideDefaults.yAxis}
        defaultLabel={chartOverrideDefaults.yAxis.label}
        onChange={(next) => setChartOverrides((current) => ({ ...current, yAxis: next }))}
      />

      <TitleOverridesSection
        title={modifierTitle(asset.modifier_schema, 'title', 'Title')}
        overrides={chartOverrides.title}
        defaultOverrides={chartOverrideDefaults.title}
        defaultText={chartOverrideDefaults.title.text}
        onChange={(next) => setChartOverrides((current) => ({ ...current, title: next }))}
      />
    </>
  )

  return (
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} settingsTitle="Modifier overrides" settingsBody={settingsBody} settingsActive={hasSettingsOverrides} sectionId={sectionId}>
      <div className="asset-dataframe-panel asset-histogram-panel">
      {overrideIncompatible ? (
        <div className="asset-panel-inline-notice error">
          <p>Saved panel overrides are no longer compatible with the current asset schema.</p>
          {onPersistedStateChange ? (
            <button type="button" className="secondary asset-inline-action" onClick={handleResetOverrides}>
              Reset panel overrides
            </button>
          ) : null}
        </div>
      ) : null}

      {response?.errors.length ? (
        <div className="asset-panel-inline-notice">
          {response.errors.map((error) => (
            <p key={error.code}>{error.message}</p>
          ))}
        </div>
      ) : null}

      <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange}>
        {(chartHeight) => (
          <>
            {prepareQuery.isLoading && !histogram ? <div className="asset-panel-placeholder"><p>Preparing histogram view...</p></div> : null}

            {prepareQuery.isError ? (
              <div className="asset-panel-placeholder error">
                <p>{prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the histogram view.'}</p>
              </div>
            ) : null}

            {histogram ? (
              <HistogramChart
                histogram={histogram}
                chartHeight={chartHeight}
                overrides={chartOverrides}
                defaultOverrides={chartOverrideDefaults}
                selectedBarIndexes={selectedBarIndexes}
                onSelectionChange={(nextIndexes) => {
                  setPageIndex(0)
                  setSelectedBarIndexes(nextIndexes)
                }}
              />
            ) : null}
          </>
        )}
      </ResizableDatavizContent>

      {table ? (
        <div className={`asset-dataframe-shell${prepareQuery.isFetching ? ' is-refreshing' : ''}`}>
          <PreparedTable
            table={table}
            columns={availableColumns}
            activeSort={resolvedSort}
            activeFilters={resolvedFilters}
            disabled={overrideIncompatible || prepareQuery.isFetching}
            onToggleSort={(column) => {
              setPageIndex(0)
              setSort((current) => nextSortForColumn(current, column))
            }}
            onApplyFilter={(filter) => {
              setPageIndex(0)
              setFilters((current) => upsertFilter(current, filter))
            }}
            onRemoveFilter={(columnId) => {
              setPageIndex(0)
              setFilters((current) => removeFilter(current, columnId))
            }}
          />
          <div className="asset-dataframe-toolbar">
            <div className="asset-dataframe-stats">{formatCount(linkedRows)} linked rows x {formatCount(columnCount)} cols</div>
            <div className="asset-dataframe-controls">
              <select
                className="asset-page-size-select"
                value={resolvedPage.size}
                onChange={(event) => {
                  setPageSize(Number(event.target.value))
                  setPageIndex(0)
                }}
                aria-label="Rows per page"
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>{size} / page</option>
                ))}
              </select>
              <div className="asset-dataframe-pagination">
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(0)}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<<'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<'}
                </button>
                <input
                  className="asset-page-input"
                  value={pageInput}
                  inputMode="numeric"
                  aria-label="Page number"
                  onChange={(event) => setPageInput(event.target.value.replace(/[^0-9]/g, ''))}
                  onBlur={commitPageInput}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      commitPageInput()
                    }
                    if (event.key === 'Escape') {
                      event.preventDefault()
                      setPageInput(String(resolvedPage.index + 1))
                    }
                  }}
                />
                <span className="asset-page-count-label">/ {pageCount}</span>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.min(pageCount - 1, current + 1))}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(pageCount - 1)}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>>'}
                </button>
              </div>
            </div>
          </div>
          {prepareQuery.isFetching ? (
            <div className="asset-dataframe-loading-overlay" aria-hidden="true">
              <div className="asset-dataframe-loading-spinner" />
            </div>
          ) : null}
        </div>
      ) : null}
      </div>
    </AssetPanelFrame>
  )
}

function PieChartAssetPanel({
  nodeId,
  asset,
  panelInfo,
  persistedState,
  onPersistedStateChange,
  panelHeight,
  onPanelHeightChange,
  sectionId,
}: {
  nodeId: string
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  persistedState: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
  panelHeight: number | null
  onPanelHeightChange?: (height: number) => void
  sectionId?: string
}) {
  const modifierColumns = useMemo(() => modifierColumnsFromSchema(asset.modifier_schema), [asset.modifier_schema])
  const chartOverrideDefaults = useMemo(
    () => defaultPieChartOverrides(asset.default_modifiers, asset.modifier_schema),
    [asset.default_modifiers, asset.modifier_schema],
  )
  const persistedOverrideKey = useMemo(
    () => stableValueKey(persistedState?.modifier_overrides ?? {}),
    [persistedState?.modifier_overrides],
  )
  const initialTableState = useMemo(
    () => initialTableStateFromModifiers(
      asset.default_modifiers,
      persistedState?.modifier_overrides ?? {},
      DEFAULT_DATAVIZ_TABLE_PAGE_SIZE,
    ),
    [asset.default_modifiers, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const initialChartOverrides = useMemo(
    () => pieChartOverridesFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}, asset.modifier_schema),
    [asset.default_modifiers, asset.modifier_schema, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const [pageIndex, setPageIndex] = useState(initialTableState.page.index)
  const [pageSize, setPageSize] = useState(initialTableState.page.size)
  const [sort, setSort] = useState<AssetSort | null>(initialTableState.sort)
  const [filters, setFilters] = useState<AssetFilter[]>(initialTableState.filters)
  const [chartOverrides, setChartOverrides] = useState<PieChartChartOverrides>(initialChartOverrides)
  const [selectedCategories, setSelectedCategories] = useState<PieChartSelectionValue[]>([])
  const [pageInput, setPageInput] = useState(String(initialTableState.page.index + 1))
  const overrideIncompatible = Boolean(
    persistedState
    && persistedState.override_schema_hash !== null
    && asset.override_schema_hash !== null
    && persistedState.override_schema_hash !== asset.override_schema_hash,
  )
  const isApplyingPersistedStateRef = useRef(false)
  const filtersKey = JSON.stringify(filters)
  const selectionKey = stableValueKey(selectedCategories)
  const externalStateKey = useMemo(
    () => tableStateKey(initialTableState),
    [initialTableState.filters, initialTableState.page.index, initialTableState.page.size, initialTableState.sort?.column, initialTableState.sort?.direction],
  )
  const externalChartOverridesKey = useMemo(() => stableValueKey(initialChartOverrides), [initialChartOverrides])
  const localStateKey = tableStateKey({
    page: { index: pageIndex, size: pageSize },
    sort,
    filters,
  })
  const localChartOverridesKey = stableValueKey(chartOverrides)

  useEffect(() => {
    if (localStateKey === externalStateKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setPageIndex(initialTableState.page.index)
    setPageSize(initialTableState.page.size)
    setSort(initialTableState.sort)
    setFilters(initialTableState.filters)
    setSelectedCategories([])
    setPageInput(String(initialTableState.page.index + 1))
  }, [asset.current_asset_version_id, externalStateKey])

  useEffect(() => {
    if (localChartOverridesKey === externalChartOverridesKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setChartOverrides(initialChartOverrides)
  }, [asset.current_asset_version_id, chartOverrideDefaults, externalChartOverridesKey])

  useEffect(() => {
    if (localStateKey === externalStateKey && localChartOverridesKey === externalChartOverridesKey) {
      isApplyingPersistedStateRef.current = false
    }
  }, [externalChartOverridesKey, externalStateKey, localChartOverridesKey, localStateKey])

  useEffect(() => {
    if (overrideIncompatible) {
      return
    }
    if (isApplyingPersistedStateRef.current) {
      return
    }
    const modifierOverrides = buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
      ...serializePieChartModifierValues(chartOverrides),
    }, asset.default_modifiers)
    const nextState = {
      modifier_overrides: modifierOverrides,
      override_schema_hash: asset.override_schema_hash,
    }
    if (
      persistedState
      && persistedState.override_schema_hash === nextState.override_schema_hash
      && stableValueKey(persistedState.modifier_overrides) === stableValueKey(nextState.modifier_overrides)
    ) {
      return
    }
    onPersistedStateChange?.(nextState)
  }, [asset.override_schema_hash, chartOverrides, filters, onPersistedStateChange, overrideIncompatible, pageIndex, pageSize, persistedState, sort])

  useEffect(() => {
    setSelectedCategories([])
  }, [asset.current_asset_version_id, filtersKey])

  const prepareQuery = useQuery({
    queryKey: [
      'asset-prepare',
      nodeId,
      asset.asset_name,
      asset.current_asset_version_id,
      pageIndex,
      pageSize,
      sort?.column ?? null,
      sort?.direction ?? null,
      filtersKey,
      selectionKey,
    ],
    queryFn: () => prepareAsset(nodeId, asset.asset_name, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: {
        page: { index: pageIndex, size: pageSize },
        sort: sort ? [sort] : [],
        filters,
      },
      transient_modifiers: selectedCategories.length ? {
        selected_categories: selectedCategories,
      } : {},
    }),
    enabled: asset.current_asset_version_id !== null && !overrideIncompatible,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const mainPayload = response?.payloads.main ?? null
  const pieChart = mainPayload?.kind === 'pie_chart' ? mainPayload : null
  const table = response?.payloads.table ?? null
  const resolvedPage = table?.page ?? { index: pageIndex, size: pageSize }
  const resolvedSort = table?.sort?.[0] ?? null
  const resolvedFilters = Array.isArray(response?.resolved_modifiers.filters) ? response.resolved_modifiers.filters : filters
  const availableColumns = modifierColumns.length
    ? modifierColumns
    : (table?.columns ?? []).map((column) => ({
      id: column.id,
      title: column.title,
      dataType: column.data_type,
      filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
    }))
  const totalRows = pieChart?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const linkedRows = table?.rows_total ?? totalRows
  const pageCount = Math.max(1, Math.ceil(linkedRows / Math.max(resolvedPage.size, 1)))
  const canGoPrevious = resolvedPage.index > 0
  const canGoNext = resolvedPage.index + 1 < pageCount
  const resolvedPanelHeight = normalizePanelHeight(panelHeight) ?? DEFAULT_PIE_CHART_HEIGHT
  const displaySlices = useMemo(
    () => preparePieChartDisplaySlices(pieChart, chartOverrides, chartOverrideDefaults),
    [chartOverrideDefaults, chartOverrides, pieChart],
  )
  const hasSettingsOverrides = Object.keys(buildModifierOverridesRecord(
    serializePieChartModifierValues(chartOverrides),
    asset.default_modifiers,
  )).length > 0

  useEffect(() => {
    setPageInput(String(resolvedPage.index + 1))
  }, [resolvedPage.index])

  useEffect(() => {
    if (pieChart === null) {
      return
    }
    const nextSelection = pieChartVisibleSelection(selectedCategories, displaySlices)
    if (!pieChartSelectionsEqual(nextSelection, selectedCategories)) {
      setSelectedCategories(nextSelection)
    }
  }, [displaySlices, pieChart, selectedCategories])

  function commitPageInput() {
    const parsed = Number(pageInput.trim())
    if (!Number.isInteger(parsed)) {
      setPageInput(String(resolvedPage.index + 1))
      return
    }
    const clampedIndex = Math.min(Math.max(parsed - 1, 0), pageCount - 1)
    setPageIndex(clampedIndex)
    setPageInput(String(clampedIndex + 1))
  }

  function handleResetOverrides() {
    const resetState = initialTableStateFromModifiers(asset.default_modifiers, {}, DEFAULT_DATAVIZ_TABLE_PAGE_SIZE)
    setPageIndex(resetState.page.index)
    setPageSize(resetState.page.size)
    setSort(resetState.sort)
    setFilters(resetState.filters)
    setChartOverrides(chartOverrideDefaults)
    setSelectedCategories([])
    setPageInput(String(resetState.page.index + 1))
    onPersistedStateChange?.({
      modifier_overrides: {},
      override_schema_hash: asset.override_schema_hash,
    })
  }

  function handleResetSettingsOverrides() {
    setChartOverrides(chartOverrideDefaults)
  }

  const settingsBody = (
    <>
      <div className="asset-dataviz-settings-actions">
        <button type="button" className="secondary asset-dataviz-settings-reset" onClick={handleResetSettingsOverrides} disabled={!hasSettingsOverrides}>
          Reset to default
        </button>
      </div>

      <PanelSettingsSection title="Pie chart">
        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.innerRadius, chartOverrideDefaults.innerRadius))}>{modifierTitle(asset.modifier_schema, 'inner_radius', 'Inner radius')}</span>
          <div className="asset-dataviz-slider-field">
            <input
              type="range"
              min={0}
              max={100}
              value={pieChartInnerRadiusPercentage(chartOverrides, chartOverrideDefaults)}
              onChange={(event) => setChartOverrides((current) => ({
                ...current,
                innerRadius: String(Number(event.target.value) / 100),
              }))}
            />
            <strong>{pieChartInnerRadiusPercentage(chartOverrides, chartOverrideDefaults)}%</strong>
          </div>
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.labelPosition, chartOverrideDefaults.labelPosition))}>{modifierTitle(asset.modifier_schema, 'label_position', 'Label position')}</span>
          <div className="asset-dataviz-slider-field">
            <input
              type="range"
              min={0}
              max={200}
              value={chartOverrides.labelPosition}
              onChange={(event) => setChartOverrides((current) => ({
                ...current,
                labelPosition: clampNumberToRange(Number(event.target.value), current.labelPosition, 0, 200, true),
              }))}
            />
            <strong>{chartOverrides.labelPosition}%</strong>
          </div>
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.labelThreshold, chartOverrideDefaults.labelThreshold))}>{modifierTitle(asset.modifier_schema, 'label_threshold', 'Label threshold')}</span>
          <div className="asset-dataviz-slider-field">
            <input
              type="range"
              min={0}
              max={100}
              value={pieChartSliderPercentage(chartOverrides.labelThreshold, chartOverrideDefaults.labelThreshold, 5)}
              onChange={(event) => setChartOverrides((current) => ({
                ...current,
                labelThreshold: event.target.value,
              }))}
            />
            <strong>{pieChartSliderPercentage(chartOverrides.labelThreshold, chartOverrideDefaults.labelThreshold, 5)}%</strong>
          </div>
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.labelSize, chartOverrideDefaults.labelSize))}>{modifierTitle(asset.modifier_schema, 'label_size', 'Label size')}</span>
          <DeferredModifierInput
            displayValue={chartOverrides.labelSize}
            inputMode="decimal"
            isValid={(value) => optionalNumberFromInput(value) !== undefined}
            fallbackValue={chartOverrideDefaults.labelSize}
            onValidChange={(nextValue) => setChartOverrides((current) => ({
              ...current,
              labelSize: nextValue,
            }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({
              ...current,
              labelSize: nextValue,
            }))}
          />
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.mergeThreshold, chartOverrideDefaults.mergeThreshold))}>{modifierTitle(asset.modifier_schema, 'merge_threshold', 'Merge threshold')}</span>
          <div className="asset-dataviz-slider-field">
            <input
              type="range"
              min={0}
              max={100}
              value={pieChartSliderPercentage(chartOverrides.mergeThreshold, chartOverrideDefaults.mergeThreshold, 0)}
              onChange={(event) => setChartOverrides((current) => ({
                ...current,
                mergeThreshold: event.target.value,
              }))}
            />
            <strong>{pieChartSliderPercentage(chartOverrides.mergeThreshold, chartOverrideDefaults.mergeThreshold, 0)}%</strong>
          </div>
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.borderThickness, chartOverrideDefaults.borderThickness))}>{modifierTitle(asset.modifier_schema, 'border_thickness', 'Border thickness')}</span>
          <DeferredModifierInput
            displayValue={chartOverrides.borderThickness}
            inputMode="decimal"
            isValid={(value) => optionalNumberFromInput(value) !== undefined}
            fallbackValue={chartOverrideDefaults.borderThickness}
            onValidChange={(nextValue) => setChartOverrides((current) => ({
              ...current,
              borderThickness: nextValue,
            }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({
              ...current,
              borderThickness: nextValue,
            }))}
          />
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.mergedCategoryLabel, chartOverrideDefaults.mergedCategoryLabel))}>{modifierTitle(asset.modifier_schema, 'merged_category_label', 'Merged category label')}</span>
          <DeferredModifierInput
            displayValue={chartOverrides.mergedCategoryLabel}
            isValid={(value) => value.trim() !== ''}
            fallbackValue={chartOverrideDefaults.mergedCategoryLabel}
            onValidChange={(nextValue) => setChartOverrides((current) => ({
              ...current,
              mergedCategoryLabel: nextValue,
            }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({
              ...current,
              mergedCategoryLabel: nextValue,
            }))}
          />
        </label>

        <label className="asset-dataviz-checkbox-field">
          <input
            type="checkbox"
            checked={chartOverrides.showMergedCategory}
            onChange={(event) => setChartOverrides((current) => ({
              ...current,
              showMergedCategory: event.target.checked,
            }))}
          />
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.showMergedCategory, chartOverrideDefaults.showMergedCategory))}>{modifierTitle(asset.modifier_schema, 'show_merged_category', 'Merged category visibility')}</span>
        </label>

        <label className="asset-dataviz-checkbox-field">
          <input
            type="checkbox"
            checked={chartOverrides.showPercentages}
            onChange={(event) => setChartOverrides((current) => ({
              ...current,
              showPercentages: event.target.checked,
            }))}
          />
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.showPercentages, chartOverrideDefaults.showPercentages))}>{modifierTitle(asset.modifier_schema, 'show_percentages', 'Show percentages')}</span>
        </label>
      </PanelSettingsSection>

      <TitleOverridesSection
        title={modifierTitle(asset.modifier_schema, 'title', 'Title')}
        overrides={chartOverrides.title}
        defaultOverrides={chartOverrideDefaults.title}
        defaultText={chartOverrideDefaults.title.text}
        onChange={(next) => setChartOverrides((current) => ({ ...current, title: next }))}
      />
    </>
  )

  return (
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} settingsTitle="Modifier overrides" settingsBody={settingsBody} settingsActive={hasSettingsOverrides} sectionId={sectionId}>
      <div className="asset-dataframe-panel asset-pie-chart-panel">
      {overrideIncompatible ? (
        <div className="asset-panel-inline-notice error">
          <p>Saved panel overrides are no longer compatible with the current asset schema.</p>
          {onPersistedStateChange ? (
            <button type="button" className="secondary asset-inline-action" onClick={handleResetOverrides}>
              Reset panel overrides
            </button>
          ) : null}
        </div>
      ) : null}

      {response?.errors.length ? (
        <div className="asset-panel-inline-notice">
          {response.errors.map((error) => (
            <p key={error.code}>{error.message}</p>
          ))}
        </div>
      ) : null}

      <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange}>
        {(chartHeight) => (
          <>
            {prepareQuery.isLoading && !pieChart ? <div className="asset-panel-placeholder"><p>Preparing pie chart view...</p></div> : null}

            {prepareQuery.isError ? (
              <div className="asset-panel-placeholder error">
                <p>{prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the pie chart view.'}</p>
              </div>
            ) : null}

            {pieChart ? (
              <PieChartChart
                pieChart={pieChart}
                chartHeight={chartHeight}
                overrides={chartOverrides}
                defaultOverrides={chartOverrideDefaults}
                selectedCategories={selectedCategories}
                onSelectionChange={(nextSelection) => {
                  setPageIndex(0)
                  setSelectedCategories(nextSelection)
                }}
              />
            ) : null}
          </>
        )}
      </ResizableDatavizContent>

      {table ? (
        <div className={`asset-dataframe-shell${prepareQuery.isFetching ? ' is-refreshing' : ''}`}>
          <PreparedTable
            table={table}
            columns={availableColumns}
            activeSort={resolvedSort}
            activeFilters={resolvedFilters}
            disabled={overrideIncompatible || prepareQuery.isFetching}
            onToggleSort={(column) => {
              setPageIndex(0)
              setSort((current) => nextSortForColumn(current, column))
            }}
            onApplyFilter={(filter) => {
              setPageIndex(0)
              setFilters((current) => upsertFilter(current, filter))
            }}
            onRemoveFilter={(columnId) => {
              setPageIndex(0)
              setFilters((current) => removeFilter(current, columnId))
            }}
          />
          <div className="asset-dataframe-toolbar">
            <div className="asset-dataframe-stats">{formatCount(linkedRows)} linked rows x {formatCount(columnCount)} cols</div>
            <div className="asset-dataframe-controls">
              <select
                className="asset-page-size-select"
                value={resolvedPage.size}
                onChange={(event) => {
                  setPageSize(Number(event.target.value))
                  setPageIndex(0)
                }}
                aria-label="Rows per page"
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>{size} / page</option>
                ))}
              </select>
              <div className="asset-dataframe-pagination">
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(0)}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<<'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<'}
                </button>
                <input
                  className="asset-page-input"
                  value={pageInput}
                  inputMode="numeric"
                  aria-label="Page number"
                  onChange={(event) => setPageInput(event.target.value.replace(/[^0-9]/g, ''))}
                  onBlur={commitPageInput}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      commitPageInput()
                    }
                    if (event.key === 'Escape') {
                      event.preventDefault()
                      setPageInput(String(resolvedPage.index + 1))
                    }
                  }}
                />
                <span className="asset-page-count-label">/ {pageCount}</span>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.min(pageCount - 1, current + 1))}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(pageCount - 1)}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>>'}
                </button>
              </div>
            </div>
          </div>
          {prepareQuery.isFetching ? (
            <div className="asset-dataframe-loading-overlay" aria-hidden="true">
              <div className="asset-dataframe-loading-spinner" />
            </div>
          ) : null}
        </div>
      ) : null}
      </div>
    </AssetPanelFrame>
  )
}

function ScatterPlotAssetPanel({
  nodeId,
  asset,
  panelInfo,
  persistedState,
  onPersistedStateChange,
  panelHeight,
  onPanelHeightChange,
  sectionId,
}: {
  nodeId: string
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  persistedState: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
  panelHeight: number | null
  onPanelHeightChange?: (height: number) => void
  sectionId?: string
}) {
  const modifierColumns = useMemo(() => modifierColumnsFromSchema(asset.modifier_schema), [asset.modifier_schema])
  const chartOverrideDefaults = useMemo(
    () => defaultScatterPlotChartOverrides(asset.default_modifiers, asset.modifier_schema),
    [asset.default_modifiers, asset.modifier_schema],
  )
  const persistedOverrideKey = useMemo(
    () => stableValueKey(persistedState?.modifier_overrides ?? {}),
    [persistedState?.modifier_overrides],
  )
  const initialTableState = useMemo(
    () => initialTableStateFromModifiers(
      asset.default_modifiers,
      persistedState?.modifier_overrides ?? {},
      DEFAULT_DATAVIZ_TABLE_PAGE_SIZE,
    ),
    [asset.default_modifiers, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const initialChartOverrides = useMemo(
    () => scatterPlotChartOverridesFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}, asset.modifier_schema),
    [asset.default_modifiers, asset.modifier_schema, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const [pageIndex, setPageIndex] = useState(initialTableState.page.index)
  const [pageSize, setPageSize] = useState(initialTableState.page.size)
  const [sort, setSort] = useState<AssetSort | null>(initialTableState.sort)
  const [filters, setFilters] = useState<AssetFilter[]>(initialTableState.filters)
  const [chartOverrides, setChartOverrides] = useState<ScatterPlotChartOverrides>(initialChartOverrides)
  const [selectedBounds, setSelectedBounds] = useState<ScatterPlotSelectionBounds | null>(null)
  const [selectedPointRowIndex, setSelectedPointRowIndex] = useState<number | null>(null)
  const [pageInput, setPageInput] = useState(String(initialTableState.page.index + 1))
  const overrideIncompatible = Boolean(
    persistedState
    && persistedState.override_schema_hash !== null
    && asset.override_schema_hash !== null
    && persistedState.override_schema_hash !== asset.override_schema_hash,
  )
  const isApplyingPersistedStateRef = useRef(false)
  const filtersKey = JSON.stringify(filters)
  const selectionKey = stableValueKey({ selectedBounds, selectedPointRowIndex })
  const externalStateKey = useMemo(
    () => tableStateKey(initialTableState),
    [initialTableState.filters, initialTableState.page.index, initialTableState.page.size, initialTableState.sort?.column, initialTableState.sort?.direction],
  )
  const externalChartOverridesKey = useMemo(() => stableValueKey(initialChartOverrides), [initialChartOverrides])
  const localStateKey = tableStateKey({
    page: { index: pageIndex, size: pageSize },
    sort,
    filters,
  })
  const localChartOverridesKey = stableValueKey(chartOverrides)

  useEffect(() => {
    if (localStateKey === externalStateKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setPageIndex(initialTableState.page.index)
    setPageSize(initialTableState.page.size)
    setSort(initialTableState.sort)
    setFilters(initialTableState.filters)
    setSelectedBounds(null)
    setSelectedPointRowIndex(null)
    setPageInput(String(initialTableState.page.index + 1))
  }, [asset.current_asset_version_id, externalStateKey])

  useEffect(() => {
    if (localChartOverridesKey === externalChartOverridesKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setChartOverrides(initialChartOverrides)
  }, [asset.current_asset_version_id, chartOverrideDefaults, externalChartOverridesKey])

  useEffect(() => {
    if (localStateKey === externalStateKey && localChartOverridesKey === externalChartOverridesKey) {
      isApplyingPersistedStateRef.current = false
    }
  }, [externalChartOverridesKey, externalStateKey, localChartOverridesKey, localStateKey])

  useEffect(() => {
    if (overrideIncompatible) {
      return
    }
    if (isApplyingPersistedStateRef.current) {
      return
    }
    const modifierOverrides = buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
      ...serializeScatterPlotChartModifierValues(chartOverrides),
    }, asset.default_modifiers)
    const nextState = {
      modifier_overrides: modifierOverrides,
      override_schema_hash: asset.override_schema_hash,
    }
    if (
      persistedState
      && persistedState.override_schema_hash === nextState.override_schema_hash
      && stableValueKey(persistedState.modifier_overrides) === stableValueKey(nextState.modifier_overrides)
    ) {
      return
    }
    onPersistedStateChange?.(nextState)
  }, [asset.override_schema_hash, chartOverrides, filters, onPersistedStateChange, overrideIncompatible, pageIndex, pageSize, persistedState, sort])

  useEffect(() => {
    setSelectedBounds(null)
    setSelectedPointRowIndex(null)
  }, [asset.current_asset_version_id, filtersKey])

  const prepareQuery = useQuery({
    queryKey: [
      'asset-prepare',
      nodeId,
      asset.asset_name,
      asset.current_asset_version_id,
      pageIndex,
      pageSize,
      sort?.column ?? null,
      sort?.direction ?? null,
      filtersKey,
      selectionKey,
    ],
    queryFn: () => prepareAsset(nodeId, asset.asset_name, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: {
        page: { index: pageIndex, size: pageSize },
        sort: sort ? [sort] : [],
        filters,
      },
      transient_modifiers: {
        ...(selectedBounds ? { selection_bounds: selectedBounds } : {}),
        ...(selectedPointRowIndex !== null ? { selected_row_index: selectedPointRowIndex } : {}),
      },
    }),
    enabled: asset.current_asset_version_id !== null && !overrideIncompatible,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const mainPayload = response?.payloads.main ?? null
  const scatterPlot = mainPayload?.kind === 'scatter_plot' ? mainPayload : null
  const table = response?.payloads.table ?? null
  const resolvedPage = table?.page ?? { index: pageIndex, size: pageSize }
  const resolvedSort = table?.sort?.[0] ?? null
  const resolvedFilters = Array.isArray(response?.resolved_modifiers.filters) ? response.resolved_modifiers.filters : filters
  const availableColumns = modifierColumns.length
    ? modifierColumns
    : (table?.columns ?? []).map((column) => ({
      id: column.id,
      title: column.title,
      dataType: column.data_type,
      filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
    }))
  const totalRows = scatterPlot?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const linkedRows = table?.rows_total ?? totalRows
  const pageCount = Math.max(1, Math.ceil(linkedRows / Math.max(resolvedPage.size, 1)))
  const canGoPrevious = resolvedPage.index > 0
  const canGoNext = resolvedPage.index + 1 < pageCount
  const selectedPoint = selectedPointRowIndex !== null
    ? scatterPlot?.points.find((point) => point.row_index === selectedPointRowIndex) ?? null
    : null
  const selectedPointLabel = selectedPoint
    ? `${scatterPlot?.x_column ?? 'x'} ${formatHistogramBound(selectedPoint.x)}, ${scatterPlot?.y_column ?? 'y'} ${formatHistogramBound(selectedPoint.y)}`
    : null
  const resolvedPanelHeight = normalizePanelHeight(panelHeight) ?? DEFAULT_SCATTER_PLOT_CHART_HEIGHT
  const hasSettingsOverrides = Object.keys(buildModifierOverridesRecord(
    serializeScatterPlotChartModifierValues(chartOverrides),
    asset.default_modifiers,
  )).length > 0

  useEffect(() => {
    setPageInput(String(resolvedPage.index + 1))
  }, [resolvedPage.index])

  useEffect(() => {
    if (selectedPointRowIndex === null || scatterPlot === null) {
      return
    }
    if (!scatterPlot.points.some((point) => point.row_index === selectedPointRowIndex)) {
      setSelectedPointRowIndex(null)
    }
  }, [scatterPlot, selectedPointRowIndex])

  function commitPageInput() {
    const parsed = Number(pageInput.trim())
    if (!Number.isInteger(parsed)) {
      setPageInput(String(resolvedPage.index + 1))
      return
    }
    const clampedIndex = Math.min(Math.max(parsed - 1, 0), pageCount - 1)
    setPageIndex(clampedIndex)
    setPageInput(String(clampedIndex + 1))
  }

  function handleResetOverrides() {
    const resetState = initialTableStateFromModifiers(asset.default_modifiers, {}, DEFAULT_DATAVIZ_TABLE_PAGE_SIZE)
    setPageIndex(resetState.page.index)
    setPageSize(resetState.page.size)
    setSort(resetState.sort)
    setFilters(resetState.filters)
    setChartOverrides(chartOverrideDefaults)
    setSelectedBounds(null)
    setSelectedPointRowIndex(null)
    setPageInput(String(resetState.page.index + 1))
    onPersistedStateChange?.({
      modifier_overrides: {},
      override_schema_hash: asset.override_schema_hash,
    })
  }

  function handleResetSettingsOverrides() {
    setChartOverrides(chartOverrideDefaults)
  }

  const settingsBody = (
    <>
      <div className="asset-dataviz-settings-actions">
        <button type="button" className="secondary asset-dataviz-settings-reset" onClick={handleResetSettingsOverrides} disabled={!hasSettingsOverrides}>
          Reset to default
        </button>
      </div>

      <PanelSettingsSection title="Scatter plot">
        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.minPointSize, chartOverrideDefaults.minPointSize))}>{modifierTitle(asset.modifier_schema, 'min_point_size', 'Min point size')}</span>
          <DeferredModifierInput
            displayValue={chartOverrides.minPointSize}
            inputMode="decimal"
            isValid={(value) => optionalNumberFromInput(value) !== undefined}
            fallbackValue={chartOverrideDefaults.minPointSize}
            onValidChange={(nextValue) => setChartOverrides((current) => ({
              ...current,
              minPointSize: nextValue,
            }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({
              ...current,
              minPointSize: nextValue,
            }))}
          />
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.maxPointSize, chartOverrideDefaults.maxPointSize))}>{modifierTitle(asset.modifier_schema, 'max_point_size', 'Max point size')}</span>
          <DeferredModifierInput
            displayValue={chartOverrides.maxPointSize}
            inputMode="decimal"
            isValid={(value) => optionalNumberFromInput(value) !== undefined}
            fallbackValue={chartOverrideDefaults.maxPointSize}
            onValidChange={(nextValue) => setChartOverrides((current) => ({
              ...current,
              maxPointSize: nextValue,
            }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({
              ...current,
              maxPointSize: nextValue,
            }))}
          />
        </label>

        <label className="asset-dataviz-checkbox-field">
          <input
            type="checkbox"
            checked={chartOverrides.showLegend}
            onChange={(event) => setChartOverrides((current) => ({
              ...current,
              showLegend: event.target.checked,
            }))}
          />
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.showLegend, chartOverrideDefaults.showLegend))}>{modifierTitle(asset.modifier_schema, 'show_legend', 'Show legend')}</span>
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.shapeStyle, chartOverrideDefaults.shapeStyle))}>{modifierTitle(asset.modifier_schema, 'shape_style', 'Shape style')}</span>
          <select
            value={chartOverrides.shapeStyle}
            onChange={(event) => setChartOverrides((current) => ({
              ...current,
              shapeStyle: event.target.value as ScatterPlotShapeStyle,
            }))}
          >
            <option value="outline">Outline</option>
            <option value="filled">Filled</option>
          </select>
        </label>
      </PanelSettingsSection>

      <AxisOverridesSection
        title={modifierTitle(asset.modifier_schema, 'x_axis', 'X axis')}
        overrides={chartOverrides.xAxis}
        defaultOverrides={chartOverrideDefaults.xAxis}
        defaultLabel={chartOverrideDefaults.xAxis.label}
        onChange={(next) => setChartOverrides((current) => ({ ...current, xAxis: next }))}
      />

      <AxisOverridesSection
        title={modifierTitle(asset.modifier_schema, 'y_axis', 'Y axis')}
        overrides={chartOverrides.yAxis}
        defaultOverrides={chartOverrideDefaults.yAxis}
        defaultLabel={chartOverrideDefaults.yAxis.label}
        onChange={(next) => setChartOverrides((current) => ({ ...current, yAxis: next }))}
      />

      <TitleOverridesSection
        title={modifierTitle(asset.modifier_schema, 'title', 'Title')}
        overrides={chartOverrides.title}
        defaultOverrides={chartOverrideDefaults.title}
        defaultText={chartOverrideDefaults.title.text}
        onChange={(next) => setChartOverrides((current) => ({ ...current, title: next }))}
      />
    </>
  )

  return (
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} settingsTitle="Modifier overrides" settingsBody={settingsBody} settingsActive={hasSettingsOverrides} sectionId={sectionId}>
      <div className="asset-dataframe-panel asset-scatter-plot-panel">
      {overrideIncompatible ? (
        <div className="asset-panel-inline-notice error">
          <p>Saved panel overrides are no longer compatible with the current asset schema.</p>
          {onPersistedStateChange ? (
            <button type="button" className="secondary asset-inline-action" onClick={handleResetOverrides}>
              Reset panel overrides
            </button>
          ) : null}
        </div>
      ) : null}

      {response?.errors.length ? (
        <div className="asset-panel-inline-notice">
          {response.errors.map((error) => (
            <p key={error.code}>{error.message}</p>
          ))}
        </div>
      ) : null}

      <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange}>
        {(chartHeight) => (
          <>
            {selectedPointLabel ? (
              <div className="asset-histogram-selection-pill">
                <strong>Selected point</strong>
                <span>{selectedPointLabel}</span>
              </div>
            ) : null}

            {prepareQuery.isLoading && !scatterPlot ? <div className="asset-panel-placeholder"><p>Preparing scatter plot view...</p></div> : null}

            {prepareQuery.isError ? (
              <div className="asset-panel-placeholder error">
                <p>{prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the scatter plot view.'}</p>
              </div>
            ) : null}

            {scatterPlot ? (
              <ScatterPlotChart
                scatterPlot={scatterPlot}
                chartHeight={chartHeight}
                overrides={chartOverrides}
                defaultOverrides={chartOverrideDefaults}
                selectedBounds={selectedBounds}
                selectedPointRowIndex={selectedPointRowIndex}
                onSelectionChange={(bounds) => {
                  setPageIndex(0)
                  setSelectedBounds(bounds)
                  setSelectedPointRowIndex(null)
                }}
                onPointSelectionChange={(rowIndex) => {
                  setPageIndex(0)
                  setSelectedBounds(null)
                  setSelectedPointRowIndex(rowIndex)
                }}
              />
            ) : null}
          </>
        )}
      </ResizableDatavizContent>

      {table ? (
        <div className={`asset-dataframe-shell${prepareQuery.isFetching ? ' is-refreshing' : ''}`}>
          <PreparedTable
            table={table}
            columns={availableColumns}
            activeSort={resolvedSort}
            activeFilters={resolvedFilters}
            disabled={overrideIncompatible || prepareQuery.isFetching}
            onToggleSort={(column) => {
              setPageIndex(0)
              setSort((current) => nextSortForColumn(current, column))
            }}
            onApplyFilter={(filter) => {
              setPageIndex(0)
              setFilters((current) => upsertFilter(current, filter))
            }}
            onRemoveFilter={(columnId) => {
              setPageIndex(0)
              setFilters((current) => removeFilter(current, columnId))
            }}
          />
          <div className="asset-dataframe-toolbar">
            <div className="asset-dataframe-stats">{formatCount(linkedRows)} linked rows x {formatCount(columnCount)} cols</div>
            <div className="asset-dataframe-controls">
              <select
                className="asset-page-size-select"
                value={resolvedPage.size}
                onChange={(event) => {
                  setPageSize(Number(event.target.value))
                  setPageIndex(0)
                }}
                aria-label="Rows per page"
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>{size} / page</option>
                ))}
              </select>
              <div className="asset-dataframe-pagination">
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(0)}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<<'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<'}
                </button>
                <input
                  className="asset-page-input"
                  value={pageInput}
                  inputMode="numeric"
                  aria-label="Page number"
                  onChange={(event) => setPageInput(event.target.value.replace(/[^0-9]/g, ''))}
                  onBlur={commitPageInput}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      commitPageInput()
                    }
                    if (event.key === 'Escape') {
                      event.preventDefault()
                      setPageInput(String(resolvedPage.index + 1))
                    }
                  }}
                />
                <span className="asset-page-count-label">/ {pageCount}</span>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.min(pageCount - 1, current + 1))}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(pageCount - 1)}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>>'}
                </button>
              </div>
            </div>
          </div>
          {prepareQuery.isFetching ? (
            <div className="asset-dataframe-loading-overlay" aria-hidden="true">
              <div className="asset-dataframe-loading-spinner" />
            </div>
          ) : null}
        </div>
      ) : null}
      </div>
    </AssetPanelFrame>
  )
}

function AssetPanelFrame({
  asset,
  panelInfo,
  settingsTitle,
  settingsBody,
  settingsActive = false,
  sectionId,
  children,
}: {
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  settingsTitle?: string
  settingsBody?: ReactNode
  settingsActive?: boolean
  sectionId?: string
  children: ReactNode
}) {
  return (
    <section id={sectionId} className="panel asset-panel-card">
      <div className="asset-panel-header">
        <div className="asset-panel-heading">
          <div className="asset-panel-title-row">
            <span className={`asset-state-bubble is-${asset.state}`} aria-hidden="true" />
            <h2>{asset.title || asset.asset_name}</h2>
          </div>
          {asset.description ? <p className="asset-panel-description">{asset.description}</p> : null}
        </div>
        <AssetPanelHeaderActions panelInfo={panelInfo} settingsTitle={settingsTitle} settingsBody={settingsBody} settingsActive={settingsActive} />
      </div>
      {children}
    </section>
  )
}

function AssetPanelHeaderActions({
  panelInfo,
  settingsTitle,
  settingsBody,
  settingsActive,
}: {
  panelInfo: AssetPanelInfo
  settingsTitle?: string
  settingsBody?: ReactNode
  settingsActive: boolean
}) {
  const [openMenu, setOpenMenu] = useState<'info' | 'settings' | null>(null)
  const actionsRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (openMenu === null || typeof document === 'undefined') {
      return
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (actionsRef.current?.contains(event.target as Node)) {
        return
      }
      setOpenMenu(null)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [openMenu])

  return (
    <div ref={actionsRef} className="asset-panel-header-actions">
      <details className="asset-panel-action-menu" open={openMenu === 'info'}>
        <summary
          className="asset-panel-action-button"
          aria-label="Show panel info"
          title="Show panel info"
          onClick={(event) => {
            event.preventDefault()
            setOpenMenu((current) => current === 'info' ? null : 'info')
          }}
        >
          <em aria-hidden="true">i</em>
        </summary>
        <div className="asset-panel-action-popover asset-panel-info-popover">
          <dl>
            <div>
              <dt>Panel id</dt>
              <dd><code>{panelInfo.panelId}</code></dd>
            </div>
            <div>
              <dt>Asset</dt>
              <dd>{panelInfo.assetTitle || panelInfo.assetName}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{panelInfo.createdLabel}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{panelInfo.runtimeType}</dd>
            </div>
          </dl>
        </div>
      </details>

      {settingsBody ? (
        <details className="asset-panel-action-menu" open={openMenu === 'settings'}>
          <summary
            className={`asset-panel-action-button${settingsActive ? ' is-overridden' : ''}`}
            aria-label={settingsTitle}
            title={settingsTitle}
            onClick={(event) => {
              event.preventDefault()
              setOpenMenu((current) => current === 'settings' ? null : 'settings')
            }}
          >
            <Cog width={14} height={14} />
          </summary>
          <div className="asset-panel-action-popover asset-panel-settings-popover">
            <div className="asset-dataviz-settings-panel">
              {settingsTitle ? <div className="asset-dataviz-settings-heading">{settingsTitle}</div> : null}
              {settingsBody}
            </div>
          </div>
        </details>
      ) : null}
    </div>
  )
}

function ResizableDatavizContent({
  height,
  onHeightChange,
  children,
}: {
  height: number
  onHeightChange?: (height: number) => void
  children: (height: number) => ReactNode
}) {
  const [draftHeight, setDraftHeight] = useState(height)
  const draftHeightRef = useRef(height)
  const dragCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    draftHeightRef.current = height
    setDraftHeight(height)
  }, [height])

  useEffect(() => () => {
    dragCleanupRef.current?.()
  }, [])

  function handlePointerDown(event: ReactPointerEvent<HTMLButtonElement>) {
    if (!onHeightChange || event.button !== 0) {
      return
    }
    event.preventDefault()
    dragCleanupRef.current?.()
    const startY = event.clientY
    const startHeight = draftHeightRef.current
    const handleWindowPointerMove = (moveEvent: PointerEvent) => {
      const nextHeight = clampPanelHeight(startHeight + moveEvent.clientY - startY)
      draftHeightRef.current = nextHeight
      setDraftHeight(nextHeight)
    }
    const handleWindowPointerUp = () => {
      const nextHeight = draftHeightRef.current
      dragCleanupRef.current?.()
      if (nextHeight !== startHeight) {
        onHeightChange(nextHeight)
      }
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', handleWindowPointerMove)
      window.removeEventListener('pointerup', handleWindowPointerUp)
      dragCleanupRef.current = null
    }
    dragCleanupRef.current = cleanup
    window.addEventListener('pointermove', handleWindowPointerMove)
    window.addEventListener('pointerup', handleWindowPointerUp)
  }

  return (
    <>
      {children(draftHeight)}
      {onHeightChange ? (
        <button
          type="button"
          className="asset-dataviz-resize-handle"
          aria-label="Resize visualization height"
          title="Drag to resize chart height"
          onPointerDown={handlePointerDown}
        >
          <span className="asset-dataviz-resize-grip" aria-hidden="true" />
        </button>
      ) : null}
    </>
  )
}

function PanelSettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="asset-dataviz-settings-section">
      <h3>{title}</h3>
      <div className="asset-dataviz-settings-fields">{children}</div>
    </section>
  )
}

function DeferredModifierInput({
  displayValue,
  isValid,
  fallbackValue,
  onValidChange,
  onCommit,
  inputMode,
  ariaLabel,
  placeholder,
}: {
  displayValue: string
  isValid: (value: string) => boolean
  fallbackValue: string
  onValidChange: (next: string) => void
  onCommit: (next: string) => void
  inputMode?: 'text' | 'decimal' | 'numeric'
  ariaLabel?: string
  placeholder?: string
}) {
  const [draftValue, setDraftValue] = useState(displayValue)
  const [isEditing, setIsEditing] = useState(false)

  useEffect(() => {
    if (!isEditing) {
      setDraftValue(displayValue)
    }
  }, [displayValue, isEditing])

  function commitValue(nextValue: string) {
    onCommit(isValid(nextValue) ? nextValue : fallbackValue)
  }

  return (
    <input
      value={isEditing ? draftValue : displayValue}
      inputMode={inputMode}
      aria-label={ariaLabel}
      placeholder={placeholder}
      onFocus={() => setIsEditing(true)}
      onChange={(event) => {
        const nextValue = event.target.value
        setIsEditing(true)
        setDraftValue(nextValue)
        if (isValid(nextValue)) {
          onValidChange(nextValue)
        }
      }}
      onBlur={() => {
        setIsEditing(false)
        commitValue(draftValue)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault()
          commitValue(draftValue)
          ;(event.currentTarget as HTMLInputElement).blur()
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          setDraftValue(displayValue)
          ;(event.currentTarget as HTMLInputElement).blur()
        }
      }}
    />
  )
}

function AxisOverridesSection({
  title,
  overrides,
  defaultOverrides,
  defaultLabel,
  onChange,
}: {
  title: string
  overrides: ChartAxisOverrides
  defaultOverrides: ChartAxisOverrides
  defaultLabel: string
  onChange: (next: ChartAxisOverrides) => void
}) {
  const resolvedLabel = resolvedAxisLabel(overrides.label, defaultLabel)
  return (
    <PanelSettingsSection title={title}>
      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.labelSize, defaultOverrides.labelSize))}>Label size</span>
        <DeferredModifierInput
          displayValue={overrides.labelSize}
          inputMode="decimal"
          placeholder="Default"
          isValid={(value) => optionalNumberFromInput(value) !== undefined}
          fallbackValue={defaultOverrides.labelSize}
          onValidChange={(nextValue) => onChange({ ...overrides, labelSize: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, labelSize: nextValue })}
        />
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(resolvedLabel, resolvedAxisLabel(defaultOverrides.label, defaultLabel)))}>Label</span>
        <DeferredModifierInput
          displayValue={resolvedLabel}
          isValid={(value) => value.trim() !== ''}
          fallbackValue={defaultOverrides.label}
          onValidChange={(nextValue) => onChange({ ...overrides, label: nextValue })}
          onCommit={(nextValue) => onChange({
            ...overrides,
            label: resolvedAxisLabel(nextValue, defaultLabel) === resolvedAxisLabel(defaultOverrides.label, defaultLabel)
              ? defaultOverrides.label
              : nextValue,
          })}
        />
      </label>

      <label className="asset-dataviz-checkbox-field">
        <input
          type="checkbox"
          checked={overrides.hideLabel}
          onChange={(event) => onChange({ ...overrides, hideLabel: event.target.checked })}
        />
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.hideLabel, defaultOverrides.hideLabel))}>Hide label</span>
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.tickCount, defaultOverrides.tickCount))}>Tick count</span>
        <DeferredModifierInput
          displayValue={overrides.tickCount}
          inputMode="numeric"
          placeholder="Auto"
          isValid={(value) => optionalIntegerFromInput(value) !== undefined}
          fallbackValue={defaultOverrides.tickCount}
          onValidChange={(nextValue) => onChange({ ...overrides, tickCount: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, tickCount: nextValue })}
        />
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.tickSize, defaultOverrides.tickSize))}>Tick size</span>
        <DeferredModifierInput
          displayValue={overrides.tickSize}
          inputMode="decimal"
          placeholder="Auto"
          isValid={(value) => optionalNumberFromInput(value) !== undefined}
          fallbackValue={defaultOverrides.tickSize}
          onValidChange={(nextValue) => onChange({ ...overrides, tickSize: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, tickSize: nextValue })}
        />
      </label>

      <label className="asset-dataviz-checkbox-field">
        <input
          type="checkbox"
          checked={overrides.showGridLines}
          onChange={(event) => onChange({ ...overrides, showGridLines: event.target.checked })}
        />
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.showGridLines, defaultOverrides.showGridLines))}>Show grid lines</span>
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.scale, defaultOverrides.scale))}>Scale</span>
        <select
          value={overrides.scale}
          onChange={(event) => onChange({ ...overrides, scale: event.target.value as DatavizAxisScale })}
        >
          <option value="lin">Lin</option>
          <option value="log">Log</option>
        </select>
      </label>
    </PanelSettingsSection>
  )
}

function TitleOverridesSection({
  title = 'Title',
  overrides,
  defaultOverrides,
  defaultText,
  onChange,
}: {
  title?: string
  overrides: ChartTitleOverrides
  defaultOverrides: ChartTitleOverrides
  defaultText: string
  onChange: (next: ChartTitleOverrides) => void
}) {
  const resolvedText = overrides.text.trim() || defaultText
  return (
    <PanelSettingsSection title={title}>
      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.size, defaultOverrides.size))}>Size</span>
        <DeferredModifierInput
          displayValue={overrides.size}
          inputMode="decimal"
          placeholder="Default"
          isValid={(value) => optionalNumberFromInput(value) !== undefined}
          fallbackValue={defaultOverrides.size}
          onValidChange={(nextValue) => onChange({ ...overrides, size: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, size: nextValue })}
        />
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(resolvedText, defaultOverrides.text.trim() || defaultText))}>Text</span>
        <DeferredModifierInput
          displayValue={resolvedText}
          isValid={(value) => value.trim() !== ''}
          fallbackValue={defaultOverrides.text}
          onValidChange={(nextValue) => onChange({ ...overrides, text: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, text: nextValue })}
        />
      </label>

      <label className="asset-dataviz-checkbox-field">
        <input
          type="checkbox"
          checked={overrides.hideTitle}
          onChange={(event) => onChange({ ...overrides, hideTitle: event.target.checked })}
        />
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.hideTitle, defaultOverrides.hideTitle))}>Hide title</span>
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.position, defaultOverrides.position))}>Position</span>
        <select
          value={overrides.position}
          onChange={(event) => onChange({ ...overrides, position: event.target.value as 'top' | 'bottom' })}
        >
          <option value="top">Top</option>
          <option value="bottom">Bottom</option>
        </select>
      </label>
    </PanelSettingsSection>
  )
}

function DataFrameHeaderFilterMenu({
  column,
  activeFilter,
  disabled,
  onApplyFilter,
  onRemoveFilter,
  onClose,
}: {
  column: ModifierColumn
  activeFilter: AssetFilter | null
  disabled: boolean
  onApplyFilter: (filter: AssetFilter) => void
  onRemoveFilter: (columnId: string) => void
  onClose: () => void
}) {
  const initialDraft = filterDraftFromColumn(column, activeFilter)
  const [selectedKind, setSelectedKind] = useState<AssetFilterKind>(initialDraft.kind)
  const [rangeLower, setRangeLower] = useState(initialDraft.rangeLower)
  const [rangeUpper, setRangeUpper] = useState(initialDraft.rangeUpper)
  const [valueInput, setValueInput] = useState(initialDraft.valueInput)
  const [includeNull, setIncludeNull] = useState(initialDraft.includeNull)
  const [regexPattern, setRegexPattern] = useState(initialDraft.regexPattern)
  const [regexCaseSensitive, setRegexCaseSensitive] = useState(initialDraft.regexCaseSensitive)
  const [editorError, setEditorError] = useState<string | null>(null)
  const category = dataTypeCategory(column.dataType)

  useEffect(() => {
    const nextDraft = filterDraftFromColumn(column, activeFilter)
    setSelectedKind(nextDraft.kind)
    setRangeLower(nextDraft.rangeLower)
    setRangeUpper(nextDraft.rangeUpper)
    setValueInput(nextDraft.valueInput)
    setIncludeNull(nextDraft.includeNull)
    setRegexPattern(nextDraft.regexPattern)
    setRegexCaseSensitive(nextDraft.regexCaseSensitive)
    setEditorError(null)
  }, [activeFilter, column])

  function applyCurrentFilter() {
    try {
      const filter = buildFilterFromInputs({
        column,
        kind: selectedKind,
        rangeLower,
        rangeUpper,
        valueInput,
        includeNull,
        regexPattern,
        regexCaseSensitive,
      })
      setEditorError(null)
      onApplyFilter(filter)
      onClose()
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : 'Could not apply this filter.')
    }
  }

  return (
    <div className="asset-table-filter-menu" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      const target = event.target
      if (
        event.key === 'Enter'
        && (target instanceof HTMLInputElement || target instanceof HTMLSelectElement)
      ) {
        event.preventDefault()
        applyCurrentFilter()
      }
    }}>
      <select
        value={selectedKind}
        onChange={(event) => {
          setSelectedKind(event.target.value as AssetFilterKind)
          setEditorError(null)
        }}
        disabled={disabled}
        aria-label={`Filter type for ${column.title}`}
      >
        {column.filterKinds.map((kind) => (
          <option key={kind} value={kind}>{filterKindLabel(kind, column.dataType)}</option>
        ))}
      </select>

      {selectedKind === 'range' ? (
        <div className="asset-table-filter-range-fields">
          <input
            value={rangeLower}
            onChange={(event) => setRangeLower(event.target.value)}
            disabled={disabled}
            placeholder={rangeFilterPlaceholder(column.dataType, 'lower')}
            aria-label={`Lower bound for ${column.title}`}
            inputMode={category === 'numeric' ? 'decimal' : undefined}
          />
          <span aria-hidden="true">-</span>
          <input
            value={rangeUpper}
            onChange={(event) => setRangeUpper(event.target.value)}
            disabled={disabled}
            placeholder={rangeFilterPlaceholder(column.dataType, 'upper')}
            aria-label={`Upper bound for ${column.title}`}
            inputMode={category === 'numeric' ? 'decimal' : undefined}
          />
        </div>
      ) : null}

      {selectedKind === 'value' ? (
        <>
          {category === 'bool' ? (
            <select
              value={valueInput}
              onChange={(event) => setValueInput(event.target.value)}
              disabled={disabled}
              aria-label={`Value filter for ${column.title}`}
            >
              <option value="">Select value</option>
              <option value="true">True</option>
              <option value="false">False</option>
            </select>
          ) : (
            <input
              value={valueInput}
              onChange={(event) => setValueInput(event.target.value)}
              disabled={disabled}
              placeholder={valueFilterPlaceholder(column.dataType)}
              aria-label={`Value filter for ${column.title}`}
              inputMode={category === 'numeric' ? 'decimal' : undefined}
            />
          )}
          <label className="asset-table-filter-checkbox">
            <input
              type="checkbox"
              checked={includeNull}
              onChange={(event) => setIncludeNull(event.target.checked)}
              disabled={disabled}
            />
            <span>Include empty</span>
          </label>
        </>
      ) : null}

      {selectedKind === 'regex' ? (
        <>
          <input
            value={regexPattern}
            onChange={(event) => setRegexPattern(event.target.value)}
            disabled={disabled}
            placeholder="Pattern"
            aria-label={`Regex filter for ${column.title}`}
          />
          <label className="asset-table-filter-checkbox">
            <input
              type="checkbox"
              checked={regexCaseSensitive}
              onChange={(event) => setRegexCaseSensitive(event.target.checked)}
              disabled={disabled}
            />
            <span>Case-sensitive</span>
          </label>
        </>
      ) : null}

      {editorError ? <p className="asset-table-filter-error">{editorError}</p> : null}

      <div className="asset-table-filter-menu-actions">
        {activeFilter ? (
          <button
            type="button"
            className="ghost asset-inline-action asset-table-filter-clear"
            onClick={() => {
              onRemoveFilter(column.id)
              onClose()
            }}
            disabled={disabled}
          >
            Clear
          </button>
        ) : <span />}
        <button type="button" className="secondary asset-table-filter-apply" onClick={applyCurrentFilter} disabled={disabled}>
          Apply
        </button>
      </div>
    </div>
  )
}

function initialTableStateFromModifiers(
  defaultModifiers: Record<string, unknown>,
  modifierOverrides: Record<string, unknown>,
  defaultPageSize = DEFAULT_TABLE_PAGE_SIZE,
): TableState {
  const page = pageFromValue(mergedModifierValue(defaultModifiers.page, modifierOverrides.page), defaultPageSize)
    ?? { index: 0, size: defaultPageSize }
  const sort = sortFromValue(mergedModifierValue(defaultModifiers.sort, modifierOverrides.sort)) ?? null
  const filters = filtersFromValue(mergedModifierValue(defaultModifiers.filters, modifierOverrides.filters)) ?? []
  return { page, sort, filters }
}

function initialHistogramStateFromModifiers(
  defaultModifiers: Record<string, unknown>,
  modifierOverrides: Record<string, unknown>,
): HistogramState {
  const tableState = initialTableStateFromModifiers(defaultModifiers, modifierOverrides, DEFAULT_DATAVIZ_TABLE_PAGE_SIZE)
  const binCount = binCountFromValue(mergedModifierValue(defaultModifiers.bin_count, modifierOverrides.bin_count)) ?? 20
  return {
    ...tableState,
    binCount,
  }
}

function emptyChartAxisOverrides(): ChartAxisOverrides {
  return {
    labelSize: '',
    label: '',
    hideLabel: false,
    tickCount: '',
    tickSize: '',
    showGridLines: true,
    scale: 'lin',
  }
}

function emptyChartTitleOverrides(): ChartTitleOverrides {
  return {
    size: '',
    text: '',
    hideTitle: true,
    position: 'top',
  }
}

function defaultHistogramChartOverrides(
  defaultModifiers: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): HistogramChartOverrides {
  return {
    xAxis: chartAxisOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'x_axis'), emptyChartAxisOverrides()),
    yAxis: chartAxisOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'y_axis'), emptyChartAxisOverrides()),
    title: chartTitleOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'title'), emptyChartTitleOverrides()),
    barWidth: clampPercentage(modifierDefaultValue(defaultModifiers, modifierSchema, 'bar_width'), 0),
    borderThickness: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'border_thickness'), ''),
  }
}

function defaultScatterPlotChartOverrides(
  defaultModifiers: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): ScatterPlotChartOverrides {
  return {
    xAxis: chartAxisOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'x_axis'), emptyChartAxisOverrides()),
    yAxis: chartAxisOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'y_axis'), emptyChartAxisOverrides()),
    title: chartTitleOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'title'), emptyChartTitleOverrides()),
    minPointSize: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'min_point_size'), ''),
    maxPointSize: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'max_point_size'), ''),
    showLegend: typeof modifierDefaultValue(defaultModifiers, modifierSchema, 'show_legend') === 'boolean'
      ? modifierDefaultValue(defaultModifiers, modifierSchema, 'show_legend') as boolean
      : true,
    shapeStyle: modifierDefaultValue(defaultModifiers, modifierSchema, 'shape_style') === 'filled' ? 'filled' : 'outline',
  }
}

function defaultPieChartOverrides(
  defaultModifiers: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): PieChartChartOverrides {
  return {
    innerRadius: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'inner_radius'), '0.5'),
    labelSize: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'label_size'), '12'),
    labelThreshold: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'label_threshold'), '5'),
    labelPosition: clampNumberToRange(modifierDefaultValue(defaultModifiers, modifierSchema, 'label_position'), 105, 0, 200, true),
    mergeThreshold: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'merge_threshold'), '0'),
    borderThickness: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'border_thickness'), '3'),
    mergedCategoryLabel: typeof modifierDefaultValue(defaultModifiers, modifierSchema, 'merged_category_label') === 'string'
      ? modifierDefaultValue(defaultModifiers, modifierSchema, 'merged_category_label') as string
      : 'Others',
    showMergedCategory: typeof modifierDefaultValue(defaultModifiers, modifierSchema, 'show_merged_category') === 'boolean'
      ? modifierDefaultValue(defaultModifiers, modifierSchema, 'show_merged_category') as boolean
      : true,
    showPercentages: typeof modifierDefaultValue(defaultModifiers, modifierSchema, 'show_percentages') === 'boolean'
      ? modifierDefaultValue(defaultModifiers, modifierSchema, 'show_percentages') as boolean
      : false,
    title: chartTitleOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'title'), emptyChartTitleOverrides()),
  }
}

function histogramChartOverridesFromModifiers(
  defaultModifiers: Record<string, unknown>,
  modifierOverrides: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): HistogramChartOverrides {
  const defaults = defaultHistogramChartOverrides(defaultModifiers, modifierSchema)
  return {
    xAxis: chartAxisOverridesFromValue(mergedModifierValue(defaultModifiers.x_axis, modifierOverrides.x_axis), defaults.xAxis),
    yAxis: chartAxisOverridesFromValue(mergedModifierValue(defaultModifiers.y_axis, modifierOverrides.y_axis), defaults.yAxis),
    title: chartTitleOverridesFromValue(mergedModifierValue(defaultModifiers.title, modifierOverrides.title), defaults.title),
    barWidth: clampPercentage(mergedModifierValue(defaultModifiers.bar_width, modifierOverrides.bar_width), defaults.barWidth),
    borderThickness: numericInputString(mergedModifierValue(defaultModifiers.border_thickness, modifierOverrides.border_thickness), defaults.borderThickness),
  }
}

function scatterPlotChartOverridesFromModifiers(
  defaultModifiers: Record<string, unknown>,
  modifierOverrides: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): ScatterPlotChartOverrides {
  const defaults = defaultScatterPlotChartOverrides(defaultModifiers, modifierSchema)
  return {
    xAxis: chartAxisOverridesFromValue(mergedModifierValue(defaultModifiers.x_axis, modifierOverrides.x_axis), defaults.xAxis),
    yAxis: chartAxisOverridesFromValue(mergedModifierValue(defaultModifiers.y_axis, modifierOverrides.y_axis), defaults.yAxis),
    title: chartTitleOverridesFromValue(mergedModifierValue(defaultModifiers.title, modifierOverrides.title), defaults.title),
    minPointSize: numericInputString(mergedModifierValue(defaultModifiers.min_point_size, modifierOverrides.min_point_size), defaults.minPointSize),
    maxPointSize: numericInputString(mergedModifierValue(defaultModifiers.max_point_size, modifierOverrides.max_point_size), defaults.maxPointSize),
    showLegend: typeof mergedModifierValue(defaultModifiers.show_legend, modifierOverrides.show_legend) === 'boolean'
      ? mergedModifierValue(defaultModifiers.show_legend, modifierOverrides.show_legend) as boolean
      : defaults.showLegend,
    shapeStyle: mergedModifierValue(defaultModifiers.shape_style, modifierOverrides.shape_style) === 'filled'
      ? 'filled'
      : defaults.shapeStyle,
  }
}

function pieChartOverridesFromModifiers(
  defaultModifiers: Record<string, unknown>,
  modifierOverrides: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): PieChartChartOverrides {
  const defaults = defaultPieChartOverrides(defaultModifiers, modifierSchema)
  return {
    innerRadius: numericInputString(mergedModifierValue(defaultModifiers.inner_radius, modifierOverrides.inner_radius), defaults.innerRadius),
    labelSize: numericInputString(mergedModifierValue(defaultModifiers.label_size, modifierOverrides.label_size), defaults.labelSize),
    labelThreshold: numericInputString(mergedModifierValue(defaultModifiers.label_threshold, modifierOverrides.label_threshold), defaults.labelThreshold),
    labelPosition: clampNumberToRange(mergedModifierValue(defaultModifiers.label_position, modifierOverrides.label_position), defaults.labelPosition, 0, 200, true),
    mergeThreshold: numericInputString(mergedModifierValue(defaultModifiers.merge_threshold, modifierOverrides.merge_threshold), defaults.mergeThreshold),
    borderThickness: numericInputString(mergedModifierValue(defaultModifiers.border_thickness, modifierOverrides.border_thickness), defaults.borderThickness),
    mergedCategoryLabel: typeof mergedModifierValue(defaultModifiers.merged_category_label, modifierOverrides.merged_category_label) === 'string'
      ? mergedModifierValue(defaultModifiers.merged_category_label, modifierOverrides.merged_category_label) as string
      : defaults.mergedCategoryLabel,
    showMergedCategory: typeof mergedModifierValue(defaultModifiers.show_merged_category, modifierOverrides.show_merged_category) === 'boolean'
      ? mergedModifierValue(defaultModifiers.show_merged_category, modifierOverrides.show_merged_category) as boolean
      : defaults.showMergedCategory,
    showPercentages: typeof mergedModifierValue(defaultModifiers.show_percentages, modifierOverrides.show_percentages) === 'boolean'
      ? mergedModifierValue(defaultModifiers.show_percentages, modifierOverrides.show_percentages) as boolean
      : defaults.showPercentages,
    title: chartTitleOverridesFromValue(mergedModifierValue(defaultModifiers.title, modifierOverrides.title), defaults.title),
  }
}

function chartAxisOverridesFromValue(value: unknown, defaults: ChartAxisOverrides): ChartAxisOverrides {
  if (!value || typeof value !== 'object') {
    return defaults
  }
  const record = value as Record<string, unknown>
  return {
    labelSize: numericInputString(record.label_size ?? record.labelSize, defaults.labelSize),
    label: typeof (record.label) === 'string' ? record.label : defaults.label,
    hideLabel: typeof (record.hide_label ?? record.hideLabel) === 'boolean' ? Boolean(record.hide_label ?? record.hideLabel) : defaults.hideLabel,
    tickCount: integerInputString(record.tick_count ?? record.tickCount, defaults.tickCount),
    tickSize: numericInputString(record.tick_size ?? record.tickSize, defaults.tickSize),
    showGridLines: typeof (record.show_grid_lines ?? record.showGridLines) === 'boolean'
      ? Boolean(record.show_grid_lines ?? record.showGridLines)
      : defaults.showGridLines,
    scale: record.scale === 'log' ? 'log' : defaults.scale,
  }
}

function chartTitleOverridesFromValue(value: unknown, defaults: ChartTitleOverrides): ChartTitleOverrides {
  if (!value || typeof value !== 'object') {
    return defaults
  }
  const record = value as Record<string, unknown>
  return {
    size: numericInputString(record.size, defaults.size),
    text: typeof record.text === 'string' ? record.text : defaults.text,
    hideTitle: typeof (record.hide_title ?? record.hideTitle) === 'boolean' ? Boolean(record.hide_title ?? record.hideTitle) : defaults.hideTitle,
    position: record.position === 'bottom' ? 'bottom' : defaults.position,
  }
}

function serializeHistogramChartModifierValues(overrides: HistogramChartOverrides): Record<string, unknown> {
  return {
    bar_width: overrides.barWidth,
    border_thickness: optionalNumberFromInput(overrides.borderThickness),
    x_axis: serializeChartAxisModifierValue(overrides.xAxis),
    y_axis: serializeChartAxisModifierValue(overrides.yAxis),
    title: serializeChartTitleModifierValue(overrides.title),
  }
}

function serializeScatterPlotChartModifierValues(overrides: ScatterPlotChartOverrides): Record<string, unknown> {
  return {
    min_point_size: optionalNumberFromInput(overrides.minPointSize),
    max_point_size: optionalNumberFromInput(overrides.maxPointSize),
    show_legend: overrides.showLegend,
    shape_style: overrides.shapeStyle,
    x_axis: serializeChartAxisModifierValue(overrides.xAxis),
    y_axis: serializeChartAxisModifierValue(overrides.yAxis),
    title: serializeChartTitleModifierValue(overrides.title),
  }
}

function serializePieChartModifierValues(overrides: PieChartChartOverrides): Record<string, unknown> {
  return {
    inner_radius: optionalNumberFromInput(overrides.innerRadius),
    label_size: optionalNumberFromInput(overrides.labelSize),
    label_threshold: optionalNumberFromInput(overrides.labelThreshold),
    label_position: overrides.labelPosition,
    merge_threshold: optionalNumberFromInput(overrides.mergeThreshold),
    border_thickness: optionalNumberFromInput(overrides.borderThickness),
    merged_category_label: overrides.mergedCategoryLabel,
    show_merged_category: overrides.showMergedCategory,
    show_percentages: overrides.showPercentages,
    title: serializeChartTitleModifierValue(overrides.title),
  }
}

function serializeChartAxisModifierValue(overrides: ChartAxisOverrides): Record<string, unknown> {
  return {
    label_size: optionalNumberFromInput(overrides.labelSize),
    label: overrides.label,
    hide_label: overrides.hideLabel,
    tick_count: optionalIntegerFromInput(overrides.tickCount),
    tick_size: optionalNumberFromInput(overrides.tickSize),
    show_grid_lines: overrides.showGridLines,
    scale: overrides.scale,
  }
}

function serializeChartTitleModifierValue(overrides: ChartTitleOverrides): Record<string, unknown> {
  return {
    size: optionalNumberFromInput(overrides.size),
    text: overrides.text,
    hide_title: overrides.hideTitle,
    position: overrides.position,
  }
}

function pageFromValue(value: unknown, defaultPageSize: number): { index: number; size: number } | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  const pageRecord = value as Record<string, unknown>
  const index = typeof pageRecord.index === 'number' && pageRecord.index >= 0 ? pageRecord.index : 0
  const size = typeof pageRecord.size === 'number' && PAGE_SIZE_OPTIONS.includes(pageRecord.size as 10 | 25 | 50 | 100)
    ? pageRecord.size
    : defaultPageSize
  return { index, size }
}

function normalizePanelHeight(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return null
  }
  return clampPanelHeight(value)
}

function clampPanelHeight(value: number): number {
  return Math.min(MAX_DATAVIZ_CHART_HEIGHT, Math.max(MIN_DATAVIZ_CHART_HEIGHT, Math.round(value)))
}

function binCountFromValue(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > 100) {
    return null
  }
  return value
}

function clampPercentage(value: unknown, fallback: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return fallback
  }
  return Math.min(100, Math.max(0, Math.round(value)))
}

function clampNumberToRange(
  value: unknown,
  fallback: number,
  min: number,
  max: number,
  round = false,
): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return fallback
  }
  const nextValue = Math.min(max, Math.max(min, value))
  return round ? Math.round(nextValue) : nextValue
}

function integerInputString(value: unknown, fallback = ''): string {
  if (typeof value === 'number' && Number.isInteger(value)) {
    return String(value)
  }
  if (typeof value === 'string' && /^-?\d+$/.test(value.trim())) {
    return value
  }
  return fallback
}

function numericInputString(value: unknown, fallback = ''): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value)
  }
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) {
    return value
  }
  return fallback
}

function optionalNumberFromInput(value: string): number | undefined {
  if (value.trim() === '') {
    return undefined
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
}

function optionalPositiveNumberFromInput(value: string): number | undefined {
  const parsed = optionalNumberFromInput(value)
  return parsed !== undefined && parsed > 0 ? parsed : undefined
}

function optionalNonNegativeNumberFromInput(value: string): number | undefined {
  const parsed = optionalNumberFromInput(value)
  return parsed !== undefined && parsed >= 0 ? parsed : undefined
}

function optionalIntegerFromInput(value: string): number | undefined {
  if (value.trim() === '') {
    return undefined
  }
  const parsed = Number(value)
  return Number.isInteger(parsed) ? parsed : undefined
}

function sortFromValue(value: unknown): AssetSort | null {
  if (!Array.isArray(value) || !value.length || !value[0] || typeof value[0] !== 'object') {
    return null
  }
  const record = value[0] as Record<string, unknown>
  if ((record.direction !== 'asc' && record.direction !== 'desc') || typeof record.column !== 'string' || !record.column) {
    return null
  }
  return { column: record.column, direction: record.direction }
}

function filtersFromValue(value: unknown): AssetFilter[] | null {
  if (value === undefined) {
    return null
  }
  if (!Array.isArray(value)) {
    return []
  }
  const filters: AssetFilter[] = []
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') {
      continue
    }
    const record = entry as Record<string, unknown>
    if (typeof record.column !== 'string' || !record.column) {
      continue
    }
    if (record.kind === 'range') {
      filters.push({
        kind: 'range',
        column: record.column,
        value_type: typeof record.value_type === 'string' ? record.value_type : undefined,
        lower: typeof record.lower === 'string' || typeof record.lower === 'number' || record.lower === null ? record.lower : null,
        upper: typeof record.upper === 'string' || typeof record.upper === 'number' || record.upper === null ? record.upper : null,
      })
      continue
    }
    if (record.kind === 'value') {
      const values = Array.isArray(record.values)
        ? record.values.filter((item): item is string | number | boolean => (
          typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean'
        ))
        : []
      filters.push({
        kind: 'value',
        column: record.column,
        value_type: typeof record.value_type === 'string' ? record.value_type : undefined,
        values,
        include_null: Boolean(record.include_null),
      })
      continue
    }
    if (record.kind === 'regex' && typeof record.pattern === 'string' && record.pattern) {
      filters.push({
        kind: 'regex',
        column: record.column,
        pattern: record.pattern,
        case_sensitive: Boolean(record.case_sensitive),
      })
    }
  }
  return filters
}

function modifierColumnsFromSchema(modifierSchema: Array<Record<string, unknown>>): ModifierColumn[] {
  const filtersEntry = modifierSchema.find((entry) => entry.id === 'filters')
  if (!filtersEntry || !Array.isArray(filtersEntry.columns)) {
    return []
  }
  return filtersEntry.columns
    .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === 'object')
    .map((entry) => ({
      id: typeof entry.id === 'string' ? entry.id : '',
      title: typeof entry.title === 'string' ? entry.title : (typeof entry.id === 'string' ? entry.id : ''),
      dataType: typeof entry.data_type === 'string' ? entry.data_type : 'String',
      filterKinds: Array.isArray(entry.filter_kinds)
        ? entry.filter_kinds.filter((kind): kind is AssetFilterKind => kind === 'range' || kind === 'value' || kind === 'regex')
        : [],
    }))
    .filter((entry) => entry.id)
}

function filterKindsForDataType(dataType: string): AssetFilterKind[] {
  const category = dataTypeCategory(dataType)
  if (category === 'numeric' || category === 'date' || category === 'datetime' || category === 'time') {
    return ['range', 'value']
  }
  if (category === 'bool') {
    return ['value']
  }
  return ['value', 'regex']
}

function dataTypeCategory(dataType: string): 'numeric' | 'date' | 'datetime' | 'time' | 'bool' | 'text' {
  if (/^(Int|UInt|Float|Decimal)/.test(dataType) || /^(int|uint|float|decimal)/i.test(dataType)) {
    return 'numeric'
  }
  if (dataType === 'Date' || /^date(32|64)?$/i.test(dataType)) {
    return 'date'
  }
  if (dataType.startsWith('Datetime') || /^datetime64/i.test(dataType) || /^timestamp/i.test(dataType)) {
    return 'datetime'
  }
  if (dataType === 'Time' || /^time/i.test(dataType)) {
    return 'time'
  }
  if (dataType === 'Boolean' || /^bool/i.test(dataType)) {
    return 'bool'
  }
  return 'text'
}

function tableStateKey(state: TableState): string {
  return stableValueKey({
    page: state.page,
    sort: state.sort,
    filters: state.filters,
  })
}

function histogramStateKey(state: HistogramState): string {
  return stableValueKey({
    page: state.page,
    sort: state.sort,
    filters: state.filters,
    binCount: state.binCount,
  })
}

function stableValueKey(value: unknown): string {
  return JSON.stringify(sortValueForKey(value))
}

function valuesEqual(left: unknown, right: unknown): boolean {
  return stableValueKey(left) === stableValueKey(right)
}

function sortValueForKey(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortValueForKey)
  }
  if (!value || typeof value !== 'object') {
    return value
  }
  const record = value as Record<string, unknown>
  return Object.fromEntries(
    Object.keys(record)
      .sort()
      .map((key) => [key, sortValueForKey(record[key])]),
  )
}

function mergedModifierValue(defaultValue: unknown, overrideValue: unknown): unknown {
  if (overrideValue === undefined) {
    return defaultValue
  }
  if (Array.isArray(defaultValue) || Array.isArray(overrideValue)) {
    return overrideValue
  }
  if (
    defaultValue
    && typeof defaultValue === 'object'
    && overrideValue
    && typeof overrideValue === 'object'
  ) {
    const defaultRecord = defaultValue as Record<string, unknown>
    const overrideRecord = overrideValue as Record<string, unknown>
    return Object.fromEntries(
      Array.from(new Set([...Object.keys(defaultRecord), ...Object.keys(overrideRecord)])).map((key) => [
        key,
        mergedModifierValue(defaultRecord[key], overrideRecord[key]),
      ]),
    )
  }
  return overrideValue
}

function buildModifierOverridesRecord(
  currentModifiers: Record<string, unknown>,
  defaultModifiers: Record<string, unknown>,
): Record<string, unknown> {
  const nextEntries = Object.entries(currentModifiers)
    .map(([key, value]) => [key, diffModifierValue(value, defaultModifiers[key])])
    .filter((entry): entry is [string, unknown] => entry[1] !== undefined)
  return Object.fromEntries(nextEntries)
}

function diffModifierValue(currentValue: unknown, defaultValue: unknown): unknown {
  if (valuesEqual(currentValue, defaultValue)) {
    return undefined
  }
  if (Array.isArray(currentValue) || Array.isArray(defaultValue)) {
    return currentValue
  }
  if (
    currentValue
    && typeof currentValue === 'object'
    && defaultValue
    && typeof defaultValue === 'object'
  ) {
    const currentRecord = currentValue as Record<string, unknown>
    const defaultRecord = defaultValue as Record<string, unknown>
    const nextEntries = Object.entries(currentRecord)
      .map(([key, value]) => [key, diffModifierValue(value, defaultRecord[key])])
      .filter((entry): entry is [string, unknown] => entry[1] !== undefined)
    return nextEntries.length ? Object.fromEntries(nextEntries) : undefined
  }
  return currentValue
}

function modifierSchemaEntry(modifierSchema: Array<Record<string, unknown>>, id: string): Record<string, unknown> | null {
  return modifierSchema.find((entry) => entry.id === id) ?? null
}

function modifierDefaultValue(
  defaultModifiers: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
  id: string,
): unknown {
  if (id in defaultModifiers) {
    return defaultModifiers[id]
  }
  return modifierSchemaEntry(modifierSchema, id)?.default_value
}

function modifierTitle(modifierSchema: Array<Record<string, unknown>>, id: string, fallback: string): string {
  const candidate = modifierSchemaEntry(modifierSchema, id)?.title
  return typeof candidate === 'string' && candidate ? candidate : fallback
}

function modifierFieldLabelClassName(active: boolean): string {
  return active ? 'asset-modifier-label is-overridden' : 'asset-modifier-label'
}

function nextSortForColumn(current: AssetSort | null, column: string): AssetSort | null {
  if (!current || current.column !== column) {
    return { column, direction: 'asc' }
  }
  if (current.direction === 'asc') {
    return { column, direction: 'desc' }
  }
  return null
}

function upsertFilter(current: AssetFilter[], filter: AssetFilter): AssetFilter[] {
  return [...current.filter((entry) => entry.column !== filter.column), filter]
}

function removeFilter(current: AssetFilter[], columnId: string): AssetFilter[] {
  return current.filter((entry) => entry.column !== columnId)
}

function filterDraftFromColumn(column: ModifierColumn, activeFilter: AssetFilter | null) {
  if (activeFilter?.kind === 'range') {
    return {
      kind: 'range' as const,
      rangeLower: activeFilter.lower === undefined || activeFilter.lower === null ? '' : String(activeFilter.lower),
      rangeUpper: activeFilter.upper === undefined || activeFilter.upper === null ? '' : String(activeFilter.upper),
      valueInput: '',
      includeNull: false,
      regexPattern: '',
      regexCaseSensitive: false,
    }
  }
  if (activeFilter?.kind === 'value') {
    return {
      kind: 'value' as const,
      rangeLower: '',
      rangeUpper: '',
      valueInput: activeFilter.values.map(String).join(', '),
      includeNull: Boolean(activeFilter.include_null),
      regexPattern: '',
      regexCaseSensitive: false,
    }
  }
  if (activeFilter?.kind === 'regex') {
    return {
      kind: 'regex' as const,
      rangeLower: '',
      rangeUpper: '',
      valueInput: '',
      includeNull: false,
      regexPattern: activeFilter.pattern,
      regexCaseSensitive: Boolean(activeFilter.case_sensitive),
    }
  }
  return {
    kind: column.filterKinds[0] ?? 'value',
    rangeLower: '',
    rangeUpper: '',
    valueInput: '',
    includeNull: false,
    regexPattern: '',
    regexCaseSensitive: false,
  }
}

function buildFilterFromInputs({
  column,
  kind,
  rangeLower,
  rangeUpper,
  valueInput,
  includeNull,
  regexPattern,
  regexCaseSensitive,
}: {
  column: ModifierColumn
  kind: AssetFilterKind
  rangeLower: string
  rangeUpper: string
  valueInput: string
  includeNull: boolean
  regexPattern: string
  regexCaseSensitive: boolean
}): AssetFilter {
  const category = dataTypeCategory(column.dataType)
  if (kind === 'range') {
    const lower = rangeLower.trim() ? coerceRangeDraftValue(rangeLower.trim(), category) : null
    const upper = rangeUpper.trim() ? coerceRangeDraftValue(rangeUpper.trim(), category) : null
    if (lower === null && upper === null) {
      throw new Error('Range filters need at least one bound.')
    }
    return {
      kind: 'range',
      column: column.id,
      value_type: category,
      lower,
      upper,
    }
  }
  if (kind === 'value') {
    const values = valueInput
      .split(',')
      .map((entry) => entry.trim())
      .filter(Boolean)
      .map((entry) => coerceDraftValue(entry, category))
    if (!values.length && !includeNull) {
      throw new Error('Value filters need at least one value or empty rows enabled.')
    }
    return {
      kind: 'value',
      column: column.id,
      value_type: category,
      values: values.filter((entry): entry is string | number | boolean => entry !== null),
      include_null: includeNull,
    }
  }
  if (!regexPattern.trim()) {
    throw new Error('Regex filters need a pattern.')
  }
  return {
    kind: 'regex',
    column: column.id,
    pattern: regexPattern.trim(),
    case_sensitive: regexCaseSensitive,
  }
}

function coerceDraftValue(value: string, category: ReturnType<typeof dataTypeCategory>): string | number | boolean | null {
  if (category === 'numeric') {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) {
      throw new Error(`\`${value}\` is not a valid number.`)
    }
    return parsed
  }
  if (category === 'bool') {
    const normalized = value.toLowerCase()
    if (normalized === 'true') {
      return true
    }
    if (normalized === 'false') {
      return false
    }
    throw new Error(`\`${value}\` is not a valid boolean.`)
  }
  return value
}

function coerceRangeDraftValue(value: string, category: ReturnType<typeof dataTypeCategory>): string | number {
  if (category === 'bool' || category === 'text') {
    throw new Error('Range filters are only available for numeric and date-like columns.')
  }
  const resolved = coerceDraftValue(value, category)
  if (typeof resolved === 'number' || typeof resolved === 'string') {
    return resolved
  }
  throw new Error('Range filters need numeric or date-like bounds.')
}

function formatFilterSummary(filter: AssetFilter, columns: ModifierColumn[]): string {
  const column = columns.find((entry) => entry.id === filter.column)
  const label = column?.title ?? filter.column
  if (filter.kind === 'range') {
    const lower = filter.lower ?? '...'
    const upper = filter.upper ?? '...'
    return `${label}: ${String(lower)} to ${String(upper)}`
  }
  if (filter.kind === 'value') {
    const values = filter.values.length ? filter.values.map(String).join(', ') : 'empty only'
    return filter.include_null ? `${label}: ${values} + empty` : `${label}: ${values}`
  }
  return filter.case_sensitive ? `${label}: /${filter.pattern}/` : `${label}: /${filter.pattern}/i`
}

function filterKindLabel(kind: AssetFilterKind, _dataType?: string): string {
  if (kind === 'range') {
    return 'Range'
  }
  if (kind === 'regex') {
    return 'Regex'
  }
  return 'Equals'
}

function rangeFilterPlaceholder(dataType: string, bound: 'lower' | 'upper'): string {
  const category = dataTypeCategory(dataType)
  if (category === 'numeric') {
    return bound === 'lower' ? 'Min' : 'Max'
  }
  if (category === 'date') {
    return bound === 'lower' ? 'From date' : 'To date'
  }
  if (category === 'datetime') {
    return bound === 'lower' ? 'From datetime' : 'To datetime'
  }
  if (category === 'time') {
    return bound === 'lower' ? 'From time' : 'To time'
  }
  return bound === 'lower' ? 'Lower bound' : 'Upper bound'
}

function valueFilterPlaceholder(dataType: string): string {
  const category = dataTypeCategory(dataType)
  if (category === 'date') {
    return 'Date'
  }
  if (category === 'datetime') {
    return 'Datetime'
  }
  if (category === 'time') {
    return 'Time'
  }
  return 'Value'
}

function HistogramChart({
  histogram,
  chartHeight,
  overrides,
  defaultOverrides,
  selectedBarIndexes,
  onSelectionChange,
}: {
  histogram: PreparedHistogramPayload
  chartHeight: number
  overrides: HistogramChartOverrides
  defaultOverrides: HistogramChartOverrides
  selectedBarIndexes: number[]
  onSelectionChange: (barIndexes: number[]) => void
}) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)
  const viewRef = useRef<VegaEmbedResult | null>(null)
  const selectedBarIndexesRef = useRef<number[]>(selectedBarIndexes)
  const onSelectionChangeRef = useRef(onSelectionChange)
  const suppressNextClickRef = useRef(false)
  const dragStateRef = useRef<{
    startedOnBarIndex: number | null
    pointerDownX: number
    pointerDownY: number
    moved: boolean
  } | null>(null)
  const chartTheme = useAssetChartTheme()

  selectedBarIndexesRef.current = selectedBarIndexes
  onSelectionChangeRef.current = onSelectionChange

  const spec = useMemo(
    () => buildHistogramVegaLiteSpec(histogram, chartTheme, chartHeight, overrides, defaultOverrides),
    [chartHeight, chartTheme, defaultOverrides, histogram, overrides],
  )

  useEffect(() => {
    if (!mountRef.current) {
      return
    }
    let disposed = false
    let pointerDownListener: ((event: Event, item: unknown) => void) | null = null
    let clickListener: ((event: Event, item: unknown) => void) | null = null
    let doubleClickListener: ((event: Event) => void) | null = null

    const handleWindowPointerMove = (event: PointerEvent) => {
      const dragState = dragStateRef.current
      if (dragState === null) {
        return
      }
      if (!dragState.moved && Math.hypot(event.clientX - dragState.pointerDownX, event.clientY - dragState.pointerDownY) >= 3) {
        dragState.moved = true
      }
    }

    const handleWindowPointerUp = async () => {
      const dragState = dragStateRef.current
      dragStateRef.current = null
      window.removeEventListener('pointermove', handleWindowPointerMove)
      window.removeEventListener('pointerup', handleWindowPointerUp)
      const result = viewRef.current
      if (dragState === null || result === null) {
        return
      }
      if (!dragState.moved) {
        if (dragState.startedOnBarIndex === null && selectedBarIndexesRef.current.length) {
          await syncHistogramSelectedBars(result, [])
          onSelectionChangeRef.current([])
        }
        await clearHistogramBrush(result)
        return
      }
      // Let Vega commit the final brush interval before we derive the selected bars.
      await result.view.runAsync()
      const brushRange = parseSelectionRangeSignal(result.view.signal(HISTOGRAM_BRUSH_SIGNAL_NAME))
      const nextIndexes = histogramSelectedBarIndexesFromBrushRange(histogram, brushRange)
      await syncHistogramSelectedBars(result, nextIndexes)
      suppressNextClickRef.current = true
      onSelectionChangeRef.current(nextIndexes)
      await clearHistogramBrush(result)
    }

    async function renderChart() {
      setChartError(null)
      try {
        const result = await embed(mountRef.current as HTMLElement, spec, {
          actions: false,
          defaultStyle: false,
          renderer: 'svg',
          tooltip: true,
        })
        if (disposed) {
          result.finalize()
          return
        }
        viewRef.current = result
        await syncHistogramSelectedBars(result, selectedBarIndexesRef.current)

        pointerDownListener = (event: Event, item: unknown) => {
          const pointerEvent = event as PointerEvent
          if (pointerEvent.button !== 0) {
            return
          }
          const startedOnBarIndex = parseHistogramClickedBarIndex(item, histogram)
          dragStateRef.current = {
            startedOnBarIndex,
            pointerDownX: pointerEvent.clientX,
            pointerDownY: pointerEvent.clientY,
            moved: false,
          }
          window.addEventListener('pointermove', handleWindowPointerMove)
          window.addEventListener('pointerup', handleWindowPointerUp)
        }

        clickListener = (event: Event, item: unknown) => {
          const resultRef = viewRef.current
          if (resultRef === null) {
            return
          }
          if (suppressNextClickRef.current) {
            suppressNextClickRef.current = false
            event.preventDefault()
            event.stopPropagation()
            return
          }
          const clickedIndex = parseHistogramClickedBarIndex(item, histogram)
          const mouseEvent = event as MouseEvent
          let nextIndexes: number[]
          if (clickedIndex === null) {
            nextIndexes = []
          } else if (mouseEvent.shiftKey) {
            nextIndexes = toggleHistogramBarIndex(selectedBarIndexesRef.current, clickedIndex)
          } else {
            nextIndexes = [clickedIndex]
          }
          void (async () => {
            await syncHistogramSelectedBars(resultRef, nextIndexes)
            onSelectionChangeRef.current(nextIndexes)
          })()
        }

        doubleClickListener = (event: Event) => {
          event.preventDefault()
          event.stopPropagation()
        }

        result.view.addEventListener('pointerdown', pointerDownListener)
        result.view.addEventListener('click', clickListener)
        result.view.addEventListener('dblclick', doubleClickListener)
      } catch (error) {
        if (!disposed) {
          setChartError(error instanceof Error ? error.message : 'Could not render the histogram view.')
        }
      }
    }

    void renderChart()

    return () => {
      disposed = true
      dragStateRef.current = null
      window.removeEventListener('pointermove', handleWindowPointerMove)
      window.removeEventListener('pointerup', handleWindowPointerUp)
      if (viewRef.current && pointerDownListener) {
        viewRef.current.view.removeEventListener('pointerdown', pointerDownListener)
      }
      if (viewRef.current && clickListener) {
        viewRef.current.view.removeEventListener('click', clickListener)
      }
      if (viewRef.current && doubleClickListener) {
        viewRef.current.view.removeEventListener('dblclick', doubleClickListener)
      }
      viewRef.current?.finalize()
      viewRef.current = null
    }
  }, [histogram, spec])

  useEffect(() => {
    const result = viewRef.current
    if (result === null) {
      return
    }
    void syncHistogramSelectedBars(result, selectedBarIndexes)
  }, [histogram, selectedBarIndexes.join(',')])

  useEffect(() => {
    const result = viewRef.current
    if (result === null) {
      return
    }
    result.view.height(chartHeight)
    void result.view.runAsync()
  }, [chartHeight])

  if (!histogram.bins.length) {
    return (
      <div className="asset-panel-placeholder">
        <p>No numeric rows match the current histogram filters.</p>
      </div>
    )
  }

  if (chartError) {
    return (
      <div className="asset-panel-placeholder error">
        <p>{chartError}</p>
      </div>
    )
  }

  return (
    <div
      ref={mountRef}
      className="asset-histogram-chart-shell asset-histogram-vega-mount"
      style={{ '--asset-dataviz-height': `${chartHeight}px` } as CSSProperties}
    />
  )
}

function PieChartChart({
  pieChart,
  chartHeight,
  overrides,
  defaultOverrides,
  selectedCategories,
  onSelectionChange,
}: {
  pieChart: PreparedPieChartPayload
  chartHeight: number
  overrides: PieChartChartOverrides
  defaultOverrides: PieChartChartOverrides
  selectedCategories: PieChartSelectionValue[]
  onSelectionChange: (categories: PieChartSelectionValue[]) => void
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)
  const viewRef = useRef<VegaEmbedResult | null>(null)
  const onSelectionChangeRef = useRef(onSelectionChange)
  const chartTheme = useAssetChartTheme()
  const displaySlices = useMemo(
    () => preparePieChartDisplaySlices(pieChart, overrides, defaultOverrides),
    [defaultOverrides, overrides, pieChart],
  )

  onSelectionChangeRef.current = onSelectionChange

  const spec = useMemo(
    () => buildPieChartVegaLiteSpec(
      displaySlices,
      selectedCategories,
      chartTheme,
      chartHeight,
      overrides,
      defaultOverrides,
    ),
    [chartHeight, chartTheme, defaultOverrides, displaySlices, overrides, selectedCategories],
  )

  useEffect(() => {
    if (!containerRef.current) {
      return
    }
    let viewResult: VegaEmbedResult | null = null
    let disposed = false

    async function renderChart() {
      setChartError(null)
      try {
        const result = await embed(containerRef.current as HTMLElement, spec, {
          actions: false,
          defaultStyle: false,
          renderer: 'svg',
          tooltip: true,
        })
        if (disposed) {
          result.finalize()
          return
        }
        viewResult = result
        viewRef.current = result
        const handleClick = (_event: Event, item: unknown) => {
          const clickedValues = parsePieChartClickedSliceValues(item)
          if (clickedValues !== null) {
            onSelectionChangeRef.current(
              togglePieChartSelection(selectedCategories, clickedValues, eventHasShiftKey(_event)),
            )
            return
          }
          if (selectedCategories.length) {
            onSelectionChangeRef.current([])
          }
        }
        const handleDoubleClick = (event: Event) => {
          event.preventDefault()
          event.stopPropagation()
          if (selectedCategories.length) {
            onSelectionChangeRef.current([])
          }
        }
        result.view.addEventListener('click', handleClick)
        result.view.addEventListener('dblclick', handleDoubleClick)
      } catch (error) {
        if (!disposed) {
          setChartError(error instanceof Error ? error.message : 'Could not render the pie chart view.')
        }
      }
    }

    void renderChart()

    return () => {
      disposed = true
      viewResult?.finalize()
      viewResult = null
      viewRef.current = null
    }
  }, [selectedCategories, spec])

  useEffect(() => {
    const result = viewRef.current
    if (result === null) {
      return
    }
    result.view.height(chartHeight)
    void result.view.runAsync()
  }, [chartHeight])

  if (!displaySlices.length) {
    return (
      <div className="asset-panel-placeholder">
        <p>No non-null rows match the current pie chart filters.</p>
      </div>
    )
  }

  if (chartError) {
    return (
      <div className="asset-panel-placeholder error">
        <p>{chartError}</p>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="asset-histogram-chart-shell"
      style={{ '--asset-dataviz-height': `${chartHeight}px` } as CSSProperties}
    />
  )
}

function ScatterPlotChart({
  scatterPlot,
  chartHeight,
  overrides,
  defaultOverrides,
  selectedBounds,
  selectedPointRowIndex,
  onSelectionChange,
  onPointSelectionChange,
}: {
  scatterPlot: PreparedScatterPlotPayload
  chartHeight: number
  overrides: ScatterPlotChartOverrides
  defaultOverrides: ScatterPlotChartOverrides
  selectedBounds: ScatterPlotSelectionBounds | null
  selectedPointRowIndex: number | null
  onSelectionChange: (bounds: ScatterPlotSelectionBounds | null) => void
  onPointSelectionChange: (rowIndex: number | null) => void
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)
  const [highlightedLegend, setHighlightedLegend] = useState<ScatterPlotLegendSelection | null>(null)
  const viewRef = useRef<VegaEmbedResult | null>(null)
  const selectedBoundsRef = useRef<ScatterPlotSelectionBounds | null>(selectedBounds)
  const pendingXRangeRef = useRef<HistogramSelectionRange | null>(selectedBounds?.x ?? null)
  const pendingYRangeRef = useRef<HistogramSelectionRange | null>(selectedBounds?.y ?? null)
  const onSelectionChangeRef = useRef(onSelectionChange)
  const selectedPointRowIndexRef = useRef<number | null>(selectedPointRowIndex)
  const onPointSelectionChangeRef = useRef(onPointSelectionChange)
  const chartTheme = useAssetChartTheme()

  selectedBoundsRef.current = selectedBounds
  pendingXRangeRef.current = selectedBounds?.x ?? null
  pendingYRangeRef.current = selectedBounds?.y ?? null
  onSelectionChangeRef.current = onSelectionChange
  selectedPointRowIndexRef.current = selectedPointRowIndex
  onPointSelectionChangeRef.current = onPointSelectionChange

  useEffect(() => {
    setHighlightedLegend(null)
  }, [scatterPlot])

  const spec = useMemo(
    () => buildScatterPlotVegaLiteSpec(
      scatterPlot,
      selectedBounds,
      selectedPointRowIndex,
      highlightedLegend,
      chartTheme,
      chartHeight,
      overrides,
      defaultOverrides,
    ),
    [
      chartTheme,
      chartHeight,
      defaultOverrides,
      highlightedLegend?.field,
      highlightedLegend?.value,
      overrides,
      scatterPlot,
      selectedBounds?.x.lower,
      selectedBounds?.x.upper,
      selectedBounds?.y.lower,
      selectedBounds?.y.upper,
      selectedPointRowIndex,
    ],
  )

  useEffect(() => {
    if (!containerRef.current) {
      return
    }
    let viewResult: VegaEmbedResult | null = null
    let disposed = false
    const handleWindowPointerUp = () => {
      const draftBounds = combineScatterPlotSelection(pendingXRangeRef.current, pendingYRangeRef.current)
      if (scatterPlotSelectionsEqual(draftBounds, selectedBoundsRef.current)) {
        return
      }
      onSelectionChangeRef.current(draftBounds)
    }

    async function renderChart() {
      setChartError(null)
      try {
        const result = await embed(containerRef.current as HTMLElement, spec, {
          actions: false,
          defaultStyle: false,
          renderer: 'svg',
          tooltip: true,
        })
        if (disposed) {
          result.finalize()
          return
        }
        viewResult = result
        viewRef.current = result
        const handleXSelectionSignal = (_name: string, value: unknown) => {
          pendingXRangeRef.current = parseSelectionRangeSignal(value)
        }
        const handleYSelectionSignal = (_name: string, value: unknown) => {
          pendingYRangeRef.current = parseSelectionRangeSignal(value)
        }
        const handleClearSelection = () => {
          pendingXRangeRef.current = null
          pendingYRangeRef.current = null
          setHighlightedLegend(null)
          if (selectedPointRowIndexRef.current !== null) {
            onPointSelectionChangeRef.current(null)
          }
          if (!scatterPlotSelectionsEqual(selectedBoundsRef.current, null)) {
            onSelectionChangeRef.current(null)
          }
        }
        const handleClick = (_event: Event, item: unknown) => {
          const pointRowIndex = parseScatterPlotClickedPointRowIndex(item)
          if (pointRowIndex !== null) {
            onPointSelectionChangeRef.current(pointRowIndex === selectedPointRowIndexRef.current ? null : pointRowIndex)
            return
          }
          const legendSelection = parseScatterPlotLegendSelection(item, scatterPlot)
          if (legendSelection !== null) {
            setHighlightedLegend((current) => (
              current !== null
              && current.field === legendSelection.field
              && current.value === legendSelection.value
                ? null
                : legendSelection
            ))
            return
          }
          if (selectedPointRowIndexRef.current !== null) {
            onPointSelectionChangeRef.current(null)
          }
        }
        result.view.addSignalListener('selection_bounds_x', handleXSelectionSignal)
        result.view.addSignalListener('selection_bounds_y', handleYSelectionSignal)
        result.view.addEventListener('click', handleClick)
        result.view.addEventListener('dblclick', handleClearSelection)
        window.addEventListener('pointerup', handleWindowPointerUp)
      } catch (error) {
        if (!disposed) {
          setChartError(error instanceof Error ? error.message : 'Could not render the scatter plot view.')
        }
      }
    }

    void renderChart()

    return () => {
      disposed = true
      window.removeEventListener('pointerup', handleWindowPointerUp)
      viewResult?.finalize()
      viewResult = null
      viewRef.current = null
    }
  }, [spec])

  useEffect(() => {
    const result = viewRef.current
    if (result === null) {
      return
    }
    result.view.height(chartHeight)
    void result.view.runAsync()
  }, [chartHeight])

  if (!scatterPlot.points.length) {
    return (
      <div className="asset-panel-placeholder">
        <p>No numeric rows match the current scatter plot filters.</p>
      </div>
    )
  }

  if (chartError) {
    return (
      <div className="asset-panel-placeholder error">
        <p>{chartError}</p>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="asset-histogram-chart-shell"
      style={{ '--asset-dataviz-height': `${chartHeight}px` } as CSSProperties}
    />
  )
}

function buildAxisSpec(overrides: ChartAxisOverrides, defaultLabel: string) {
  const resolvedLabel = resolvedAxisLabel(overrides.label, defaultLabel)
  return {
    title: overrides.hideLabel ? null : resolvedLabel,
    titleFontSize: optionalPositiveNumberFromInput(overrides.labelSize),
    tickCount: optionalIntegerFromInput(overrides.tickCount),
    tickSize: optionalNonNegativeNumberFromInput(overrides.tickSize),
    grid: overrides.showGridLines,
    labelFlush: false,
  }
}

function buildScaleType(scale: DatavizAxisScale): 'linear' | 'log' {
  return scale === 'log' ? 'log' : 'linear'
}

function buildChartTitle(overrides: ChartTitleOverrides, defaultText: string) {
  if (overrides.hideTitle) {
    return undefined
  }
  return {
    text: overrides.text.trim() || defaultText,
    fontSize: optionalPositiveNumberFromInput(overrides.size),
    orient: overrides.position,
  }
}

function buildChartPadding(title: ChartTitleOverrides) {
  if (title.hideTitle) {
    return { top: 8, bottom: 8, left: 12, right: 12 }
  }
  return {
    top: title.position === 'top' ? 18 : 8,
    bottom: title.position === 'bottom' ? 18 : 8,
    left: 12,
    right: 12,
  }
}

function resolvedAxisLabel(value: string, defaultLabel: string): string {
  if (!value.trim() || value === 'X axis' || value === 'Y axis' || value === 'Rows') {
    return defaultLabel
  }
  return value
}

function buildHistogramVegaLiteSpec(
  histogram: PreparedHistogramPayload,
  theme: AssetChartTheme,
  chartHeight: number,
  overrides: HistogramChartOverrides,
  defaultOverrides: HistogramChartOverrides,
): VisualizationSpec {
  const barWidthRatio = overrides.barWidth / 100
  const borderThickness = optionalNonNegativeNumberFromInput(overrides.borderThickness) ?? 0
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    autosize: { type: 'fit-x', contains: 'padding' },
    width: 'container',
    height: chartHeight,
    background: 'transparent',
    padding: buildChartPadding(overrides.title),
    title: buildChartTitle(overrides.title, defaultOverrides.title.text),
    config: {
      view: { stroke: 'transparent' },
      title: {
        color: theme.axisTitleColor,
        offset: 18,
      },
      axis: {
        domainColor: theme.axisDomainColor,
        tickColor: theme.axisDomainColor,
        labelColor: theme.axisLabelColor,
        titleColor: theme.axisTitleColor,
        gridColor: theme.gridColor,
      },
    },
    data: {
      values: histogram.bins.map((bin) => ({
        ...bin,
        label: `${formatHistogramBound(bin.start)} to ${formatHistogramBound(bin.end)}`,
        adjusted_start: ((bin.start + bin.end) / 2) - ((bin.end - bin.start) * barWidthRatio / 2),
        adjusted_end: ((bin.start + bin.end) / 2) + ((bin.end - bin.start) * barWidthRatio / 2),
      })),
    },
    params: [
      {
        name: 'selected_bars',
        select: {
          type: 'point',
          fields: ['index'],
          on: 'click[event.button===999]',
          clear: false,
        },
      },
      {
        name: 'brush_selection',
        select: {
          type: 'interval',
          encodings: ['x'],
          translate: false,
          zoom: false,
          clear: false,
        },
      },
    ],
    mark: {
      type: 'rect',
      cornerRadius: 3,
      stroke: borderThickness > 0 ? '#1d4ed8' : undefined,
      strokeWidth: borderThickness,
    },
    encoding: {
      x: {
        field: 'adjusted_start',
        type: 'quantitative',
        axis: buildAxisSpec(overrides.xAxis, defaultOverrides.xAxis.label),
        scale: {
          ...(histogram.domain ? {
            domain: [histogram.domain.min, histogram.domain.max],
            nice: false,
            zero: false,
          } : {}),
          type: buildScaleType(overrides.xAxis.scale),
        },
      },
      x2: { field: 'adjusted_end' },
      y: {
        field: 'count',
        type: 'quantitative',
        axis: buildAxisSpec(overrides.yAxis, defaultOverrides.yAxis.label),
        scale: {
          type: buildScaleType(overrides.yAxis.scale),
        },
      },
      ...(overrides.yAxis.scale === 'log' ? {} : { y2: { datum: 0 } }),
      color: {
        condition: [
          {
            test: 'data("selected_bars_store").length === 0 && data("brush_selection_store").length === 0',
            value: '#2563eb',
          },
          { param: 'selected_bars', empty: false, value: '#2563eb' },
          {
            test: `isArray(${HISTOGRAM_BRUSH_SIGNAL_NAME}) && datum.end > ${HISTOGRAM_BRUSH_SIGNAL_NAME}[0] && datum.start < ${HISTOGRAM_BRUSH_SIGNAL_NAME}[1]`,
            value: '#2563eb',
          },
        ],
        value: '#94a3b8',
      },
      opacity: {
        condition: [
          {
            test: 'data("selected_bars_store").length === 0 && data("brush_selection_store").length === 0',
            value: 0.95,
          },
          { param: 'selected_bars', empty: false, value: 0.95 },
          {
            test: `isArray(${HISTOGRAM_BRUSH_SIGNAL_NAME}) && datum.end > ${HISTOGRAM_BRUSH_SIGNAL_NAME}[0] && datum.start < ${HISTOGRAM_BRUSH_SIGNAL_NAME}[1]`,
            value: 0.95,
          },
        ],
        value: 0.3,
      },
      tooltip: [
        { field: 'label', type: 'nominal' as const, title: 'Range' },
        { field: 'count', type: 'quantitative' as const, title: 'Rows' },
      ],
    },
  }
}

function buildPieChartVegaLiteSpec(
  displaySlices: PieChartDisplaySlice[],
  selectedCategories: PieChartSelectionValue[],
  theme: AssetChartTheme,
  chartHeight: number,
  overrides: PieChartChartOverrides,
  defaultOverrides: PieChartChartOverrides,
): VisualizationSpec {
  const titlePadding = overrides.title.hideTitle ? 8 : 52
  const outerRadius = Math.max(80, Math.floor((chartHeight - titlePadding) / 2))
  const innerRadius = outerRadius * pieChartInnerRadiusValue(overrides, defaultOverrides)
  const labelPosition = pieChartLabelPositionValue(overrides, defaultOverrides)
  const labelThreshold = pieChartPercentageValue(overrides.labelThreshold, defaultOverrides.labelThreshold, 5, 0, 100)
  const borderThickness = optionalNonNegativeNumberFromInput(overrides.borderThickness) ?? 3
  const labelSize = optionalPositiveNumberFromInput(overrides.labelSize) ?? optionalPositiveNumberFromInput(defaultOverrides.labelSize) ?? 12
  const opaqueLabelColor = opaqueColor(theme.axisTitleColor)
  const totalCount = displaySlices.reduce((sum, slice) => sum + slice.count, 0)
  let cumulativeCount = 0
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    autosize: { type: 'fit-x', contains: 'padding' },
    width: 'container',
    height: chartHeight,
    background: 'transparent',
    padding: buildChartPadding(overrides.title),
    title: buildChartTitle(overrides.title, defaultOverrides.title.text),
    config: {
      view: { stroke: 'transparent' },
      title: {
        color: theme.axisTitleColor,
        offset: 18,
      },
    },
    data: {
      values: displaySlices.map((slice, index) => {
        const labelText = pieChartLabelText(slice.label, slice.share, overrides.showPercentages)
        const midAngle = totalCount > 0 ? (((cumulativeCount + (slice.count / 2)) / totalCount) * Math.PI * 2) : 0
        const labelRadius = pieChartLabelRadius(labelPosition, innerRadius, outerRadius, labelText, labelSize, midAngle)
        cumulativeCount += slice.count
        return {
          label: slice.label,
          count: slice.count,
          share: slice.share,
          share_label: formatPieChartShare(slice.share),
          color: slice.color,
          raw_values: slice.rawValues,
          slice_order: index,
          mid_angle: midAngle,
          is_selected: pieChartSelectionIncludes(selectedCategories, slice.rawValues),
          show_label: (slice.share * 100) >= labelThreshold,
          label_text: labelText,
          label_radius: labelRadius,
        }
      }),
    },
    layer: [
      {
        mark: {
          type: 'arc',
          innerRadius,
          outerRadius,
          stroke: opaqueLabelColor,
          strokeWidth: borderThickness,
        },
        encoding: {
          theta: { field: 'count', type: 'quantitative', stack: true },
          order: { field: 'slice_order', type: 'quantitative', sort: 'ascending' },
          color: {
            field: 'color',
            type: 'nominal',
            scale: null,
            legend: null,
          },
          opacity: {
            condition: {
              test: selectedCategories.length ? 'datum.is_selected' : 'true',
              value: 0.96,
            },
            value: selectedCategories.length ? 0.34 : 0.96,
          },
          tooltip: [
            { field: 'label', type: 'nominal' as const, title: 'Category' },
            { field: 'count', type: 'quantitative' as const, title: 'Rows' },
            { field: 'share_label', type: 'nominal' as const, title: 'Share' },
          ],
        },
      },
      {
        transform: [{ filter: 'datum.show_label' }],
        mark: {
          type: 'text',
          fill: theme.axisTitleColor,
          fontSize: labelSize,
          fontWeight: 600,
          align: 'center',
          baseline: 'middle',
        },
        encoding: {
          theta: { field: 'mid_angle', type: 'quantitative', scale: null },
          radius: { field: 'label_radius', type: 'quantitative', scale: null },
          text: { field: 'label_text', type: 'nominal' },
        },
      },
    ],
  }
}

function buildScatterPlotVegaLiteSpec(
  scatterPlot: PreparedScatterPlotPayload,
  selectedBounds: ScatterPlotSelectionBounds | null,
  selectedPointRowIndex: number | null,
  highlightedLegend: ScatterPlotLegendSelection | null,
  theme: AssetChartTheme,
  chartHeight: number,
  overrides: ScatterPlotChartOverrides,
  defaultOverrides: ScatterPlotChartOverrides,
): VisualizationSpec {
  const sizeType: 'quantitative' | 'nominal' = scatterPlot.size_kind ?? 'nominal'
  const colorType: 'quantitative' | 'nominal' = scatterPlot.color_kind ?? 'nominal'
  const showLegend = overrides.showLegend
  const minPointSize = optionalPositiveNumberFromInput(overrides.minPointSize)
  const maxPointSize = optionalPositiveNumberFromInput(overrides.maxPointSize)
  const tooltip = [
    { field: 'x', type: 'quantitative' as const, title: scatterPlot.x_column },
    { field: 'y', type: 'quantitative' as const, title: scatterPlot.y_column },
    ...(scatterPlot.shape_column ? [{ field: 'shape', type: 'nominal' as const, title: scatterPlot.shape_column }] : []),
    ...(scatterPlot.size_column ? [{ field: 'size', type: sizeType as 'quantitative' | 'nominal', title: scatterPlot.size_column }] : []),
    ...(scatterPlot.color_column ? [{ field: 'color', type: colorType as 'quantitative' | 'nominal', title: scatterPlot.color_column }] : []),
  ]
  const sizeEncoding = scatterPlot.size_column
    ? {
      field: 'size',
      type: sizeType as 'quantitative' | 'nominal',
      title: scatterPlot.size_column,
      legend: showLegend ? undefined : null,
      scale: {
        ...(minPointSize !== undefined ? { rangeMin: minPointSize } : {}),
        ...(maxPointSize !== undefined ? { rangeMax: maxPointSize } : {}),
      },
    }
    : undefined
  const shapeEncoding = scatterPlot.shape_column
    ? {
      field: 'shape',
      type: 'nominal' as const,
      title: scatterPlot.shape_column,
      legend: showLegend ? undefined : null,
    }
    : undefined
  const colorEncoding = scatterPlot.color_column
    ? {
      field: 'color',
      type: colorType as 'quantitative' | 'nominal',
      title: scatterPlot.color_column,
      legend: showLegend ? undefined : null,
    }
    : {
      condition: [
        {
          param: 'selection_bounds',
          empty: false,
          value: theme.selectionColor,
        },
        {
          test: 'data("selection_bounds_store").length === 0 && datum.is_persistently_emphasized',
          value: theme.selectionColor,
        },
      ],
      value: theme.fallbackPointColor,
    }
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    autosize: { type: 'fit-x', contains: 'padding' },
    width: 'container',
    height: chartHeight,
    background: 'transparent',
    padding: buildChartPadding(overrides.title),
    title: buildChartTitle(overrides.title, defaultOverrides.title.text),
    config: {
      view: { stroke: 'transparent' },
      title: {
        color: theme.axisTitleColor,
        offset: 18,
      },
      axis: {
        domainColor: theme.axisDomainColor,
        tickColor: theme.axisDomainColor,
        labelColor: theme.axisLabelColor,
        titleColor: theme.axisTitleColor,
        gridColor: theme.gridColor,
      },
      legend: {
        labelColor: theme.legendLabelColor,
        titleColor: theme.legendTitleColor,
      },
    },
    data: {
      values: scatterPlot.points.map((point) => ({
        ...point,
        is_persistently_emphasized: (
          (selectedPointRowIndex === null || point.row_index === selectedPointRowIndex)
          &&
          (selectedBounds === null ? true : (
            point.x >= selectedBounds.x.lower
            && point.x <= selectedBounds.x.upper
            && point.y >= selectedBounds.y.lower
            && point.y <= selectedBounds.y.upper
          ))
          && (highlightedLegend === null ? true : point[highlightedLegend.field] === highlightedLegend.value)
        ),
      })),
    },
    params: [
      {
        name: 'selection_bounds',
        select: {
          type: 'interval',
          encodings: ['x', 'y'],
          translate: false,
          zoom: false,
          mark: {
            fill: theme.selectionColor,
            fillOpacity: 0.14,
            stroke: theme.selectionColor,
            strokeWidth: 1.5,
          },
        },
      },
    ],
    mark: {
      type: 'point',
      filled: overrides.shapeStyle === 'filled',
      size: scatterPlot.size_column ? undefined : (maxPointSize ?? minPointSize ?? 60),
    },
    encoding: {
      x: {
        field: 'x',
        type: 'quantitative',
        axis: buildAxisSpec(overrides.xAxis, defaultOverrides.xAxis.label),
        scale: {
          ...(scatterPlot.domain ? {
            domain: [scatterPlot.domain.x.min, scatterPlot.domain.x.max],
            nice: false,
            zero: false,
          } : {}),
          type: buildScaleType(overrides.xAxis.scale),
        },
      },
      y: {
        field: 'y',
        type: 'quantitative',
        axis: buildAxisSpec(overrides.yAxis, defaultOverrides.yAxis.label),
        scale: {
          ...(scatterPlot.domain ? {
            domain: [scatterPlot.domain.y.min, scatterPlot.domain.y.max],
            nice: false,
            zero: false,
          } : {}),
          type: buildScaleType(overrides.yAxis.scale),
        },
      },
      ...(shapeEncoding ? { shape: shapeEncoding } : {}),
      ...(sizeEncoding ? { size: sizeEncoding } : {}),
      color: colorEncoding,
      opacity: {
        condition: [
          {
            param: 'selection_bounds',
            empty: false,
            value: 0.92,
          },
          {
            test: 'data("selection_bounds_store").length === 0 && datum.is_persistently_emphasized',
            value: 0.92,
          },
        ],
        value: 0.18,
      },
      tooltip,
    },
  }
}

function parseHistogramClickedRange(item: unknown): HistogramSelectionRange | null {
  if (!item || typeof item !== 'object') {
    return null
  }
  const datum = 'datum' in item ? (item as { datum?: unknown }).datum : null
  if (!datum || typeof datum !== 'object') {
    return null
  }
  const record = datum as Record<string, unknown>
  const lower = typeof record.start === 'number' ? record.start : Number(record.start)
  const upper = typeof record.end === 'number' ? record.end : Number(record.end)
  if (!Number.isFinite(lower) || !Number.isFinite(upper)) {
    return null
  }
  return lower <= upper ? { lower, upper } : { lower: upper, upper: lower }
}

function parseHistogramClickedBarIndex(item: unknown, histogram: PreparedHistogramPayload): number | null {
  const clickedRange = parseHistogramClickedRange(item)
  if (clickedRange === null) {
    return null
  }
  const index = histogram.bins.findIndex((bin) => bin.start === clickedRange.lower && bin.end === clickedRange.upper)
  return index >= 0 ? index : null
}

function histogramSelectionRangesFromIndexes(
  histogram: PreparedHistogramPayload,
  selectedBarIndexes: number[],
): HistogramSelectionRange[] {
  const indexes = [...new Set(selectedBarIndexes)]
    .filter((index) => Number.isInteger(index) && index >= 0 && index < histogram.bins.length)
    .sort((left, right) => left - right)
  if (!indexes.length) {
    return []
  }
  const ranges: HistogramSelectionRange[] = []
  let startIndex = indexes[0] ?? 0
  let previousIndex = startIndex
  for (let position = 1; position < indexes.length; position += 1) {
    const currentIndex = indexes[position]
    if (currentIndex === undefined) {
      continue
    }
    if (currentIndex !== previousIndex + 1) {
      const startBin = histogram.bins[startIndex]
      const endBin = histogram.bins[previousIndex]
      if (startBin && endBin) {
        ranges.push({ lower: startBin.start, upper: endBin.end })
      }
      startIndex = currentIndex
    }
    previousIndex = currentIndex
  }
  const finalStartBin = histogram.bins[startIndex]
  const finalEndBin = histogram.bins[previousIndex]
  if (finalStartBin && finalEndBin) {
    ranges.push({ lower: finalStartBin.start, upper: finalEndBin.end })
  }
  return ranges
}

function parseHistogramSelectedBarsStore(value: unknown): number[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .map((entry) => {
      if (!entry || typeof entry !== 'object') {
        return null
      }
      const values = (entry as { values?: unknown }).values
      if (!Array.isArray(values) || values.length !== 1) {
        return null
      }
      const index = values[0]
      return typeof index === 'number' && Number.isInteger(index) && index >= 0 ? index : null
    })
    .filter((index): index is number => index !== null)
    .sort((left, right) => left - right)
}

function parseSelectionRangeSignal(value: unknown): HistogramSelectionRange | null {
  if (!Array.isArray(value) || value.length !== 2) {
    return null
  }
  const lower = typeof value[0] === 'number' ? value[0] : Number(value[0])
  const upper = typeof value[1] === 'number' ? value[1] : Number(value[1])
  if (!Number.isFinite(lower) || !Number.isFinite(upper)) {
    return null
  }
  return lower <= upper ? { lower, upper } : { lower: upper, upper: lower }
}

function combineScatterPlotSelection(
  xRange: HistogramSelectionRange | null,
  yRange: HistogramSelectionRange | null,
): ScatterPlotSelectionBounds | null {
  if (!xRange || !yRange) {
    return null
  }
  return { x: xRange, y: yRange }
}

function parseScatterPlotClickedPointRowIndex(item: unknown): number | null {
  if (!item || typeof item !== 'object') {
    return null
  }
  const datum = 'datum' in item ? (item as { datum?: unknown }).datum : null
  if (!datum || typeof datum !== 'object') {
    return null
  }
  const rowIndex = (datum as Record<string, unknown>).row_index
  if (typeof rowIndex === 'number' && Number.isInteger(rowIndex) && rowIndex >= 0) {
    return rowIndex
  }
  return null
}

function parseScatterPlotLegendSelection(
  item: unknown,
  scatterPlot: PreparedScatterPlotPayload,
): ScatterPlotLegendSelection | null {
  if (!item || typeof item !== 'object') {
    return null
  }
  const record = item as { datum?: unknown; mark?: { role?: unknown } }
  const role = typeof record.mark?.role === 'string' ? record.mark.role : ''
  if (!role.includes('legend')) {
    return null
  }
  const value = extractScatterLegendDatumValue(record.datum)
  if (value === null) {
    return null
  }
  const candidateFields: Array<ScatterPlotLegendSelection['field']> = []
  if (scatterPlot.color_column !== null && scatterPlot.color_kind === 'nominal') {
    candidateFields.push('color')
  }
  if (scatterPlot.shape_column !== null) {
    candidateFields.push('shape')
  }
  if (scatterPlot.size_column !== null && scatterPlot.size_kind === 'nominal') {
    candidateFields.push('size')
  }
  for (const field of candidateFields) {
    if (scatterPlot.points.some((point) => point[field] === value)) {
      return { field, value }
    }
  }
  return null
}

function extractScatterLegendDatumValue(datum: unknown): string | number | boolean | null {
  if (typeof datum === 'string' || typeof datum === 'number' || typeof datum === 'boolean') {
    return datum
  }
  if (!datum || typeof datum !== 'object') {
    return null
  }
  const record = datum as Record<string, unknown>
  for (const key of ['value', 'label']) {
    const candidate = record[key]
    if (typeof candidate === 'string' || typeof candidate === 'number' || typeof candidate === 'boolean') {
      return candidate
    }
  }
  if ('datum' in record) {
    return extractScatterLegendDatumValue(record.datum)
  }
  return null
}

function parsePieChartClickedSliceValues(item: unknown): PieChartSelectionValue[] | null {
  if (!item || typeof item !== 'object') {
    return null
  }
  const datum = 'datum' in item ? (item as { datum?: unknown }).datum : null
  if (!datum || typeof datum !== 'object') {
    return null
  }
  const rawValues = (datum as Record<string, unknown>).raw_values
  if (!Array.isArray(rawValues)) {
    return null
  }
  const values = rawValues.filter((value): value is PieChartSelectionValue => (
    typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
  ))
  return values.length ? values : null
}

function histogramSelectedBarIndexesFromBrushRange(
  histogram: PreparedHistogramPayload,
  brushRange: HistogramSelectionRange | null,
): number[] {
  if (brushRange === null) {
    return []
  }
  return histogram.bins
    .map((bin, index) => ({ bin, index }))
    .filter(({ bin }) => bin.end > brushRange.lower && bin.start < brushRange.upper)
    .map(({ index }) => index)
}

async function syncHistogramSelectedBars(result: VegaEmbedResult, selectedBarIndexes: number[]) {
  const nextIndexes = [...new Set(selectedBarIndexes)]
    .filter((index) => Number.isInteger(index) && index >= 0)
    .sort((left, right) => left - right)
  const currentIndexes = parseHistogramSelectedBarsStore(result.view.data('selected_bars_store'))
  if (histogramBarIndexArraysEqual(currentIndexes, nextIndexes)) {
    return
  }
  result.view.data(
    'selected_bars_store',
    nextIndexes.map((index) => ({
      unit: '',
      fields: [{ type: 'E', field: 'index' }],
      values: [index],
    })),
  )
  await result.view.runAsync()
}

async function clearHistogramBrush(result: VegaEmbedResult) {
  if (!result.view.data('brush_selection_store').length) {
    return
  }
  result.view.data('brush_selection_store', [])
  await result.view.runAsync()
}

function histogramBarIndexArraysEqual(left: number[], right: number[]): boolean {
  if (left.length !== right.length) {
    return false
  }
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) {
      return false
    }
  }
  return true
}

function eventHasShiftKey(event: Event): boolean {
  return 'shiftKey' in event && Boolean((event as MouseEvent).shiftKey)
}

function toggleHistogramBarIndex(selectedBarIndexes: number[], clickedIndex: number): number[] {
  const uniqueIndexes = [...new Set(selectedBarIndexes)].sort((left, right) => left - right)
  if (uniqueIndexes.includes(clickedIndex)) {
    return uniqueIndexes.filter((index) => index !== clickedIndex)
  }
  return [...uniqueIndexes, clickedIndex].sort((left, right) => left - right)
}

function rangesEqual(left: HistogramSelectionRange | null, right: HistogramSelectionRange | null): boolean {
  if (!left && !right) {
    return true
  }
  if (!left || !right) {
    return false
  }
  return left.lower === right.lower && left.upper === right.upper
}

function scatterPlotSelectionsEqual(left: ScatterPlotSelectionBounds | null, right: ScatterPlotSelectionBounds | null): boolean {
  if (!left && !right) {
    return true
  }
  if (!left || !right) {
    return false
  }
  return rangesEqual(left.x, right.x) && rangesEqual(left.y, right.y)
}

function preparePieChartDisplaySlices(
  pieChart: PreparedPieChartPayload | null,
  overrides: PieChartChartOverrides,
  defaultOverrides: PieChartChartOverrides,
): PieChartDisplaySlice[] {
  if (pieChart === null) {
    return []
  }
  const mergeThreshold = pieChartPercentageValue(overrides.mergeThreshold, defaultOverrides.mergeThreshold, 0, 0, 100)
  const mergedCategoryLabel = overrides.mergedCategoryLabel.trim() || defaultOverrides.mergedCategoryLabel || 'Others'
  const baseSlices = pieChart.slices.map((slice) => ({
    key: pieChartSelectionKey([slice.value]),
    label: slice.label,
    count: slice.count,
    share: slice.share,
    color: slice.color,
    rawValues: [slice.value],
    isMerged: false,
  }))
  if (mergeThreshold <= 0) {
    return sortPieChartDisplaySlices(baseSlices)
  }
  const retainedSlices = baseSlices.filter((slice) => slice.share * 100 >= mergeThreshold)
  const mergedSlices = baseSlices.filter((slice) => slice.share * 100 < mergeThreshold)
  if (!mergedSlices.length) {
    return sortPieChartDisplaySlices(retainedSlices)
  }
  if (!overrides.showMergedCategory) {
    return sortPieChartDisplaySlices(retainedSlices)
  }
  return sortPieChartDisplaySlices([
    ...retainedSlices,
    {
      key: `merged:${pieChartSelectionKey(mergedSlices.flatMap((slice) => slice.rawValues))}`,
      label: mergedCategoryLabel,
      count: mergedSlices.reduce((sum, slice) => sum + slice.count, 0),
      share: mergedSlices.reduce((sum, slice) => sum + slice.share, 0),
      color: '#94a3b8',
      rawValues: mergedSlices.flatMap((slice) => slice.rawValues),
      isMerged: true,
    },
  ])
}

function sortPieChartDisplaySlices(slices: PieChartDisplaySlice[]): PieChartDisplaySlice[] {
  return [...slices].sort((left, right) => {
    if (left.isMerged !== right.isMerged) {
      return left.isMerged ? 1 : -1
    }
    if (right.count !== left.count) {
      return right.count - left.count
    }
    return left.label.localeCompare(right.label)
  })
}

function pieChartVisibleSelection(
  selectedCategories: PieChartSelectionValue[],
  displaySlices: PieChartDisplaySlice[],
): PieChartSelectionValue[] {
  if (!selectedCategories.length) {
    return []
  }
  const normalizedSelection = normalizePieChartSelection(selectedCategories)
  const visibleValues = new Set(
    displaySlices.flatMap((slice) => slice.rawValues).map((value) => pieChartSelectionPrimitiveKey(value)),
  )
  return normalizedSelection.filter((value) => visibleValues.has(pieChartSelectionPrimitiveKey(value)))
}

function pieChartSelectionIncludes(
  selectedCategories: PieChartSelectionValue[],
  sliceValues: PieChartSelectionValue[],
): boolean {
  if (!selectedCategories.length || !sliceValues.length) {
    return false
  }
  const selectedKeys = new Set(
    normalizePieChartSelection(selectedCategories).map((value) => pieChartSelectionPrimitiveKey(value)),
  )
  return normalizePieChartSelection(sliceValues)
    .every((value) => selectedKeys.has(pieChartSelectionPrimitiveKey(value)))
}

function togglePieChartSelection(
  selectedCategories: PieChartSelectionValue[],
  clickedValues: PieChartSelectionValue[],
  additive: boolean,
): PieChartSelectionValue[] {
  const normalizedClickedValues = normalizePieChartSelection(clickedValues)
  if (!additive) {
    return pieChartSelectionsEqual(normalizedClickedValues, selectedCategories) ? [] : normalizedClickedValues
  }
  const clickedKeys = new Set(normalizedClickedValues.map((value) => pieChartSelectionPrimitiveKey(value)))
  if (normalizedClickedValues.every((value) => selectedCategories.some((selected) => pieChartSelectionPrimitiveKey(selected) === pieChartSelectionPrimitiveKey(value)))) {
    return normalizePieChartSelection(
      selectedCategories.filter((value) => !clickedKeys.has(pieChartSelectionPrimitiveKey(value))),
    )
  }
  return normalizePieChartSelection([...selectedCategories, ...normalizedClickedValues])
}

function pieChartSelectionsEqual(left: PieChartSelectionValue[], right: PieChartSelectionValue[]): boolean {
  const normalizedLeft = normalizePieChartSelection(left)
  const normalizedRight = normalizePieChartSelection(right)
  if (normalizedLeft.length !== normalizedRight.length) {
    return false
  }
  for (let index = 0; index < normalizedLeft.length; index += 1) {
    if (pieChartSelectionPrimitiveKey(normalizedLeft[index]) !== pieChartSelectionPrimitiveKey(normalizedRight[index])) {
      return false
    }
  }
  return true
}

function normalizePieChartSelection(values: PieChartSelectionValue[]): PieChartSelectionValue[] {
  return [...new Map(values.map((value) => [pieChartSelectionPrimitiveKey(value), value])).values()]
    .sort((left, right) => pieChartSelectionPrimitiveKey(left).localeCompare(pieChartSelectionPrimitiveKey(right)))
}

function pieChartSelectionKey(values: PieChartSelectionValue[]): string {
  return normalizePieChartSelection(values)
    .map((value) => pieChartSelectionPrimitiveKey(value))
    .join('|')
}

function pieChartSelectionPrimitiveKey(value: PieChartSelectionValue): string {
  return `${typeof value}:${String(value)}`
}

function pieChartPercentageValue(
  currentValue: string,
  defaultValue: string,
  fallback: number,
  min: number,
  max: number,
): number {
  const parsed = optionalNumberFromInput(currentValue) ?? optionalNumberFromInput(defaultValue) ?? fallback
  return clampNumberToRange(parsed, fallback, min, max)
}

function pieChartInnerRadiusValue(overrides: PieChartChartOverrides, defaultOverrides: PieChartChartOverrides): number {
  return pieChartPercentageValue(overrides.innerRadius, defaultOverrides.innerRadius, 0.5, 0, 1)
}

function pieChartInnerRadiusPercentage(overrides: PieChartChartOverrides, defaultOverrides: PieChartChartOverrides): number {
  return clampNumberToRange(Math.round(pieChartInnerRadiusValue(overrides, defaultOverrides) * 100), 50, 0, 100, true)
}

function pieChartLabelPositionValue(overrides: PieChartChartOverrides, defaultOverrides: PieChartChartOverrides): number {
  return clampNumberToRange(overrides.labelPosition, defaultOverrides.labelPosition, 0, 200, true)
}

function pieChartSliderPercentage(currentValue: string, defaultValue: string, fallback: number): number {
  return clampNumberToRange(
    Math.round(pieChartPercentageValue(currentValue, defaultValue, fallback, 0, 100)),
    fallback,
    0,
    100,
    true,
  )
}

function useAssetChartTheme(): AssetChartTheme {
  const [themeVersion, setThemeVersion] = useState(0)

  useEffect(() => {
    if (typeof MutationObserver === 'undefined' || typeof document === 'undefined') {
      return
    }
    const root = document.documentElement
    const observer = new MutationObserver(() => {
      setThemeVersion((current) => current + 1)
    })
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] })
    return () => observer.disconnect()
  }, [])

  return useMemo(() => {
    if (typeof window === 'undefined' || typeof document === 'undefined') {
      return {
        axisDomainColor: '#94a3b8',
        axisLabelColor: '#64748b',
        axisTitleColor: '#475569',
        gridColor: 'rgba(148, 163, 184, 0.18)',
        legendLabelColor: '#64748b',
        legendTitleColor: '#475569',
        selectionColor: '#2563eb',
        fallbackPointColor: '#94a3b8',
      }
    }
    const styles = window.getComputedStyle(document.documentElement)
    const read = (name: string, fallback: string) => styles.getPropertyValue(name).trim() || fallback
    return {
      axisDomainColor: read('--line', 'rgba(148, 163, 184, 0.42)'),
      axisLabelColor: read('--muted', '#64748b'),
      axisTitleColor: read('--ink', '#475569'),
      gridColor: read('--line', 'rgba(148, 163, 184, 0.18)'),
      legendLabelColor: read('--muted', '#64748b'),
      legendTitleColor: read('--ink', '#475569'),
      selectionColor: read('--run', '#2563eb'),
      fallbackPointColor: read('--muted', '#94a3b8'),
    }
  }, [themeVersion])
}

function formatHistogramBound(value: number): string {
  if (Number.isInteger(value)) {
    return String(value)
  }
  return value.toFixed(2).replace(/\.00$/, '').replace(/(\.[1-9])0$/, '$1')
}

function formatPieChartShare(value: number): string {
  const percentage = value * 100
  if (percentage >= 10 || Number.isInteger(percentage)) {
    return `${Math.round(percentage)}%`
  }
  return `${percentage.toFixed(1).replace(/\.0$/, '')}%`
}

function pieChartLabelText(label: string, share: number, showPercentages: boolean): string {
  if (!showPercentages) {
    return label
  }
  return `${label} (${(share * 100).toFixed(1)}%)`
}

function pieChartLabelRadius(
  labelPosition: number,
  innerRadius: number,
  outerRadius: number,
  labelText: string,
  labelSize: number,
  theta: number,
): number {
  const requestedRadius = outerRadius * (labelPosition / 100)
  const innerRadiusPercentage = outerRadius > 0 ? (innerRadius / outerRadius) * 100 : 0
  const labelExtent = estimatedPieChartLabelRadialExtent(labelText, labelSize, theta)
  if (labelPosition >= 100) {
    return requestedRadius + labelExtent
  }
  if (labelPosition < innerRadiusPercentage) {
    return Math.max(0, requestedRadius - labelExtent)
  }
  return requestedRadius
}

function estimatedPieChartLabelRadialExtent(labelText: string, labelSize: number, theta: number): number {
  const lines = labelText.split(/\r?\n/)
  const longestLineLength = lines.reduce((maxLength, line) => Math.max(maxLength, line.length), 0)
  const halfWidth = longestLineLength * labelSize * 0.31
  const halfHeight = Math.max(lines.length, 1) * labelSize * 0.55
  return (halfWidth * Math.abs(Math.sin(theta))) + (halfHeight * Math.abs(Math.cos(theta)))
}

function opaqueColor(color: string): string {
  const rgbMatch = color.trim().match(/^rgba\(([^)]+)\)$/i)
  if (!rgbMatch) {
    return color
  }
  const [red, green, blue] = rgbMatch[1].split(',').map((part) => part.trim())
  return `rgb(${red}, ${green}, ${blue})`
}

function PreparedTable({
  table,
  columns,
  activeSort,
  activeFilters,
  disabled,
  onToggleSort,
  onApplyFilter,
  onRemoveFilter,
}: {
  table: PreparedTablePayload
  columns: ModifierColumn[]
  activeSort: AssetSort | null
  activeFilters: AssetFilter[]
  disabled: boolean
  onToggleSort: (column: string) => void
  onApplyFilter: (filter: AssetFilter) => void
  onRemoveFilter: (columnId: string) => void
}) {
  const [openFilterColumnId, setOpenFilterColumnId] = useState<string | null>(null)
  const openFilterCellRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!openFilterColumnId) {
      return
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (openFilterCellRef.current && !openFilterCellRef.current.contains(event.target as globalThis.Node)) {
        setOpenFilterColumnId(null)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenFilterColumnId(null)
      }
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [openFilterColumnId])

  useEffect(() => {
    if (openFilterColumnId && !table.columns.some((column) => column.id === openFilterColumnId)) {
      setOpenFilterColumnId(null)
    }
  }, [openFilterColumnId, table.columns])

  return (
    <div className="table-wrap asset-table-wrap">
      <table className="preview-table asset-table">
        <thead>
          <tr>
            {table.columns.map((column) => {
              const filterColumn = columns.find((entry) => entry.id === column.id) ?? {
                id: column.id,
                title: column.title,
                dataType: column.data_type,
                filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
              }
              const isActive = activeSort?.column === column.id
              const activeFilter = activeFilters.find((entry) => entry.column === column.id) ?? null
              const isFilterOpen = openFilterColumnId === column.id
              return (
                <th key={column.id}>
                  <div className="asset-table-header-cell" ref={isFilterOpen ? openFilterCellRef : undefined}>
                    <div className="asset-table-header-main">
                      <div className="asset-table-header-label">
                        <span className="asset-table-column-title" title={column.title}>{column.title}</span>
                        <span className="asset-table-column-type">{column.data_type}</span>
                      </div>
                      <div className="asset-table-header-actions">
                        {column.sortable ? (
                          <button
                            type="button"
                            className={`asset-table-header-action asset-table-sort-toggle${isActive ? ' is-active-sort' : ''}`}
                            onClick={() => onToggleSort(column.id)}
                            disabled={disabled}
                            aria-label={`${isActive ? 'Change' : 'Sort'} ${column.title}`}
                            title={isActive ? `Sorted ${activeSort?.direction === 'asc' ? 'ascending' : 'descending'}` : `Sort ${column.title}`}
                          >
                            {isActive ? (
                              activeSort?.direction === 'asc'
                                ? <ChevronUp width={16} height={16} />
                                : <ChevronDown width={16} height={16} />
                            ) : (
                              <ChevronsUpDown width={16} height={16} />
                            )}
                          </button>
                        ) : null}
                        {filterColumn.filterKinds.length ? (
                          <button
                            type="button"
                            className={`asset-table-header-action asset-table-filter-toggle${activeFilter ? ' has-active-filter' : ''}`}
                            onClick={() => setOpenFilterColumnId((current) => current === column.id ? null : column.id)}
                            disabled={disabled}
                            aria-label={`${activeFilter ? 'Edit' : 'Add'} filter for ${column.title}`}
                            title={activeFilter ? formatFilterSummary(activeFilter, columns) : `Filter ${column.title}`}
                          >
                            <Funnel width={16} height={16} />
                          </button>
                        ) : null}
                      </div>
                    </div>
                    {isFilterOpen && filterColumn.filterKinds.length ? (
                      <DataFrameHeaderFilterMenu
                        column={filterColumn}
                        activeFilter={activeFilter}
                        disabled={disabled}
                        onApplyFilter={onApplyFilter}
                        onRemoveFilter={onRemoveFilter}
                        onClose={() => setOpenFilterColumnId(null)}
                      />
                    ) : null}
                  </div>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {table.rows.length ? table.rows.map((row, index) => (
            <tr key={index}>
              {table.columns.map((column) => (
                <td key={column.id}>{formatCellValue(row[column.id])}</td>
              ))}
            </tr>
          )) : (
            <tr>
              <td colSpan={Math.max(table.columns.length, 1)} className="asset-table-empty-cell">No rows on this page.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return JSON.stringify(value)
}

function formatCount(value: number): string {
  return new Intl.NumberFormat('en-US').format(value).replace(/,/g, ' ')
}
