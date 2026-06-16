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
  buildVegaLiteChartConfig,
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
  clampNumberToRange,
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
  prepareTarget,
  viewerMode = 'notebook',
  panelInfo,
  persistedState,
  onPersistedStateChange,
  onReadyStateChange,
  panelHeight,
  onPanelHeightChange,
  sectionId,
  frameVariant,
}: DatavizAssetPanelProps) {
  const prepareNodeId = prepareTarget?.nodeId ?? nodeId
  const prepareAssetName = prepareTarget?.assetName ?? asset.asset_name
  const preparePanelContext = prepareTarget?.panelContext ?? null
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
  const [selectedLegend, setSelectedLegend] = useState<ScatterPlotLegendSelection | null>(null)
  const [pageInput, setPageInput] = useState(String(initialTableState.page.index + 1))
  const requiresOverrideValidation = Boolean(
    persistedState
    && persistedState.override_schema_hash !== null
    && asset.override_schema_hash !== null
    && persistedState.override_schema_hash !== asset.override_schema_hash,
  )
  const isApplyingPersistedStateRef = useRef(false)
  const filtersKey = JSON.stringify(filters)
  const selectionKey = stableValueKey({ selectedBounds, selectedPointRowIndex, selectedLegend })
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
  const modifierOverrides = useMemo(
    () => buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
      ...serializeScatterPlotChartModifierValues(chartOverrides),
    }, asset.default_modifiers),
    [asset.default_modifiers, chartOverrides, filters, pageIndex, pageSize, sort],
  )
  const overrideValidationKey = requiresOverrideValidation ? stableValueKey(modifierOverrides) : null

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
    setSelectedLegend(null)
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
    setSelectedBounds(null)
    setSelectedPointRowIndex(null)
    setSelectedLegend(null)
  }, [asset.current_asset_version_id, filtersKey])

  const prepareQuery = useQuery({
    queryKey: [
      'asset-prepare',
      prepareNodeId,
      prepareAssetName,
      asset.current_asset_version_id,
      pageIndex,
      pageSize,
      sort?.column ?? null,
      sort?.direction ?? null,
      filtersKey,
      selectionKey,
      persistedState?.override_schema_hash ?? null,
      overrideValidationKey,
      stableValueKey(preparePanelContext),
    ],
    queryFn: () => prepareAsset(prepareNodeId, prepareAssetName, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: modifierOverrides,
      transient_modifiers: {
        ...(selectedBounds ? { selection_bounds: selectedBounds } : {}),
        ...(selectedPointRowIndex !== null ? { selected_row_index: selectedPointRowIndex } : {}),
        ...(selectedLegend ? { selected_legend: selectedLegend } : {}),
      },
      panel_context: preparePanelContext,
      persisted_override_schema_hash: persistedState?.override_schema_hash ?? null,
    }),
    enabled: asset.current_asset_version_id !== null,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const mainPayload = response?.payloads.main ?? null
  const scatterPlot = mainPayload?.kind === 'scatter_plot' ? mainPayload : null
  const overrideIncompatible = Boolean(response?.errors.some((error) => error.code === 'override_incompatible'))
  const overrideValidationBlocked = requiresOverrideValidation && (prepareQuery.isFetching || !prepareQuery.isSuccess)
  const prepareErrors = response?.errors.filter((error) => error.code !== 'override_incompatible') ?? []
  const isPanelReady = overrideIncompatible || prepareQuery.isError || prepareQuery.isSuccess
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
  const displayedRows = table?.rows_total ?? totalRows
  const baseRows = typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : totalRows
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const pageCount = Math.max(1, Math.ceil(displayedRows / Math.max(resolvedPage.size, 1)))
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
    onReadyStateChange?.(isPanelReady)
  }, [isPanelReady, onReadyStateChange])

  useEffect(() => {
    if (overrideIncompatible || overrideValidationBlocked || isApplyingPersistedStateRef.current) {
      return
    }
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
  }, [asset.override_schema_hash, modifierOverrides, onPersistedStateChange, overrideIncompatible, overrideValidationBlocked, persistedState])

  useEffect(() => {
    if (selectedPointRowIndex === null || scatterPlot === null) {
      return
    }
    if (!scatterPlot.points.some((point) => point.row_index === selectedPointRowIndex)) {
      setSelectedPointRowIndex(null)
    }
  }, [scatterPlot, selectedPointRowIndex])

  useEffect(() => {
    if (selectedLegend === null || scatterPlot === null) {
      return
    }
    if (!scatterPlot.points.some((point) => point[selectedLegend.field] === selectedLegend.value)) {
      setSelectedLegend(null)
    }
  }, [scatterPlot, selectedLegend])

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
    setSelectedLegend(null)
    setPageInput(String(resetState.page.index + 1))
    onPersistedStateChange?.({
      modifier_overrides: {},
      override_schema_hash: asset.override_schema_hash,
    })
  }

  function handleResetSettingsOverrides() {
    setChartOverrides(chartOverrideDefaults)
  }

  function handleClearTableFilters() {
    setPageIndex(0)
    setSort(null)
    setFilters([])
    setSelectedBounds(null)
    setSelectedPointRowIndex(null)
    setSelectedLegend(null)
    setPageInput('1')
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

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.sizeScaling, chartOverrideDefaults.sizeScaling))}>{modifierTitle(asset.modifier_schema, 'size_scaling', 'Size scaling')}</span>
          <div className="asset-dataviz-slider-field">
            <input
              type="range"
              min={0.1}
              max={3}
              step={0.1}
              value={chartOverrides.sizeScaling}
              onChange={(event) => setChartOverrides((current) => ({
                ...current,
                sizeScaling: clampNumberToRange(Number(event.target.value), current.sizeScaling, 0.1, 3),
              }))}
            />
            <strong>{formatScatterPlotSizeScaling(chartOverrides.sizeScaling)}</strong>
          </div>
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
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} settingsTitle="Modifier overrides" settingsBody={settingsBody} settingsActive={hasSettingsOverrides} sectionId={sectionId} frameVariant={frameVariant}>
      <div className="asset-dataframe-panel asset-scatter-plot-panel">
        {overrideIncompatible ? <OverrideIncompatibleNotice onReset={onPersistedStateChange ? handleResetOverrides : undefined} /> : null}
        <PrepareErrorsNotice errors={prepareErrors} />
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
                    setSelectedLegend(null)
                  }}
                  onPointSelectionChange={(rowIndex) => {
                    setPageIndex(0)
                    setSelectedBounds(null)
                    setSelectedPointRowIndex(rowIndex)
                    setSelectedLegend(null)
                  }}
                  selectedLegend={selectedLegend}
                  onLegendSelectionChange={(selection) => {
                    setPageIndex(0)
                    setSelectedBounds(null)
                    setSelectedPointRowIndex(null)
                    setSelectedLegend(selection)
                  }}
                />
              ) : null}
            </>
          )}
        </ResizableDatavizContent>
        {table ? (
          <PreparedAssetTableSection
            title="DataFrame"
            collapsible
            defaultExpanded={false}
            table={table}
            columns={availableColumns}
            activeSort={resolvedSort}
            activeFilters={resolvedFilters}
            viewerMode={viewerMode}
            disabled={overrideIncompatible || overrideValidationBlocked || prepareQuery.isFetching}
            totalRows={baseRows}
            displayedRows={displayedRows}
            columnCount={columnCount}
            pageInput={pageInput}
            pageCount={pageCount}
            isRefreshing={prepareQuery.isFetching}
            canGoPrevious={canGoPrevious}
            canGoNext={canGoNext}
            hasTemporarySelection={selectedBounds !== null || selectedPointRowIndex !== null || selectedLegend !== null}
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
            onClearFilters={handleClearTableFilters}
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
  selectedLegend,
  onSelectionChange,
  onPointSelectionChange,
  onLegendSelectionChange,
}: {
  scatterPlot: PreparedScatterPlotPayload
  chartHeight: number
  overrides: ScatterPlotChartOverrides
  defaultOverrides: ScatterPlotChartOverrides
  selectedBounds: ScatterPlotSelectionBounds | null
  selectedPointRowIndex: number | null
  selectedLegend: ScatterPlotLegendSelection | null
  onSelectionChange: (bounds: ScatterPlotSelectionBounds | null) => void
  onPointSelectionChange: (rowIndex: number | null) => void
  onLegendSelectionChange: (selection: ScatterPlotLegendSelection | null) => void
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)
  const viewRef = useRef<VegaEmbedResult | null>(null)
  const selectedBoundsRef = useRef<ScatterPlotSelectionBounds | null>(selectedBounds)
  const pendingXRangeRef = useRef(selectedBounds?.x ?? null)
  const pendingYRangeRef = useRef(selectedBounds?.y ?? null)
  const onSelectionChangeRef = useRef(onSelectionChange)
  const selectedPointRowIndexRef = useRef<number | null>(selectedPointRowIndex)
  const onPointSelectionChangeRef = useRef(onPointSelectionChange)
  const selectedLegendRef = useRef<ScatterPlotLegendSelection | null>(selectedLegend)
  const onLegendSelectionChangeRef = useRef(onLegendSelectionChange)
  const chartTheme = useAssetChartTheme()

  selectedBoundsRef.current = selectedBounds
  pendingXRangeRef.current = selectedBounds?.x ?? null
  pendingYRangeRef.current = selectedBounds?.y ?? null
  onSelectionChangeRef.current = onSelectionChange
  selectedPointRowIndexRef.current = selectedPointRowIndex
  onPointSelectionChangeRef.current = onPointSelectionChange
  selectedLegendRef.current = selectedLegend
  onLegendSelectionChangeRef.current = onLegendSelectionChange

  const spec = useMemo(
    () => buildScatterPlotVegaLiteSpec(
      scatterPlot,
      selectedBounds,
      selectedPointRowIndex,
      selectedLegend,
      chartTheme,
      chartHeight,
      overrides,
      defaultOverrides,
    ),
    [
      chartTheme,
      chartHeight,
      defaultOverrides,
      overrides,
      scatterPlot,
      selectedBounds?.x.lower,
      selectedBounds?.x.upper,
      selectedBounds?.y.lower,
      selectedBounds?.y.upper,
      selectedLegend?.field,
      selectedLegend?.value,
      selectedPointRowIndex,
    ],
  )

  useEffect(() => {
    if (!containerRef.current) {
      return
    }
    let viewResult: VegaEmbedResult | null = null
    let disposed = false
    let legendSignalListeners: Array<{ signalName: string; handleLegendSignal: (name: string, value: unknown) => void }> = []
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
          if (selectedLegendRef.current !== null) {
            onLegendSelectionChangeRef.current(null)
          }
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
          if (isScatterPlotLegendItem(item)) {
            return
          }
          if (selectedLegendRef.current !== null) {
            onLegendSelectionChangeRef.current(null)
          }
          if (selectedPointRowIndexRef.current !== null) {
            onPointSelectionChangeRef.current(null)
          }
        }
        legendSignalListeners = scatterPlotLegendBindings(scatterPlot).map(({ field, signalName }) => {
          const handleLegendSignal = (_name: string, value: unknown) => {
            const legendValue = parseScatterPlotLegendSignalValue(value)
            if (legendValue === null) {
              return
            }
            const currentSelection = selectedLegendRef.current
            onLegendSelectionChangeRef.current(
              currentSelection !== null
              && currentSelection.field === field
              && currentSelection.value === legendValue
                ? null
                : { field, value: legendValue },
            )
          }
          result.view.addSignalListener(signalName, handleLegendSignal)
          return { signalName, handleLegendSignal }
        })
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
      if (viewResult !== null) {
        for (const { signalName, handleLegendSignal } of legendSignalListeners) {
          viewResult.view.removeSignalListener(signalName, handleLegendSignal)
        }
      }
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
  const sizeScaling = clampNumberToRange(overrides.sizeScaling, defaultOverrides.sizeScaling, 0.1, 3)
  const sizeEncoding = buildScatterPlotSizeEncoding(scatterPlot, sizeType, showLegend, minPointSize, maxPointSize, sizeScaling)
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
    config: buildVegaLiteChartConfig(theme),
    data: {
      values: scatterPlot.points.map((point) => ({
        ...point,
        tooltip: buildScatterPlotTooltip(point, scatterPlot),
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
      ...scatterPlotLegendBindings(scatterPlot).map(({ field }) => ({
        name: scatterPlotLegendParamName(field),
        select: {
          type: 'point' as const,
          fields: [field],
          toggle: 'true',
          clear: false,
        },
        bind: 'legend' as const,
      })),
    ],
    mark: {
      type: 'point',
      filled: overrides.shapeStyle === 'filled',
      size: scatterPlot.size_column ? undefined : 100,
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
      tooltip: { field: 'tooltip' },
    },
  }
}

function buildScatterPlotTooltip(
  point: PreparedScatterPlotPayload['points'][number],
  scatterPlot: PreparedScatterPlotPayload,
): Record<string, string | number | boolean | null> {
  return {
    ...(point.label !== null && point.label !== undefined ? { title: point.label } : {}),
    [scatterPlot.x_column]: point.x,
    [scatterPlot.y_column]: point.y,
    ...(scatterPlot.shape_column ? { [scatterPlot.shape_column]: point.shape ?? null } : {}),
    ...(scatterPlot.size_column ? { [scatterPlot.size_column]: point.size ?? null } : {}),
    ...(scatterPlot.color_column ? { [scatterPlot.color_column]: point.color ?? null } : {}),
  }
}

function buildScatterPlotSizeEncoding(
  scatterPlot: PreparedScatterPlotPayload,
  sizeType: 'quantitative' | 'nominal',
  showLegend: boolean,
  minPointSize: number | undefined,
  maxPointSize: number | undefined,
  sizeScaling: number,
): Record<string, unknown> | undefined {
  if (!scatterPlot.size_column) {
    return undefined
  }
  if (sizeType !== 'quantitative') {
    return {
      field: 'size',
      type: sizeType,
      title: scatterPlot.size_column,
      legend: showLegend ? undefined : null,
      scale: {
        ...(minPointSize !== undefined ? { rangeMin: minPointSize } : {}),
        ...(maxPointSize !== undefined ? { rangeMax: maxPointSize } : {}),
      },
    }
  }
  if (scatterPlot.size_domain && scatterPlot.size_domain.min === scatterPlot.size_domain.max) {
    return {
      field: 'size',
      type: sizeType,
      title: scatterPlot.size_column,
      legend: showLegend ? undefined : null,
      scale: {
        type: 'pow',
        exponent: sizeScaling,
        domain: [scatterPlot.size_domain.min, scatterPlot.size_domain.max],
        range: [100, 100],
      },
    }
  }
  return {
    field: 'size',
    type: sizeType,
    title: scatterPlot.size_column,
    legend: showLegend ? undefined : null,
    scale: {
      type: 'pow',
      exponent: sizeScaling,
      ...(minPointSize !== undefined ? { rangeMin: minPointSize } : {}),
      ...(maxPointSize !== undefined ? { rangeMax: maxPointSize } : {}),
    },
  }
}

function formatScatterPlotSizeScaling(value: number): string {
  return value.toFixed(1).replace(/\.0$/, '')
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

function isScatterPlotLegendItem(item: unknown): boolean {
  if (!item || typeof item !== 'object') {
    return false
  }
  const role = typeof (item as { mark?: { role?: unknown } }).mark?.role === 'string'
    ? (item as { mark?: { role?: string } }).mark?.role ?? ''
    : ''
  return role.includes('legend')
}

function scatterPlotLegendBindings(
  scatterPlot: PreparedScatterPlotPayload,
): Array<{ field: ScatterPlotLegendSelection['field']; signalName: string }> {
  const bindings: Array<{ field: ScatterPlotLegendSelection['field']; signalName: string }> = []
  if (scatterPlot.color_column !== null && scatterPlot.color_kind === 'nominal') {
    bindings.push({ field: 'color', signalName: scatterPlotLegendSignalName('color') })
  }
  if (scatterPlot.shape_column !== null) {
    bindings.push({ field: 'shape', signalName: scatterPlotLegendSignalName('shape') })
  }
  if (scatterPlot.size_column !== null && scatterPlot.size_kind === 'nominal') {
    bindings.push({ field: 'size', signalName: scatterPlotLegendSignalName('size') })
  }
  return bindings
}

function scatterPlotLegendParamName(field: ScatterPlotLegendSelection['field']): string {
  return `legend_${field}`
}

function scatterPlotLegendSignalName(field: ScatterPlotLegendSelection['field']): string {
  return `${scatterPlotLegendParamName(field)}_${field}_legend`
}

function parseScatterPlotLegendSignalValue(value: unknown): string | number | boolean | null {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? value
    : null
}
