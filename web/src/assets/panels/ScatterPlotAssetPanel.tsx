import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import embed, { type Result as VegaEmbedResult, type VisualizationSpec } from 'vega-embed'

import { prepareAsset } from '../../lib/api'
import type { AssetFilter, AssetSort, PreparedScatterPlotPayload } from '../../lib/types'
import {
  buildAxisSpec,
  buildChartPadding,
  buildChartTitle,
  buildScaleType,
  combineScatterPlotSelection,
  formatHistogramBound,
  parseSelectionRangeSignal,
  scatterPlotSelectionsEqual,
  useAssetChartTheme,
} from '../shared/chart'
import {
  AssetPanelFrame,
  AxisOverridesSection,
  DeferredModifierInput,
  ErrorPlaceholder,
  LoadingPlaceholder,
  OverrideIncompatibleNotice,
  PanelSettingsSection,
  PrepareErrorsNotice,
  PreparedAssetTableSection,
  ResizableDatavizContent,
  TitleOverridesSection,
} from '../shared/layout'
import {
  buildModifierOverridesRecord,
  defaultScatterPlotChartOverrides,
  filterKindsForDataType,
  initialTableStateFromModifiers,
  modifierColumnsFromSchema,
  modifierFieldLabelClassName,
  modifierTitle,
  nextSortForColumn,
  normalizePanelHeight,
  optionalPositiveNumberFromInput,
  optionalNumberFromInput,
  removeFilter,
  scatterPlotChartOverridesFromModifiers,
  serializeScatterPlotChartModifierValues,
  stableValueKey,
  tableStateKey,
  upsertFilter,
  valuesEqual,
} from '../shared/modifiers'
import type {
  DatavizAssetPanelProps,
  ScatterPlotChartOverrides,
  ScatterPlotLegendSelection,
  ScatterPlotSelectionBounds,
  ScatterPlotShapeStyle,
} from '../shared/types'
import { DEFAULT_DATAVIZ_TABLE_PAGE_SIZE, DEFAULT_SCATTER_PLOT_CHART_HEIGHT } from '../shared/types'

export function ScatterPlotAssetPanel({
  nodeId,
  asset,
  panelInfo,
  persistedState,
  onPersistedStateChange,
  panelHeight,
  onPanelHeightChange,
  sectionId,
}: DatavizAssetPanelProps) {
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
    if (overrideIncompatible || isApplyingPersistedStateRef.current) {
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
  }, [asset.default_modifiers, asset.override_schema_hash, chartOverrides, filters, onPersistedStateChange, overrideIncompatible, pageIndex, pageSize, persistedState, sort])

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
            onValidChange={(nextValue) => setChartOverrides((current) => ({ ...current, minPointSize: nextValue }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({ ...current, minPointSize: nextValue }))}
          />
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.maxPointSize, chartOverrideDefaults.maxPointSize))}>{modifierTitle(asset.modifier_schema, 'max_point_size', 'Max point size')}</span>
          <DeferredModifierInput
            displayValue={chartOverrides.maxPointSize}
            inputMode="decimal"
            isValid={(value) => optionalNumberFromInput(value) !== undefined}
            fallbackValue={chartOverrideDefaults.maxPointSize}
            onValidChange={(nextValue) => setChartOverrides((current) => ({ ...current, maxPointSize: nextValue }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({ ...current, maxPointSize: nextValue }))}
          />
        </label>

        <label className="asset-dataviz-checkbox-field">
          <input
            type="checkbox"
            checked={chartOverrides.showLegend}
            onChange={(event) => setChartOverrides((current) => ({ ...current, showLegend: event.target.checked }))}
          />
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.showLegend, chartOverrideDefaults.showLegend))}>{modifierTitle(asset.modifier_schema, 'show_legend', 'Show legend')}</span>
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.shapeStyle, chartOverrideDefaults.shapeStyle))}>{modifierTitle(asset.modifier_schema, 'shape_style', 'Shape style')}</span>
          <select
            value={chartOverrides.shapeStyle}
            onChange={(event) => setChartOverrides((current) => ({ ...current, shapeStyle: event.target.value as ScatterPlotShapeStyle }))}
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
        {overrideIncompatible ? <OverrideIncompatibleNotice onReset={onPersistedStateChange ? handleResetOverrides : undefined} /> : null}
        <PrepareErrorsNotice errors={response?.errors ?? []} />
        <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange}>
          {(chartHeight) => (
            <>
              {selectedPointLabel ? (
                <div className="asset-histogram-selection-pill">
                  <strong>Selected point</strong>
                  <span>{selectedPointLabel}</span>
                </div>
              ) : null}
              {prepareQuery.isLoading && !scatterPlot ? <LoadingPlaceholder message="Preparing scatter plot view..." /> : null}
              {prepareQuery.isError ? (
                <ErrorPlaceholder message={prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the scatter plot view.'} />
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
          <PreparedAssetTableSection
            table={table}
            columns={availableColumns}
            activeSort={resolvedSort}
            activeFilters={resolvedFilters}
            disabled={overrideIncompatible || prepareQuery.isFetching}
            rowsLabel={linkedRows}
            columnCount={columnCount}
            pageInput={pageInput}
            pageCount={pageCount}
            isRefreshing={prepareQuery.isFetching}
            canGoPrevious={canGoPrevious}
            canGoNext={canGoNext}
            onPageInputChange={setPageInput}
            onCommitPageInput={commitPageInput}
            onResetPageInput={() => setPageInput(String(resolvedPage.index + 1))}
            onPageSizeChange={(size) => {
              setPageSize(size)
              setPageIndex(0)
            }}
            onFirstPage={() => setPageIndex(0)}
            onPreviousPage={() => setPageIndex((current) => Math.max(0, current - 1))}
            onNextPage={() => setPageIndex((current) => Math.min(pageCount - 1, current + 1))}
            onLastPage={() => setPageIndex(pageCount - 1)}
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
        ) : null}
      </div>
    </AssetPanelFrame>
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
  const pendingXRangeRef = useRef(selectedBounds?.x ?? null)
  const pendingYRangeRef = useRef(selectedBounds?.y ?? null)
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
    return <LoadingPlaceholder message="No numeric rows match the current scatter plot filters." />
  }

  if (chartError) {
    return <ErrorPlaceholder message={chartError} />
  }

  return (
    <div
      ref={containerRef}
      className="asset-histogram-chart-shell"
      style={{ '--asset-dataviz-height': `${chartHeight}px` } as CSSProperties}
    />
  )
}

function buildScatterPlotVegaLiteSpec(
  scatterPlot: PreparedScatterPlotPayload,
  selectedBounds: ScatterPlotSelectionBounds | null,
  selectedPointRowIndex: number | null,
  highlightedLegend: ScatterPlotLegendSelection | null,
  theme: ReturnType<typeof useAssetChartTheme>,
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
