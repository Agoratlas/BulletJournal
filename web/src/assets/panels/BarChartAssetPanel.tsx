import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import embed, { type Result as VegaEmbedResult, type VisualizationSpec } from 'vega-embed'

import { prepareAsset } from '../../lib/api'
import type { AssetFilter, AssetHighlight, AssetSort, PreparedBarChartPayload } from '../../lib/types'
import {
  buildAxisSpec,
  buildChartPadding,
  buildChartTitle,
  buildScaleType,
  buildVegaLiteChartConfig,
  eventHasShiftKey,
  opaqueColor,
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
  clampPercentage,
  defaultBarChartChartOverrides,
  barChartChartOverridesFromModifiers,
  filterKindsForDataType,
  initialTableStateFromModifiers,
  modifierFieldLabelClassName,
  modifierColumnsFromSchema,
  modifierTitle,
  nextSortForColumn,
  replaceHighlightsForColumn,
  normalizePanelHeight,
  optionalNonNegativeNumberFromInput,
  optionalNumberFromInput,
  removeFilter,
  serializeBarChartModifierValues,
  stableValueKey,
  tableStateKey,
  upsertFilter,
  valuesEqual,
} from '../shared/modifiers'
import type { BarChartChartOverrides, DatavizAssetPanelProps } from '../shared/types'
import { DEFAULT_DATAVIZ_TABLE_PAGE_SIZE, DEFAULT_HISTOGRAM_CHART_HEIGHT } from '../shared/types'

type BarChartSelectionValue = string | number | boolean

