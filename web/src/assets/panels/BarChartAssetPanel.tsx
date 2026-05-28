import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import embed, { type Result as VegaEmbedResult, type VisualizationSpec } from 'vega-embed'

import { prepareAsset } from '../../lib/api'
import type { AssetFilter, AssetSort, PreparedBarChartPayload } from '../../lib/types'
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
  defaultHistogramChartOverrides,
  filterKindsForDataType,
  histogramChartOverridesFromModifiers,
  initialTableStateFromModifiers,
  modifierFieldLabelClassName,
  modifierColumnsFromSchema,
  modifierTitle,
  nextSortForColumn,
  normalizePanelHeight,
  optionalNonNegativeNumberFromInput,
  optionalNumberFromInput,
  removeFilter,
  serializeHistogramChartModifierValues,
  stableValueKey,
  tableStateKey,
  upsertFilter,
  valuesEqual,
} from '../shared/modifiers'
import type { DatavizAssetPanelProps, HistogramChartOverrides } from '../shared/types'
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
  sectionId,
  frameVariant,
}: DatavizAssetPanelProps) {
  const prepareNodeId = prepareTarget?.nodeId ?? nodeId
  const prepareAssetName = prepareTarget?.assetName ?? asset.asset_name
  const preparePanelContext = prepareTarget?.panelContext ?? null
  const modifierColumns = useMemo(() => modifierColumnsFromSchema(asset.modifier_schema), [asset.modifier_schema])
  const chartOverrideDefaults = useMemo(
    () => defaultHistogramChartOverrides(asset.default_modifiers, asset.modifier_schema),
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
    () => histogramChartOverridesFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}, asset.modifier_schema),
    [asset.default_modifiers, asset.modifier_schema, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const [pageIndex, setPageIndex] = useState(initialTableState.page.index)
  const [pageSize, setPageSize] = useState(initialTableState.page.size)
  const [sort, setSort] = useState<AssetSort | null>(initialTableState.sort)
  const [filters, setFilters] = useState<AssetFilter[]>(initialTableState.filters)
  const [chartOverrides, setChartOverrides] = useState<HistogramChartOverrides>(initialChartOverrides)
  const [selectedCategories, setSelectedCategories] = useState<BarChartSelectionValue[]>([])
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
    if (overrideIncompatible || isApplyingPersistedStateRef.current) {
      return
    }
    const modifierOverrides = buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
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
  }, [asset.default_modifiers, asset.override_schema_hash, chartOverrides, filters, onPersistedStateChange, overrideIncompatible, pageIndex, pageSize, persistedState, sort])

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
      selectionKey,
      stableValueKey(preparePanelContext),
    ],
    queryFn: () => prepareAsset(prepareNodeId, prepareAssetName, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: {
        page: { index: pageIndex, size: pageSize },
        sort: sort ? [sort] : [],
        filters,
      },
      transient_modifiers: selectedCategories.length ? {
        selected_categories: selectedCategories,
      } : {},
      panel_context: preparePanelContext,
    }),
    enabled: asset.current_asset_version_id !== null && !overrideIncompatible,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const mainPayload = response?.payloads.main ?? null
  const barChart = mainPayload?.kind === 'bar_chart' ? mainPayload : null
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
  const totalRows = barChart?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const displayedRows = table?.rows_total ?? totalRows
  const baseRows = typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : totalRows
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const pageCount = Math.max(1, Math.ceil(displayedRows / Math.max(resolvedPage.size, 1)))
  const canGoPrevious = resolvedPage.index > 0
  const canGoNext = resolvedPage.index + 1 < pageCount
  const resolvedPanelHeight = normalizePanelHeight(panelHeight) ?? DEFAULT_HISTOGRAM_CHART_HEIGHT
  const hasSettingsOverrides = Object.keys(buildModifierOverridesRecord(
    serializeHistogramChartModifierValues(chartOverrides),
    asset.default_modifiers,
  )).length > 0

  useEffect(() => {
    setPageInput(String(resolvedPage.index + 1))
  }, [resolvedPage.index])

  useEffect(() => {
    onReadyStateChange?.(isPanelReady)
  }, [isPanelReady, onReadyStateChange])

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
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} settingsTitle="Modifier overrides" settingsBody={settingsBody} settingsActive={hasSettingsOverrides} sectionId={sectionId} frameVariant={frameVariant}>
      <div className="asset-dataframe-panel asset-histogram-panel">
        {overrideIncompatible ? <OverrideIncompatibleNotice onReset={onPersistedStateChange ? handleResetOverrides : undefined} /> : null}
        <PrepareErrorsNotice errors={response?.errors ?? []} />
        <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange}>
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
          <PreparedAssetTableSection
            table={table}
            columns={availableColumns}
            activeSort={resolvedSort}
            activeFilters={resolvedFilters}
            viewerMode={viewerMode}
            disabled={overrideIncompatible || prepareQuery.isFetching}
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
  selectedCategories,
  onSelectionChange,
}: {
  barChart: PreparedBarChartPayload
  chartHeight: number
  overrides: HistogramChartOverrides
  defaultOverrides: HistogramChartOverrides
  selectedCategories: BarChartSelectionValue[]
  onSelectionChange: (categories: BarChartSelectionValue[]) => void
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)
  const viewRef = useRef<VegaEmbedResult | null>(null)
  const onSelectionChangeRef = useRef(onSelectionChange)
  const chartTheme = useAssetChartTheme()

  onSelectionChangeRef.current = onSelectionChange

  const spec = useMemo(
    () => buildBarChartVegaLiteSpec(barChart, selectedCategories, chartTheme, chartHeight, overrides, defaultOverrides),
    [barChart, chartHeight, chartTheme, defaultOverrides, overrides, selectedCategories],
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
          const clickedValues = parseBarChartClickedValues(item)
          if (clickedValues !== null) {
            onSelectionChangeRef.current(
              toggleBarChartSelection(selectedCategories, clickedValues, eventHasShiftKey(_event)),
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
          setChartError(error instanceof Error ? error.message : 'Could not render the bar chart view.')
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
  selectedCategories: BarChartSelectionValue[],
  theme: ReturnType<typeof useAssetChartTheme>,
  chartHeight: number,
  overrides: HistogramChartOverrides,
  defaultOverrides: HistogramChartOverrides,
): VisualizationSpec {
  const borderThickness = optionalNonNegativeNumberFromInput(overrides.borderThickness) ?? 0
  const barWidth = clampPercentage(overrides.barWidth, 90)
  const paddingInner = Math.max(0.02, 1 - (barWidth / 100))
  const yScaleType = buildScaleType(overrides.yAxis.scale)
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
      values: barChart.bars.map((bar, index) => ({
        category_label: bar.label,
        aggregate_value: bar.aggregate_value,
        aggregate_label: formatBarChartAggregateValue(bar.aggregate_value),
        color: bar.color,
        raw_values: [bar.value],
        bar_order: index,
        is_selected: barChartSelectionIncludes(selectedCategories, [bar.value]),
      })),
    },
    mark: {
      type: 'bar',
      cursor: 'pointer',
      stroke: opaqueColor(theme.axisDomainColor),
      strokeWidth: borderThickness,
      cornerRadiusTopLeft: 3,
      cornerRadiusTopRight: 3,
    },
    encoding: {
      x: {
        field: 'category_label',
        type: 'nominal',
        sort: { field: 'bar_order', order: 'ascending' },
        scale: {
          paddingInner,
          paddingOuter: 0.08,
        },
        axis: {
          ...buildAxisSpec(overrides.xAxis, defaultOverrides.xAxis.label),
          labelAngle: -30,
        },
      },
      y: {
        field: 'aggregate_value',
        type: 'quantitative',
        scale: {
          type: yScaleType,
          zero: yScaleType !== 'log',
          nice: yScaleType !== 'log',
        },
        axis: buildAxisSpec(overrides.yAxis, defaultOverrides.yAxis.label),
      },
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
        { field: 'category_label', type: 'nominal' as const, title: barChart.category_column },
        { field: 'aggregate_label', type: 'nominal' as const, title: `${capitalizeAggregation(barChart.aggregation)} of ${barChart.value_column}` },
      ],
    },
  }
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
  const visibleValues = new Set(
    barChart.bars.map((bar) => barChartSelectionPrimitiveKey(bar.value)),
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
