import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import embed, { type Result as VegaEmbedResult, type VisualizationSpec } from 'vega-embed'

import { prepareAsset } from '../../lib/api'
import type { AssetFilter, AssetSort, PreparedHistogramPayload } from '../../lib/types'
import {
  buildAxisSpec,
  buildChartPadding,
  buildChartTitle,
  buildScaleType,
  buildVegaLiteChartConfig,
  formatHistogramBound,
  parseSelectionRangeSignal,
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
  binCountFromValue,
  buildModifierOverridesRecord,
  clampPercentage,
  defaultHistogramChartOverrides,
  filterKindsForDataType,
  granularityFromValue,
  histogramChartOverridesFromModifiers,
  histogramStateKey,
  initialHistogramStateFromModifiers,
  modifierFieldLabelClassName,
  modifierColumnsFromSchema,
  modifierDefaultValue,
  modifierTitle,
  nextSortForColumn,
  normalizePanelHeight,
  optionalIntegerFromInput,
  optionalNonNegativeNumberFromInput,
  optionalNumberFromInput,
  removeFilter,
  serializeHistogramChartModifierValues,
  stableValueKey,
  upsertFilter,
  valuesEqual,
} from '../shared/modifiers'
import type { DatavizAssetPanelProps, HistogramChartOverrides, HistogramSelectionRange, TimeHistogramGranularity } from '../shared/types'
import { DEFAULT_HISTOGRAM_CHART_HEIGHT, HISTOGRAM_BRUSH_SIGNAL_NAME } from '../shared/types'