export function BarChartAssetPanel({
  nodeId,
  asset,
  prepareTarget,
  panelInfo,
  viewerMode = 'notebook',
  persistedState,
  onPersistedStateChange,
  onReadyStateChange,
  panelHeight,
  onPanelHeightChange,
  isPanelResized,
  chartScale = 1,
  minPanelHeight,
  sectionId,
  frameVariant,
}: DatavizAssetPanelProps) {
  const prepareNodeId = prepareTarget?.nodeId ?? nodeId
  const prepareAssetName = prepareTarget?.assetName ?? asset.asset_name
  const preparePanelContext = prepareTarget?.panelContext ?? null
  const modifierColumns = useMemo(() => modifierColumnsFromSchema(asset.modifier_schema), [asset.modifier_schema])
  const chartOverrideDefaults = useMemo(
    () => defaultBarChartChartOverrides(asset.default_modifiers, asset.modifier_schema),
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
    () => barChartChartOverridesFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}, asset.modifier_schema),
    [asset.default_modifiers, asset.modifier_schema, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const [pageIndex, setPageIndex] = useState(initialTableState.page.index)
  const [pageSize, setPageSize] = useState(initialTableState.page.size)
  const [sort, setSort] = useState<AssetSort | null>(initialTableState.sort)
  const [filters, setFilters] = useState<AssetFilter[]>(initialTableState.filters)
  const [highlights, setHighlights] = useState<AssetHighlight[]>(initialTableState.highlights ?? [])
  const [chartOverrides, setChartOverrides] = useState<BarChartChartOverrides>(initialChartOverrides)
  const [selectedCategories, setSelectedCategories] = useState<BarChartSelectionValue[]>([])
  const [pageInput, setPageInput] = useState(String(initialTableState.page.index + 1))
  const requiresOverrideValidation = Boolean(
    persistedState
    && persistedState.override_schema_hash !== null
    && asset.override_schema_hash !== null
    && persistedState.override_schema_hash !== asset.override_schema_hash,
  )
  const isApplyingPersistedStateRef = useRef(false)
  const filtersKey = JSON.stringify(filters)
  const highlightsKey = JSON.stringify(highlights)
  const selectionKey = stableValueKey(selectedCategories)
  const externalStateKey = useMemo(
    () => tableStateKey(initialTableState),
    [initialTableState.filters, initialTableState.highlights, initialTableState.page.index, initialTableState.page.size, initialTableState.sort?.column, initialTableState.sort?.direction],
  )
  const externalChartOverridesKey = useMemo(() => stableValueKey(initialChartOverrides), [initialChartOverrides])
  const localStateKey = tableStateKey({
    page: { index: pageIndex, size: pageSize },
    sort,
    filters,
    highlights,
  })
  const localChartOverridesKey = stableValueKey(chartOverrides)
  const modifierOverrides = useMemo(
    () => buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
      highlights,
      ...serializeBarChartModifierValues(chartOverrides),
    }, asset.default_modifiers),
    [asset.default_modifiers, chartOverrides, filters, highlights, pageIndex, pageSize, sort],
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
    setHighlights(initialTableState.highlights ?? [])
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
    setSelectedCategories([])
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
      highlightsKey,
      selectionKey,
      persistedState?.override_schema_hash ?? null,
      overrideValidationKey,
      stableValueKey(preparePanelContext),
    ],
    queryFn: () => prepareAsset(prepareNodeId, prepareAssetName, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: modifierOverrides,
      transient_modifiers: selectedCategories.length ? (
        asset.definition?.bar_group_column
          ? { selected_groups: selectedCategories }
          : { selected_categories: selectedCategories }
      ) : {},
      panel_context: preparePanelContext,
      persisted_override_schema_hash: persistedState?.override_schema_hash ?? null,
    }),
    enabled: asset.current_asset_version_id !== null,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const mainPayload = response?.payloads.main ?? null
  const barChart = mainPayload?.kind === 'bar_chart' ? mainPayload : null
  const overrideIncompatible = Boolean(response?.errors.some((error) => error.code === 'override_incompatible'))
  const overrideValidationBlocked = requiresOverrideValidation && (prepareQuery.isFetching || !prepareQuery.isSuccess)
  const prepareErrors = response?.errors.filter((error) => error.code !== 'override_incompatible') ?? []
  const isPanelReady = overrideIncompatible || prepareQuery.isError || prepareQuery.isSuccess
  const table = response?.payloads.table ?? null
  const resolvedPage = table?.page ?? { index: pageIndex, size: pageSize }
  const resolvedSort = table?.sort?.[0] ?? null
  const resolvedFilters = Array.isArray(response?.resolved_modifiers.filters) ? response.resolved_modifiers.filters : filters
  const resolvedHighlights = Array.isArray(response?.resolved_modifiers.highlights)
    ? response.resolved_modifiers.highlights as AssetHighlight[]
    : highlights
  const availableColumns = modifierColumns.length
    ? modifierColumns
    : (table?.columns ?? []).map((column) => ({
      id: column.id,
      title: column.title,
      dataType: column.data_type,
      filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
    }))
  const totalRows = barChart?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const displayedRows = table?.rows_total ?? totalRows
  const baseRows = typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : totalRows
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const pageCount = Math.max(1, Math.ceil(displayedRows / Math.max(resolvedPage.size, 1)))
  const canGoPrevious = resolvedPage.index > 0
  const canGoNext = resolvedPage.index + 1 < pageCount
  const resolvedPanelHeight = normalizePanelHeight(panelHeight) ?? DEFAULT_HISTOGRAM_CHART_HEIGHT
  const hasSettingsOverrides = Object.keys(buildModifierOverridesRecord(
    serializeBarChartModifierValues(chartOverrides),
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
    if (barChart === null) {
      return
    }
    const nextSelection = barChartVisibleSelection(selectedCategories, barChart)
    if (!barChartSelectionsEqual(nextSelection, selectedCategories)) {
      setSelectedCategories(nextSelection)
    }
  }, [barChart, selectedCategories])

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
    setHighlights(resetState.highlights ?? [])
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

  function handleClearTableFilters() {
    setPageIndex(0)
    setSort(null)
    setFilters([])
    setSelectedCategories([])
    setPageInput('1')
  }

  const settingsBody = (
    <>
      <div className="asset-dataviz-settings-actions">
        <button type="button" className="secondary asset-dataviz-settings-reset" onClick={handleResetSettingsOverrides} disabled={!hasSettingsOverrides}>
          Reset to default
        </button>
      </div>

      <PanelSettingsSection title="Bar chart">
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
            onValidChange={(nextValue) => setChartOverrides((current) => ({ ...current, borderThickness: nextValue }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({ ...current, borderThickness: nextValue }))}
          />
        </label>

        {barChart?.group_column ? (
          <PanelSettingsSection title="Grouping">
            <label className="asset-dataviz-field">
              <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.groupMode, chartOverrideDefaults.groupMode))}>{modifierTitle(asset.modifier_schema, 'group_mode', 'Group mode')}</span>
              <select
                className="asset-dataviz-select-field"
                value={chartOverrides.groupMode}
                onChange={(event) => setChartOverrides((current) => ({
                  ...current,
                  groupMode: event.target.value as 'grouped' | 'stacked',
                }))}
              >
                <option value="grouped">Grouped</option>
                <option value="stacked">Stacked</option>
              </select>
            </label>

            <label className="asset-dataviz-field">
              <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.groupNormalize, chartOverrideDefaults.groupNormalize))}>{modifierTitle(asset.modifier_schema, 'group_normalize', 'Normalize groups')}</span>
              <input
                type="checkbox"
                className="asset-dataviz-checkbox-field"
                checked={chartOverrides.groupNormalize}
                onChange={(event) => setChartOverrides((current) => ({
                  ...current,
                  groupNormalize: event.target.checked,
                }))}
              />
            </label>

            <label className="asset-dataviz-field">
              <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.groupSpacing, chartOverrideDefaults.groupSpacing))}>{modifierTitle(asset.modifier_schema, 'group_spacing', 'Group spacing')}</span>
              <div className="asset-dataviz-slider-field">
                <input
                  type="range"
                  min={0}
                  max={50}
                  value={chartOverrides.groupSpacing}
                  onChange={(event) => setChartOverrides((current) => ({
                    ...current,
                    groupSpacing: clampPercentage(Number(event.target.value), current.groupSpacing),
                  }))}
                />
                <strong>{chartOverrides.groupSpacing}%</strong>
              </div>
            </label>
          </PanelSettingsSection>
        ) : null}
      </PanelSettingsSection>

      <AxisOverridesSection
        title={modifierTitle(asset.modifier_schema, 'x_axis', 'X axis')}
        overrides={chartOverrides.xAxis}
        defaultOverrides={chartOverrideDefaults.xAxis}
        defaultLabel={chartOverrideDefaults.xAxis.label}
        onChange={(next) => setChartOverrides((current) => ({ ...current, xAxis: next }))}
        allowLogScale={false}
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
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} settingsTitle="Modifier overrides" settingsBody={settingsBody} settingsActive={hasSettingsOverrides} sectionId={sectionId} frameVariant={frameVariant} showExportActions={viewerMode === 'dashboard'} isPanelResized={isPanelResized}>
      <div className="asset-dataframe-panel asset-histogram-panel">
        {overrideIncompatible ? <OverrideIncompatibleNotice onReset={onPersistedStateChange ? handleResetOverrides : undefined} /> : null}
        <PrepareErrorsNotice errors={prepareErrors} />
        <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange} isResized={isPanelResized} minHeight={minPanelHeight}>
          {(chartHeight) => (
            <>
              {prepareQuery.isLoading && !barChart ? <LoadingPlaceholder message="Preparing bar chart view..." /> : null}
              {prepareQuery.isError ? (
                <ErrorPlaceholder message={prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the bar chart view.'} />
              ) : null}
              {barChart ? (
                <BarChartChart
                  barChart={barChart}
                  chartHeight={chartHeight}
                  overrides={chartOverrides}
                  defaultOverrides={chartOverrideDefaults}
                  chartScale={chartScale}
                  selectedGroups={selectedCategories}
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
          <PreparedAssetTableSection
            title="DataFrame"
            collapsible
            defaultExpanded={false}
            table={table}
            columns={availableColumns}
            activeSort={resolvedSort}
            activeFilters={resolvedFilters}
            activeHighlights={resolvedHighlights}
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
            hasTemporarySelection={selectedCategories.length > 0}
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
            onApplyHighlights={(columnId, nextHighlights) => {
              setPageIndex(0)
              setHighlights((current) => replaceHighlightsForColumn(current, columnId, nextHighlights))
            }}
            onClearFilters={handleClearTableFilters}
          />
        ) : null}
      </div>
    </AssetPanelFrame>
  )
}

function BarChartChart({
  barChart,
  chartHeight,
  overrides,
  defaultOverrides,
  chartScale,
  selectedGroups,
  onSelectionChange,
}: {
  barChart: PreparedBarChartPayload
  chartHeight: number
  overrides: BarChartChartOverrides
  defaultOverrides: BarChartChartOverrides
  chartScale: number
  selectedGroups: BarChartSelectionValue[]
  onSelectionChange: (groups: BarChartSelectionValue[]) => void
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)
  const viewRef = useRef<VegaEmbedResult | null>(null)
  const initialChartHeightRef = useRef(chartHeight)
  const onSelectionChangeRef = useRef(onSelectionChange)
  const selectedGroupsRef = useRef(selectedGroups)
  const shiftHeldRef = useRef(false)
  const chartTheme = useAssetChartTheme()
  const hasGroup = Boolean(barChart.group_column)

  onSelectionChangeRef.current = onSelectionChange
  selectedGroupsRef.current = selectedGroups

  const spec = useMemo(
    () => buildBarChartVegaLiteSpec(barChart, selectedGroups, chartTheme, initialChartHeightRef.current, overrides, defaultOverrides, chartScale),
    [barChart, chartScale, chartTheme, defaultOverrides, overrides, selectedGroups],
  )

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      shiftHeldRef.current = e.shiftKey
    }
    window.addEventListener('keydown', handleKey)
    window.addEventListener('keyup', handleKey)

    if (!containerRef.current) {
      return
    }
    let viewResult: VegaEmbedResult | null = null
    let disposed = false
    let legendSignalName: string | null = null

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

        if (hasGroup) {
          legendSignalName = barChartLegendSignalName()
          const handleLegendSignal = (_name: string, value: unknown) => {
            const legendValue = parseBarChartLegendSignalValue(value)
            if (legendValue !== null) {
              onSelectionChangeRef.current(
                toggleBarChartSelection(selectedGroupsRef.current, [legendValue], shiftHeldRef.current),
              )
            }
          }
          result.view.addSignalListener(legendSignalName, handleLegendSignal)
        }

        const handleClick = (_event: Event, item: unknown) => {
          if (hasGroup && isBarChartLegendItem(item)) {
            return
          }
          const clickedValues = parseBarChartClickedValues(item)
          if (clickedValues !== null) {
            onSelectionChangeRef.current(
              toggleBarChartSelection(selectedGroups, clickedValues, eventHasShiftKey(_event)),
            )
            return
          }
          if (selectedGroups.length) {
            onSelectionChangeRef.current([])
          }
        }
        const handleDoubleClick = (event: Event) => {
          event.preventDefault()
          event.stopPropagation()
          if (selectedGroups.length) {
            onSelectionChangeRef.current([])
          }
        }
        result.view.addEventListener('click', handleClick)
        result.view.addEventListener('dblclick', handleDoubleClick)
      } catch (error) {
        if (!disposed) {
          setChartError(error instanceof Error ? error.message : 'Could not render the bar chart view.')
        }
      }
    }

    void renderChart()

    return () => {
      disposed = true
      window.removeEventListener('keydown', handleKey)
      window.removeEventListener('keyup', handleKey)
      if (viewResult !== null && legendSignalName !== null) {
        viewResult.view.removeSignalListener(legendSignalName, () => {})
      }
      viewResult?.finalize()
      viewResult = null
      viewRef.current = null
    }
  }, [selectedGroups, spec, hasGroup])

  useLayoutEffect(() => {
    const result = viewRef.current
    if (result !== null) {
      result.view.height(chartHeight).run()
    }
  }, [chartHeight])

  useEffect(() => {
    const result = viewRef.current
    if (result === null) {
      return
    }
    result.view.height(chartHeight)
    void result.view.runAsync()
  }, [chartHeight])

  if (!barChart.bars.length) {
    return <LoadingPlaceholder message="No non-null rows match the current bar chart filters." />
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

function buildBarChartVegaLiteSpec(
  barChart: PreparedBarChartPayload,
  selectedGroups: BarChartSelectionValue[],
  theme: ReturnType<typeof useAssetChartTheme>,
  chartHeight: number,
  overrides: BarChartChartOverrides,
  defaultOverrides: BarChartChartOverrides,
  chartScale: number,
): VisualizationSpec {
  const borderThickness = optionalNonNegativeNumberFromInput(overrides.borderThickness) ?? 0
  const barWidth = clampPercentage(overrides.barWidth, 90)
  const yScaleType = buildScaleType(overrides.yAxis.scale)
  const hasGroup = Boolean(barChart.group_column)
  const isStacked = overrides.groupMode === 'stacked'
  const isNormalized = overrides.groupNormalize
  const groupSpacing = overrides.groupSpacing / 100

  const tooltipFields: Record<string, unknown>[] = [
    { field: 'category_label', type: 'nominal' as const, title: barChart.category_column },
  ]
  if (hasGroup) {
    tooltipFields.push({
      field: 'group_label', type: 'nominal' as const, title: barChart.group_column ?? 'Group',
    })
    tooltipFields.push({
      field: 'aggregate_label', type: 'nominal' as const, title: `${capitalizeAggregation(barChart.aggregation)} of ${barChart.value_column}`,
    })
    if (isNormalized) {
      tooltipFields.push({
        field: 'group_proportion_label', type: 'nominal' as const, title: 'Proportion',
      })
    }
  } else {
    tooltipFields.push({
      field: 'aggregate_label', type: 'nominal' as const, title: `${capitalizeAggregation(barChart.aggregation)} of ${barChart.value_column}`,
    })
  }

  const colorScale = hasGroup ? buildBarChartColorScale(barChart.bars) : null

  const xPaddingInner = Math.max(0.02, 1 - (barWidth / 100))

  const spec: VisualizationSpec = {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    autosize: { type: 'fit', contains: 'padding', resize: true },
    width: 'container',
    height: chartHeight,
    background: 'transparent',
    padding: buildChartPadding(overrides.title),
    title: buildChartTitle(overrides.title, defaultOverrides.title.text, chartScale),
    config: buildVegaLiteChartConfig(theme, chartScale),
    data: {
      values: barChart.bars.map((bar, index) => {
        const selectionValue = hasGroup ? (bar.group ?? bar.value) : bar.value
        return {
          category_label: bar.label,
          group_label: bar.group_label ?? '',
          group_index: bar.group_index ?? 0,
          category_index: bar.category_index ?? index,
          aggregate_value: bar.aggregate_value,
          aggregate_label: formatBarChartAggregateValue(bar.aggregate_value),
          group_proportion_label: hasGroup && bar.group_proportion !== undefined
            ? formatBarChartProportion(bar.group_proportion)
            : '',
          color: bar.color,
          raw_values: [selectionValue],
          is_selected: barChartSelectionIncludes(selectedGroups, [selectionValue]),
        }
      }),
    },
    mark: {
      type: 'bar',
      cursor: 'pointer',
      stroke: opaqueColor(theme.axisDomainColor),
      strokeWidth: borderThickness,
      ...(isStacked
        ? {
          cornerRadiusTopLeft: { signal: 'datum.max_aggregate_value_end > 0 ? 3 : 0' },
          cornerRadiusTopRight: { signal: 'datum.max_aggregate_value_end > 0 ? 3 : 0' },
          cornerRadiusBottomLeft: { signal: 'datum.min_aggregate_value_start < 0 ? 3 : 0' },
          cornerRadiusBottomRight: { signal: 'datum.min_aggregate_value_start < 0 ? 3 : 0' },
        }
        : {
          cornerRadiusTopLeft: { signal: 'datum.aggregate_value > 0 ? 3 : 0' },
          cornerRadiusTopRight: { signal: 'datum.aggregate_value > 0 ? 3 : 0' },
          cornerRadiusBottomLeft: { signal: 'datum.aggregate_value < 0 ? 3 : 0' },
          cornerRadiusBottomRight: { signal: 'datum.aggregate_value < 0 ? 3 : 0' },
        }),
    },
    encoding: {
      x: {
        field: 'category_label',
        type: 'nominal',
        sort: { field: 'category_index', order: 'ascending' },
        scale: {
          paddingInner: xPaddingInner,
          paddingOuter: 0.08,
        },
        axis: {
          ...buildAxisSpec(overrides.xAxis, defaultOverrides.xAxis.label, chartScale),
          labelAngle: -30,
        },
      },
      y: {
        field: 'aggregate_value',
        type: 'quantitative',
        stack: isStacked ? (isNormalized ? 'normalize' : 'zero') : false,
        scale: {
          type: yScaleType,
          ...(yScaleType !== 'log' ? { zero: !isNormalized } : {}),
          nice: yScaleType !== 'log',
        },
        axis: {
          ...buildAxisSpec(overrides.yAxis, defaultOverrides.yAxis.label, chartScale),
          ...(isNormalized ? { title: 'Percentage', format: isStacked ? '.1%' : '.1f' } : {}),
        },
      },
      color: colorScale ? {
        field: 'group_label',
        type: 'nominal',
        scale: { domain: colorScale.domain, range: colorScale.range },
        legend: { title: barChart.group_column ?? 'Group', symbolStrokeWidth: 0 },
      } : {
        field: 'color',
        type: 'nominal',
        scale: null,
        legend: null,
      },
      opacity: {
        condition: {
          test: selectedGroups.length ? 'datum.is_selected' : 'true',
          value: 0.96,
        },
        value: selectedGroups.length ? 0.34 : 0.96,
      },
      tooltip: tooltipFields,
    },
  }

  if (hasGroup) {
    spec.params = [
      {
        name: barChartLegendParamName(),
        select: { type: 'point' as const, fields: ['group_label'], toggle: 'true', clear: false },
        bind: 'legend' as const,
      },
    ]
  }

  if (hasGroup && !isStacked) {
    spec.encoding = {
      ...spec.encoding,
      xOffset: {
        field: 'group_label',
        type: 'nominal',
        sort: { field: 'group_index', order: 'ascending' },
        scale: {
          paddingInner: groupSpacing,
        },
      },
    }
  }

  return spec
}

function buildBarChartColorScale(
  bars: PreparedBarChartPayload['bars'],
): { domain: string[]; range: string[] } {
  const seen = new Map<string, { label: string; color: string; index: number }>()
  for (const bar of bars) {
    if (bar.group !== undefined && bar.group_label) {
      const key = barChartSelectionPrimitiveKey(bar.group)
      if (!seen.has(key)) {
        seen.set(key, {
          label: bar.group_label,
          color: bar.color,
          index: bar.group_index ?? seen.size,
        })
      }
    }
  }
  const sorted = [...seen.values()].sort((a, b) => a.index - b.index)
  return {
    domain: sorted.map((e) => e.label),
    range: sorted.map((e) => e.color),
  }
}

function isBarChartLegendItem(item: unknown): boolean {
  if (!item || typeof item !== 'object') {
    return false
  }
  const role = typeof (item as { mark?: { role?: unknown } }).mark?.role === 'string'
    ? (item as { mark?: { role?: string } }).mark?.role ?? ''
    : ''
  return role.includes('legend')
}

function barChartLegendParamName(): string {
  return 'legend_group'
}

function barChartLegendSignalName(): string {
  return `${barChartLegendParamName()}_group_label_legend`
}

function parseBarChartLegendSignalValue(value: unknown): BarChartSelectionValue | null {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
    ? value
    : null
}

function parseBarChartClickedValues(item: unknown): BarChartSelectionValue[] | null {
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
  const values = rawValues.filter((value): value is BarChartSelectionValue => (
    typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
  ))
  return values.length ? values : null
}

function barChartVisibleSelection(
  selectedCategories: BarChartSelectionValue[],
  barChart: PreparedBarChartPayload,
): BarChartSelectionValue[] {
  if (!selectedCategories.length) {
    return []
  }
  const normalizedSelection = normalizeBarChartSelection(selectedCategories)
  const hasGroup = Boolean(barChart.group_column)
  const visibleValues = new Set(
    barChart.bars.map((bar) => barChartSelectionPrimitiveKey(hasGroup ? (bar.group ?? bar.value) : bar.value)),
  )
  return normalizedSelection.filter((value) => visibleValues.has(barChartSelectionPrimitiveKey(value)))
}

function barChartSelectionIncludes(
  selectedCategories: BarChartSelectionValue[],
  barValues: BarChartSelectionValue[],
): boolean {
  if (!selectedCategories.length || !barValues.length) {
    return false
  }
  const selectedKeys = new Set(
    normalizeBarChartSelection(selectedCategories).map((value) => barChartSelectionPrimitiveKey(value)),
  )
  return normalizeBarChartSelection(barValues)
    .every((value) => selectedKeys.has(barChartSelectionPrimitiveKey(value)))
}

function toggleBarChartSelection(
  selectedCategories: BarChartSelectionValue[],
  clickedValues: BarChartSelectionValue[],
  additive: boolean,
): BarChartSelectionValue[] {
  const normalizedClickedValues = normalizeBarChartSelection(clickedValues)
  if (!additive) {
    return barChartSelectionsEqual(normalizedClickedValues, selectedCategories) ? [] : normalizedClickedValues
  }
  const clickedKeys = new Set(normalizedClickedValues.map((value) => barChartSelectionPrimitiveKey(value)))
  if (normalizedClickedValues.every((value) => selectedCategories.some((selected) => barChartSelectionPrimitiveKey(selected) === barChartSelectionPrimitiveKey(value)))) {
    return normalizeBarChartSelection(
      selectedCategories.filter((value) => !clickedKeys.has(barChartSelectionPrimitiveKey(value))),
    )
  }
  return normalizeBarChartSelection([...selectedCategories, ...normalizedClickedValues])
}

function barChartSelectionsEqual(left: BarChartSelectionValue[], right: BarChartSelectionValue[]): boolean {
  const normalizedLeft = normalizeBarChartSelection(left)
  const normalizedRight = normalizeBarChartSelection(right)
  if (normalizedLeft.length !== normalizedRight.length) {
    return false
  }
  for (let index = 0; index < normalizedLeft.length; index += 1) {
    if (barChartSelectionPrimitiveKey(normalizedLeft[index]) !== barChartSelectionPrimitiveKey(normalizedRight[index])) {
      return false
    }
  }
  return true
}

function normalizeBarChartSelection(values: BarChartSelectionValue[]): BarChartSelectionValue[] {
  return [...new Map(values.map((value) => [barChartSelectionPrimitiveKey(value), value])).values()]
    .sort((left, right) => barChartSelectionPrimitiveKey(left).localeCompare(barChartSelectionPrimitiveKey(right)))
}

function barChartSelectionPrimitiveKey(value: BarChartSelectionValue): string {
  return `${typeof value}:${String(value)}`
}

function formatBarChartAggregateValue(value: number): string {
  if (Number.isInteger(value)) {
    return String(value)
  }
  return value.toFixed(2).replace(/\.00$/, '').replace(/(\.[1-9])0$/, '$1')
}

function capitalizeAggregation(value: string): string {
  if (!value.length) {
    return 'Value'
  }
  return value[0].toUpperCase() + value.slice(1)
}

function formatBarChartProportion(value: number): string {
  return `${(value * 100).toFixed(1).replace(/\.0$/, '')}%`
}