export function HistogramAssetPanel({
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
  const isTimeHistogram = asset.asset_type === 'time_histogram'
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
  const [binCount, setBinCount] = useState(initialState.binCount ?? 20)
  const [binCountInput, setBinCountInput] = useState(String(initialState.binCount ?? 20))
  const [granularity, setGranularity] = useState<TimeHistogramGranularity>(initialState.granularity ?? 'auto')
  const [chartOverrides, setChartOverrides] = useState<HistogramChartOverrides>(initialChartOverrides)
  const [selectedBarIndexes, setSelectedBarIndexes] = useState<number[]>([])
  const [pageInput, setPageInput] = useState(String(initialState.page.index + 1))
  const currentHistogramRef = useRef<PreparedHistogramPayload | null>(null)
  const requiresOverrideValidation = Boolean(
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
    [initialState.binCount, initialState.filters, initialState.granularity, initialState.page.index, initialState.page.size, initialState.sort?.column, initialState.sort?.direction],
  )
  const externalChartOverridesKey = useMemo(() => stableValueKey(initialChartOverrides), [initialChartOverrides])
  const localStateKey = histogramStateKey({
    page: { index: pageIndex, size: pageSize },
    sort,
    filters,
    binCount: isTimeHistogram ? null : binCount,
    granularity: isTimeHistogram ? granularity : null,
  })
  const localChartOverridesKey = stableValueKey(chartOverrides)
  const modifierOverrides = useMemo(
    () => buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
      ...(isTimeHistogram ? { granularity } : { bin_count: binCount }),
      ...serializeHistogramChartModifierValues(chartOverrides),
    }, asset.default_modifiers),
    [asset.default_modifiers, binCount, chartOverrides, filters, granularity, isTimeHistogram, pageIndex, pageSize, sort],
  )
  const overrideValidationKey = requiresOverrideValidation ? stableValueKey(modifierOverrides) : null

  useEffect(() => {
    if (localStateKey === externalStateKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setPageIndex(initialState.page.index)
    setPageSize(initialState.page.size)
    setSort(initialState.sort)
    setFilters(initialState.filters)
    setBinCount(initialState.binCount ?? 20)
    setBinCountInput(String(initialState.binCount ?? 20))
    setGranularity(initialState.granularity ?? 'auto')
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
    setSelectedBarIndexes([])
  }, [asset.current_asset_version_id, binCount, filtersKey, granularity])

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
      isTimeHistogram ? granularity : binCount,
      selectionKey,
      persistedState?.override_schema_hash ?? null,
      overrideValidationKey,
      stableValueKey(preparePanelContext),
    ],
    queryFn: () => prepareAsset(prepareNodeId, prepareAssetName, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: modifierOverrides,
      transient_modifiers: currentHistogramRef.current && selectedBarIndexes.length ? {
        selection_ranges: histogramSelectionRangesFromIndexes(currentHistogramRef.current, selectedBarIndexes),
      } : {},
      panel_context: preparePanelContext,
      persisted_override_schema_hash: persistedState?.override_schema_hash ?? null,
    }),
    enabled: asset.current_asset_version_id !== null,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const mainPayload = response?.payloads.main ?? null
  const histogram = mainPayload?.kind === 'histogram' ? mainPayload : null
  const overrideIncompatible = Boolean(response?.errors.some((error) => error.code === 'override_incompatible'))
  const overrideValidationBlocked = requiresOverrideValidation && (prepareQuery.isFetching || !prepareQuery.isSuccess)
  const prepareErrors = response?.errors.filter((error) => error.code !== 'override_incompatible') ?? []
  const isPanelReady = overrideIncompatible || prepareQuery.isError || prepareQuery.isSuccess
  currentHistogramRef.current = histogram
  const table = response?.payloads.table ?? null
  const resolvedPage = table?.page ?? { index: pageIndex, size: pageSize }
  const resolvedSort = table?.sort?.[0] ?? null
  const resolvedFilters = Array.isArray(response?.resolved_modifiers.filters) ? response.resolved_modifiers.filters : filters
  const resolvedBinCount = typeof response?.resolved_modifiers.bin_count === 'number' ? response.resolved_modifiers.bin_count : binCount
  const resolvedGranularity = granularityFromValue(response?.resolved_modifiers.granularity) ?? granularity
  const availableColumns = modifierColumns.length
    ? modifierColumns
    : (table?.columns ?? []).map((column) => ({
      id: column.id,
      title: column.title,
      dataType: column.data_type,
      filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
    }))
  const totalRows = histogram?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const displayedRows = table?.rows_total ?? totalRows
  const baseRows = typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : totalRows
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const pageCount = Math.max(1, Math.ceil(displayedRows / Math.max(resolvedPage.size, 1)))
  const canGoPrevious = resolvedPage.index > 0
  const canGoNext = resolvedPage.index + 1 < pageCount
  const resolvedPanelHeight = normalizePanelHeight(panelHeight) ?? DEFAULT_HISTOGRAM_CHART_HEIGHT
  const defaultBinCount = binCountFromValue(modifierDefaultValue(asset.default_modifiers, asset.modifier_schema, 'bin_count')) ?? initialState.binCount ?? 20
  const defaultGranularity = granularityFromValue(modifierDefaultValue(asset.default_modifiers, asset.modifier_schema, 'granularity')) ?? initialState.granularity ?? 'auto'
  const hasSettingsOverrides = Object.keys(buildModifierOverridesRecord({
    ...(isTimeHistogram ? { granularity } : { bin_count: binCount }),
    ...serializeHistogramChartModifierValues(chartOverrides),
  }, asset.default_modifiers)).length > 0

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
    setBinCountInput(String(resolvedBinCount))
  }, [resolvedBinCount])

  useEffect(() => {
    setGranularity(resolvedGranularity)
  }, [resolvedGranularity])

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
    setBinCount(resetState.binCount ?? 20)
    setBinCountInput(String(resetState.binCount ?? 20))
    setGranularity(resetState.granularity ?? 'auto')
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
    setGranularity(defaultGranularity)
    setChartOverrides(chartOverrideDefaults)
  }

  function handleClearTableFilters() {
    setPageIndex(0)
    setSort(null)
    setFilters([])
    setSelectedBarIndexes([])
    setPageInput('1')
  }

  const settingsBody = (
    <>
      <div className="asset-dataviz-settings-actions">
        <button type="button" className="secondary asset-dataviz-settings-reset" onClick={handleResetSettingsOverrides} disabled={!hasSettingsOverrides}>
          Reset to default
        </button>
      </div>

      <PanelSettingsSection title="Histogram">
        {isTimeHistogram ? (
          <label className="asset-dataviz-field">
            <span className={modifierFieldLabelClassName(!valuesEqual(granularity, defaultGranularity))}>{modifierTitle(asset.modifier_schema, 'granularity', 'Granularity')}</span>
            <select
              aria-label="Time histogram granularity"
              value={granularity}
              onChange={(event) => {
                setPageIndex(0)
                setGranularity((granularityFromValue(event.target.value) ?? 'auto') as TimeHistogramGranularity)
              }}
              disabled={overrideIncompatible || overrideValidationBlocked || prepareQuery.isFetching}
            >
              <option value="auto">Auto</option>
              <option value="year">Year</option>
              <option value="month">Month</option>
              <option value="week">Week</option>
              <option value="day">Day</option>
              <option value="hour">Hour</option>
            </select>
          </label>
        ) : (
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
              disabled={overrideIncompatible || overrideValidationBlocked || prepareQuery.isFetching}
            />
          </label>
        )}

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
        allowLogScale={!isTimeHistogram}
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
        <PrepareErrorsNotice errors={prepareErrors} />
        <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange}>
          {(chartHeight) => (
            <>
              {prepareQuery.isLoading && !histogram ? <LoadingPlaceholder message="Preparing histogram view..." /> : null}
              {prepareQuery.isError ? (
                <ErrorPlaceholder message={prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the histogram view.'} />
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
          <PreparedAssetTableSection
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
            hasTemporarySelection={selectedBarIndexes.length > 0}
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
          let nextIndexes: number[]
          if (clickedIndex === null) {
            nextIndexes = []
          } else if ((event as MouseEvent).shiftKey) {
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
    return <LoadingPlaceholder message="No rows match the current histogram filters." />
  }

  if (chartError) {
    return <ErrorPlaceholder message={chartError} />
  }

  return (
    <div
      ref={mountRef}
      className="asset-histogram-chart-shell asset-histogram-vega-mount"
      style={{ '--asset-dataviz-height': `${chartHeight}px` } as CSSProperties}
    />
  )
}

function buildHistogramVegaLiteSpec(
  histogram: PreparedHistogramPayload,
  theme: ReturnType<typeof useAssetChartTheme>,
  chartHeight: number,
  overrides: HistogramChartOverrides,
  defaultOverrides: HistogramChartOverrides,
): VisualizationSpec {
  const barWidthRatio = overrides.barWidth / 100
  const borderThickness = optionalNonNegativeNumberFromInput(overrides.borderThickness) ?? 0
  const temporalHistogram = histogram.x_value_kind === 'temporal'
  const temporalTickLimit = Math.max(
    1,
    optionalIntegerFromInput(overrides.xAxis.tickCount)
      ?? optionalIntegerFromInput(defaultOverrides.xAxis.tickCount)
      ?? 20,
  )
  const temporalAxisTicks = temporalHistogram
    ? buildCenteredHistogramAxisTicks(histogram, temporalTickLimit)
    : null
  const xAxisSpec = {
    ...buildAxisSpec(overrides.xAxis, defaultOverrides.xAxis.label),
    ...(temporalAxisTicks ? {
      values: temporalAxisTicks.values,
      labelExpr: temporalAxisTicks.labelExpr,
    } : {}),
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
      values: histogram.bins.map((bin) => ({
        ...bin,
        label: bin.label ?? `${formatHistogramBound(bin.start)} to ${formatHistogramBound(bin.end)}`,
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
      cornerRadiusTopLeft: 3,
      cornerRadiusTopRight: 3,
      stroke: borderThickness > 0 ? '#1d4ed8' : undefined,
      strokeWidth: borderThickness,
    },
    encoding: {
      x: {
        field: 'adjusted_start',
        type: 'quantitative',
        axis: xAxisSpec,
        scale: {
          ...(histogram.domain ? {
            domain: [histogram.domain.min, histogram.domain.max],
            nice: false,
            zero: false,
          } : {}),
          type: temporalHistogram ? 'linear' : buildScaleType(overrides.xAxis.scale),
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

function buildCenteredHistogramAxisTicks(
  histogram: PreparedHistogramPayload,
  maxTickLabels: number,
): { values: number[]; labelExpr: string } | null {
  const granularity = histogram.time_granularity
  if (!granularity || !histogram.bins.length) {
    return null
  }
  const selectedBins = selectHistogramAxisBins(histogram.bins, maxTickLabels)
  const includeYear = histogramSpansMultipleYears(histogram)
  const labelsByCenter = Object.fromEntries(
    selectedBins.map((bin) => [
      String((bin.start + bin.end) / 2),
      formatHistogramAxisTickLabel(bin.start, granularity, includeYear),
    ]),
  )
  return {
    values: selectedBins.map((bin) => (bin.start + bin.end) / 2),
    labelExpr: `${JSON.stringify(labelsByCenter)}[toString(datum.value)] || ''`,
  }
}

function selectHistogramAxisBins(bins: PreparedHistogramPayload['bins'], maxTicks: number) {
  if (bins.length <= maxTicks) {
    return bins
  }
  const step = Math.ceil(bins.length / Math.max(maxTicks, 1))
  return bins.filter((_, index) => index % step === 0)
}

function formatHistogramAxisTickLabel(
  start: number,
  granularity: NonNullable<PreparedHistogramPayload['time_granularity']>,
  includeYear: boolean,
): string {
  const value = new Date(start)
  if (granularity === 'year') {
    return String(value.getUTCFullYear())
  }
  if (granularity === 'month') {
    return `${MONTH_LABELS[value.getUTCMonth()]} ${value.getUTCFullYear()}`
  }
  if (granularity === 'hour') {
    const hourLabel = `${String(value.getUTCHours()).padStart(2, '0')}:00`
    return includeYear
      ? `${MONTH_LABELS[value.getUTCMonth()]} ${value.getUTCDate()}, ${value.getUTCFullYear()} ${hourLabel}`
      : `${MONTH_LABELS[value.getUTCMonth()]} ${value.getUTCDate()} ${hourLabel}`
  }
  return includeYear
    ? `${MONTH_LABELS[value.getUTCMonth()]} ${value.getUTCDate()}, ${value.getUTCFullYear()}`
    : `${MONTH_LABELS[value.getUTCMonth()]} ${value.getUTCDate()}`
}

function histogramSpansMultipleYears(histogram: PreparedHistogramPayload): boolean {
  if (!histogram.bins.length) {
    return false
  }
  const firstYear = new Date(histogram.bins[0].start).getUTCFullYear()
  const lastYear = new Date(histogram.bins[histogram.bins.length - 1].start).getUTCFullYear()
  return firstYear !== lastYear
}

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

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

function toggleHistogramBarIndex(selectedBarIndexes: number[], clickedIndex: number): number[] {
  const uniqueIndexes = [...new Set(selectedBarIndexes)].sort((left, right) => left - right)
  if (uniqueIndexes.includes(clickedIndex)) {
    return uniqueIndexes.filter((index) => index !== clickedIndex)
  }
  return [...uniqueIndexes, clickedIndex].sort((left, right) => left - right)
}
