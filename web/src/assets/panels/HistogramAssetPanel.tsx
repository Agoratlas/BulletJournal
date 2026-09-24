import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import embed, { type Result as VegaEmbedResult, type VisualizationSpec } from 'vega-embed'

import { prepareAsset } from '../../lib/api'
import { groupedHistogramBounds, normalizeGroupedValues, stackStarts, topStackSegmentIndexes } from '../shared/groupedChart'
import { histogramGroupFromLegendLabel, histogramSegmentIsSelected, histogramSelectionFromGroupClick, histogramSelectionFromRange, type HistogramGroupValue } from '../shared/histogramSelection'
import type { AssetFilter, AssetHighlight, AssetSort, PreparedHistogramPayload } from '../../lib/types'
import {
  buildAxisSpec,
  buildChartPadding,
  buildChartTitle,
  buildScaleType,
  buildVegaLiteChartConfig,
  eventHasShiftKey,
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
  defaultBarWidthForGroupMode,
  groupedChartDefaultsForDiff,
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
  replaceHighlightsForColumn,
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
  isPanelResized,
  chartScale = 1,
  minPanelHeight,
  sectionId,
  frameVariant,
}: DatavizAssetPanelProps) {
  const prepareNodeId = prepareTarget?.nodeId ?? nodeId
  const prepareAssetName = prepareTarget?.assetName ?? asset.asset_name
  const preparePanelContext = prepareTarget?.panelContext ?? null
  const isTemporalHistogram = asset.modifier_schema.some((entry) => entry.id === 'granularity')
  const modifierColumns = useMemo(() => modifierColumnsFromSchema(asset.modifier_schema), [asset.modifier_schema])
  const chartOverrideDefaults = useMemo(
    () => defaultHistogramChartOverrides(asset.default_modifiers, asset.modifier_schema, Boolean(asset.definition?.histogram_group_column)),
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
    () => histogramChartOverridesFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}, asset.modifier_schema, Boolean(asset.definition?.histogram_group_column)),
    [asset.default_modifiers, asset.modifier_schema, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const [pageIndex, setPageIndex] = useState(initialState.page.index)
  const [pageSize, setPageSize] = useState(initialState.page.size)
  const [sort, setSort] = useState<AssetSort | null>(initialState.sort)
  const [filters, setFilters] = useState<AssetFilter[]>(initialState.filters)
  const [highlights, setHighlights] = useState<AssetHighlight[]>(initialState.highlights ?? [])
  const [binCount, setBinCount] = useState(initialState.binCount ?? 20)
  const [binCountInput, setBinCountInput] = useState(String(initialState.binCount ?? 20))
  const [timeGranularity, setTimeGranularity] = useState<TimeHistogramGranularity>(initialState.granularity ?? 'auto')
  const [chartOverrides, setChartOverrides] = useState<HistogramChartOverrides>(initialChartOverrides)
  const [selectedBarIndexes, setSelectedBarIndexes] = useState<number[]>([])
  const [selectedGroups, setSelectedGroups] = useState<Array<string | number | boolean>>([])
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
  const highlightsKey = JSON.stringify(highlights)
  const selectionKey = stableValueKey({ selectedBarIndexes, selectedGroups })
  const externalStateKey = useMemo(
    () => histogramStateKey(initialState),
    [initialState.binCount, initialState.filters, initialState.granularity, initialState.highlights, initialState.page.index, initialState.page.size, initialState.sort?.column, initialState.sort?.direction],
  )
  const externalChartOverridesKey = useMemo(() => stableValueKey(initialChartOverrides), [initialChartOverrides])
  const localStateKey = histogramStateKey({
    page: { index: pageIndex, size: pageSize },
    sort,
    filters,
    highlights,
    binCount: isTemporalHistogram ? null : binCount,
    granularity: isTemporalHistogram ? timeGranularity : null,
  })
  const localChartOverridesKey = stableValueKey(chartOverrides)
  const modifierOverrides = useMemo(
    () => buildModifierOverridesRecord({
      page: { index: pageIndex, size: pageSize },
      sort: sort ? [sort] : [],
      filters,
      highlights,
      ...(isTemporalHistogram ? { granularity: timeGranularity } : { bin_count: binCount }),
      ...serializeHistogramChartModifierValues(chartOverrides),
    }, groupedChartDefaultsForDiff(asset.default_modifiers, chartOverrides, Boolean(asset.definition?.histogram_group_column))),
    [asset.default_modifiers, binCount, chartOverrides, filters, highlights, isTemporalHistogram, pageIndex, pageSize, sort, timeGranularity],
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
    setHighlights(initialState.highlights ?? [])
    setBinCount(initialState.binCount ?? 20)
    setBinCountInput(String(initialState.binCount ?? 20))
    setTimeGranularity(initialState.granularity ?? 'auto')
    setSelectedBarIndexes([])
    setSelectedGroups([])
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
    setSelectedGroups([])
  }, [asset.current_asset_version_id, binCount, filtersKey, timeGranularity])

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
      isTemporalHistogram ? timeGranularity : binCount,
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
        ...(selectedGroups.length ? { selected_groups: selectedGroups } : {}),
      } : selectedGroups.length ? { selected_groups: selectedGroups } : {},
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
  const resolvedHighlights = Array.isArray(response?.resolved_modifiers.highlights)
    ? response.resolved_modifiers.highlights as AssetHighlight[]
    : highlights
  const resolvedBinCount = typeof response?.resolved_modifiers.bin_count === 'number' ? response.resolved_modifiers.bin_count : binCount
  const resolvedTimeGranularity = granularityFromValue(response?.resolved_modifiers.granularity) ?? timeGranularity
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
  const hasGroups = Boolean(histogram?.group_column)
  const defaultBinCount = binCountFromValue(modifierDefaultValue(asset.default_modifiers, asset.modifier_schema, 'bin_count')) ?? initialState.binCount ?? 20
  const defaultTimeGranularity = granularityFromValue(modifierDefaultValue(asset.default_modifiers, asset.modifier_schema, 'granularity')) ?? initialState.granularity ?? 'auto'
  const hasSettingsOverrides = Object.keys(buildModifierOverridesRecord({
    ...(isTemporalHistogram ? { granularity: timeGranularity } : { bin_count: binCount }),
    ...serializeHistogramChartModifierValues(chartOverrides),
  }, groupedChartDefaultsForDiff(asset.default_modifiers, chartOverrides, Boolean(asset.definition?.histogram_group_column)))).length > 0

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
    setTimeGranularity(resolvedTimeGranularity)
  }, [resolvedTimeGranularity])

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
    setHighlights(resetState.highlights ?? [])
    setBinCount(resetState.binCount ?? 20)
    setBinCountInput(String(resetState.binCount ?? 20))
    setTimeGranularity(resetState.granularity ?? 'auto')
    setChartOverrides(chartOverrideDefaults)
    setSelectedBarIndexes([])
    setSelectedGroups([])
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
    setTimeGranularity(defaultTimeGranularity)
    setChartOverrides(chartOverrideDefaults)
  }

  function handleClearTableFilters() {
    setPageIndex(0)
    setSort(null)
    setFilters([])
    setSelectedBarIndexes([])
    setSelectedGroups([])
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
        {isTemporalHistogram ? (
          <label className="asset-dataviz-field">
            <span className={modifierFieldLabelClassName(!valuesEqual(timeGranularity, defaultTimeGranularity))}>{modifierTitle(asset.modifier_schema, 'granularity', 'Granularity')}</span>
            <select
              aria-label="Time histogram granularity"
              value={timeGranularity}
              onChange={(event) => {
                setPageIndex(0)
                setTimeGranularity((granularityFromValue(event.target.value) ?? 'auto') as TimeHistogramGranularity)
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

        {hasGroups ? (
          <PanelSettingsSection title="Groups">
            <label className="asset-dataviz-field">
              <span>{modifierTitle(asset.modifier_schema, 'group_mode', 'Group mode')}</span>
              <select value={chartOverrides.groupMode} onChange={(event) => setChartOverrides((current) => ({
                ...current,
                groupMode: event.target.value === 'stacked' ? 'stacked' : 'grouped',
                barWidth: current.barWidth === defaultBarWidthForGroupMode(asset.default_modifiers, current.groupMode, true)
                  ? defaultBarWidthForGroupMode(asset.default_modifiers, event.target.value === 'stacked' ? 'stacked' : 'grouped', true)
                  : current.barWidth,
              }))}>
                <option value="grouped">Grouped</option>
                <option value="stacked">Stacked</option>
              </select>
            </label>
            <label className="asset-dataviz-field">
              <span>{modifierTitle(asset.modifier_schema, 'group_normalize', 'Normalize groups')}</span>
              <select value={chartOverrides.groupNormalize} onChange={(event) => setChartOverrides((current) => ({ ...current, groupNormalize: event.target.value as 'none' | 'max' | 'sum' }))}>
                <option value="none">No normalization</option>
                <option value="max">Normalize by max</option>
                <option value="sum">Normalize by sum</option>
              </select>
            </label>
            {chartOverrides.groupMode === 'grouped' ? <label className="asset-dataviz-field"><span>{modifierTitle(asset.modifier_schema, 'group_spacing', 'Group spacing')}</span><div className="asset-dataviz-slider-field"><input type="range" min={0} max={50} value={chartOverrides.groupSpacing} onChange={(event) => setChartOverrides((current) => ({ ...current, groupSpacing: clampPercentage(Number(event.target.value), current.groupSpacing) }))} /><strong>{chartOverrides.groupSpacing}%</strong></div></label> : null}
          </PanelSettingsSection>
        ) : null}
      </PanelSettingsSection>

      <AxisOverridesSection
        title={modifierTitle(asset.modifier_schema, 'x_axis', 'X axis')}
        overrides={chartOverrides.xAxis}
        defaultOverrides={chartOverrideDefaults.xAxis}
        defaultLabel={chartOverrideDefaults.xAxis.label}
        onChange={(next) => setChartOverrides((current) => ({ ...current, xAxis: next }))}
        allowLogScale={!isTemporalHistogram}
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
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} settingsTitle="Modifier overrides" settingsBody={settingsBody} settingsActive={hasSettingsOverrides} sectionId={sectionId} frameVariant={frameVariant} showExportActions isPanelResized={isPanelResized}>
      <div className="asset-dataframe-panel asset-histogram-panel">
        {overrideIncompatible ? <OverrideIncompatibleNotice onReset={onPersistedStateChange ? handleResetOverrides : undefined} /> : null}
        <PrepareErrorsNotice errors={prepareErrors} />
        <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange} isResized={isPanelResized} minHeight={minPanelHeight}>
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
                  chartScale={chartScale}
                  selectedBarIndexes={selectedBarIndexes}
                  onSelectionChange={(nextIndexes) => {
                    setPageIndex(0)
                    setSelectedBarIndexes(nextIndexes)
                  }}
                  selectedGroups={selectedGroups}
                  onGroupSelectionChange={(nextGroups) => {
                    setPageIndex(0)
                    setSelectedGroups(nextGroups)
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
            hasTemporarySelection={selectedBarIndexes.length > 0 || selectedGroups.length > 0}
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

function HistogramChart({
  histogram,
  chartHeight,
  overrides,
  defaultOverrides,
  chartScale,
  selectedBarIndexes,
  onSelectionChange,
  selectedGroups,
  onGroupSelectionChange,
}: {
  histogram: PreparedHistogramPayload
  chartHeight: number
  overrides: HistogramChartOverrides
  defaultOverrides: HistogramChartOverrides
  chartScale: number
  selectedBarIndexes: number[]
  onSelectionChange: (barIndexes: number[]) => void
  selectedGroups: HistogramGroupValue[]
  onGroupSelectionChange: (groups: HistogramGroupValue[]) => void
}) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)
  const viewRef = useRef<VegaEmbedResult | null>(null)
  const initialChartHeightRef = useRef(chartHeight)
  const selectedBarIndexesRef = useRef<number[]>(selectedBarIndexes)
  const selectedGroupsRef = useRef<HistogramGroupValue[]>(selectedGroups)
  const onSelectionChangeRef = useRef(onSelectionChange)
  const onGroupSelectionChangeRef = useRef(onGroupSelectionChange)
  const shiftHeldRef = useRef(false)
  const suppressNextClickRef = useRef(false)
  const dragStateRef = useRef<{
    startedOnBarIndex: number | null
    pointerDownX: number
    pointerDownY: number
    moved: boolean
  } | null>(null)
  const chartTheme = useAssetChartTheme()

  selectedBarIndexesRef.current = selectedBarIndexes
  selectedGroupsRef.current = selectedGroups
  onSelectionChangeRef.current = onSelectionChange
  onGroupSelectionChangeRef.current = onGroupSelectionChange

  const spec = useMemo(
    () => buildHistogramVegaLiteSpec(histogram, chartTheme, initialChartHeightRef.current, overrides, defaultOverrides, chartScale, selectedGroups, selectedBarIndexes),
    [chartScale, chartTheme, defaultOverrides, histogram, overrides, selectedGroups, selectedBarIndexes],
  )

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      shiftHeldRef.current = event.shiftKey
    }
    window.addEventListener('keydown', handleKey)
    window.addEventListener('keyup', handleKey)
    if (!mountRef.current) {
      window.removeEventListener('keydown', handleKey)
      window.removeEventListener('keyup', handleKey)
      return
    }
    let disposed = false
    let pointerDownListener: ((event: Event, item: unknown) => void) | null = null
    let clickListener: ((event: Event, item: unknown) => void) | null = null
    let doubleClickListener: ((event: Event) => void) | null = null
    let legendSignalListener: ((_name: string, value: unknown) => void) | null = null

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
        await clearHistogramBrush(result)
        return
      }
      suppressNextClickRef.current = true
      await result.view.runAsync()
      const brushRange = parseSelectionRangeSignal(result.view.signal(HISTOGRAM_BRUSH_SIGNAL_NAME))
      const nextSelection = histogramSelectionFromRange(histogramSelectedBarIndexesFromBrushRange(histogram, brushRange))
      await syncHistogramSelectedBars(result, nextSelection.selectedBarIndexes)
      onSelectionChangeRef.current(nextSelection.selectedBarIndexes)
      if (selectedGroupsRef.current.length) {
        onGroupSelectionChangeRef.current(nextSelection.selectedGroups)
      }
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

        if (histogram.group_column) {
          legendSignalListener = (_name: string, value: unknown) => {
            const group = histogramGroupFromLegendLabel(histogram.bins, value)
            if (group !== null) {
              const nextSelection = histogramSelectionFromGroupClick(selectedGroupsRef.current, group, shiftHeldRef.current)
              void (async () => {
                await syncHistogramSelectedBars(result, nextSelection.selectedBarIndexes)
                onSelectionChangeRef.current(nextSelection.selectedBarIndexes)
                onGroupSelectionChangeRef.current(nextSelection.selectedGroups)
              })()
            }
          }
          result.view.addSignalListener('legend_group_group_label_legend', legendSignalListener)
        }

        pointerDownListener = (event: Event, item: unknown) => {
          const pointerEvent = event as PointerEvent
          if (pointerEvent.button !== 0) {
            return
          }
          suppressNextClickRef.current = false
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
          if (isHistogramLegendItem(item)) {
            return
          }
          const clickedIndex = parseHistogramClickedBarIndex(item, histogram)
          const clickedGroup = parseHistogramClickedGroup(item)
          if (histogram.group_column && clickedGroup !== null) {
            const nextSelection = histogramSelectionFromGroupClick(selectedGroupsRef.current, clickedGroup, eventHasShiftKey(event))
            void (async () => {
              await syncHistogramSelectedBars(resultRef, nextSelection.selectedBarIndexes)
              onSelectionChangeRef.current(nextSelection.selectedBarIndexes)
              onGroupSelectionChangeRef.current(nextSelection.selectedGroups)
            })()
            return
          }
          const clearGroups = clickedIndex === null && selectedGroupsRef.current.length > 0
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
            if (clearGroups) {
              onGroupSelectionChangeRef.current([])
            }
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
      window.removeEventListener('keydown', handleKey)
      window.removeEventListener('keyup', handleKey)
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
      if (viewRef.current && legendSignalListener) {
        viewRef.current.view.removeSignalListener('legend_group_group_label_legend', legendSignalListener)
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
  chartScale: number,
  selectedGroups: HistogramGroupValue[],
  selectedBarIndexes: number[],
): VisualizationSpec {
  const barWidthRatio = overrides.barWidth / 100
  const borderThickness = optionalNonNegativeNumberFromInput(overrides.borderThickness) ?? 0
  const temporalHistogram = histogram.x_value_kind === 'temporal'
  const xScaleType = temporalHistogram ? 'linear' : buildScaleType(overrides.xAxis.scale)
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
    ...buildAxisSpec(overrides.xAxis, defaultOverrides.xAxis.label, chartScale),
    ...(temporalAxisTicks ? {
      values: temporalAxisTicks.values,
      labelExpr: temporalAxisTicks.labelExpr,
    } : {}),
  }
  const hasGroups = Boolean(histogram.group_column)
  const groupCount = Math.max(1, new Set(histogram.bins.map((bin) => bin.group_index)).size)
  const isStacked = hasGroups && overrides.groupMode === 'stacked'
  const isNormalized = hasGroups && overrides.groupNormalize !== 'none'
  const groupStyles = [...new Map(histogram.bins.map((bin) => [
    bin.group_index ?? 0,
    { label: bin.group_label ?? String(bin.group ?? ''), color: bin.color ?? '#2563eb' },
  ])).values()]
  const displayValues = normalizeGroupedValues(
    histogram.bins.map((bin) => ({ bucketIndex: bin.index, value: bin.count })),
    isNormalized ? overrides.groupNormalize : 'none',
  )
  const starts = stackStarts(displayValues, histogram.bins.map((bin) => bin.index))
  const topSegments = topStackSegmentIndexes(histogram.bins.map((bin, index) => ({
    bucketIndex: bin.index,
    groupIndex: bin.group_index ?? 0,
    value: displayValues[index] ?? bin.count,
  })))
  const values = histogram.bins.map((bin, index) => {
    const width = (bin.end - bin.start) * barWidthRatio
    const center = (bin.start + bin.end) / 2
    const displayCount = displayValues[index] ?? bin.count
    const stackStart = isStacked ? starts[index] ?? 0 : 0
    const label = bin.label ?? `${formatHistogramBound(bin.start)} to ${formatHistogramBound(bin.end)}`
    if (hasGroups && !isStacked) {
      const bounds = groupedHistogramBounds(bin.start, bin.end, overrides.barWidth, overrides.groupSpacing, bin.group_index ?? 0, groupCount)
      return { ...bin, display_count: displayCount, stack_start: 0, stack_end: displayCount, is_stack_top: topSegments.has(index), is_selected: histogramSegmentIsSelected(bin.group ?? null, bin.index, selectedGroups, selectedBarIndexes), display_label: `${displayCount.toFixed(1)}%`, label, adjusted_start: bounds.start, adjusted_end: bounds.end }
    }
    return { ...bin, display_count: displayCount, stack_start: stackStart, stack_end: stackStart + displayCount, is_stack_top: topSegments.has(index), is_selected: histogramSegmentIsSelected(bin.group ?? null, bin.index, selectedGroups, selectedBarIndexes), display_label: `${displayCount.toFixed(1)}%`, label, adjusted_start: center - width / 2, adjusted_end: center + width / 2 }
  })
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v6.json',
    autosize: { type: 'fit', contains: 'padding', resize: true },
    width: 'container',
    height: chartHeight,
    background: 'transparent',
    padding: buildChartPadding(overrides.title),
    title: buildChartTitle(overrides.title, defaultOverrides.title.text, chartScale),
    config: buildVegaLiteChartConfig(theme, chartScale),
    data: {
      values,
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
      ...(hasGroups ? [{
        name: 'legend_group',
        select: { type: 'point' as const, fields: ['group_label'], toggle: 'true', clear: false },
        bind: 'legend' as const,
      }] : []),
    ],
    mark: {
      type: 'rect',
      cornerRadiusTopLeft: isStacked ? { signal: 'datum.is_stack_top ? 3 : 0' } : 3,
      cornerRadiusTopRight: isStacked ? { signal: 'datum.is_stack_top ? 3 : 0' } : 3,
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
            ...(xScaleType === 'log' ? {} : { zero: false }),
          } : {}),
          type: xScaleType,
        },
      },
      x2: { field: 'adjusted_end' },
      y: {
        field: isStacked ? 'stack_end' : 'display_count',
        type: 'quantitative',
        axis: {
          ...buildAxisSpec(overrides.yAxis, isNormalized ? 'Percentage' : defaultOverrides.yAxis.label, chartScale),
          ...(isNormalized ? { title: 'Percentage', labelExpr: "format(datum.value, '.0f') + '%'" } : {}),
        },
        scale: {
          type: buildScaleType(overrides.yAxis.scale),
        },
        stack: false,
      },
      ...(isStacked ? { y2: { field: 'stack_start' } } : overrides.yAxis.scale === 'log' ? {} : { y2: { datum: 0 } }),
      ...(hasGroups ? { color: {
        field: 'group_label',
        type: 'nominal' as const,
        scale: {
          domain: groupStyles.map((group) => group.label),
          range: groupStyles.map((group) => group.color),
        },
        legend: { title: histogram.group_column },
      } } : { color: {
        field: 'color',
        type: 'nominal',
        scale: null,
        legend: null,
        condition: {
          test: `data("selected_bars_store").length > 0 || data("brush_selection_store").length > 0 ? !(datum.is_selected || (isArray(${HISTOGRAM_BRUSH_SIGNAL_NAME}) && datum.end > ${HISTOGRAM_BRUSH_SIGNAL_NAME}[0] && datum.start < ${HISTOGRAM_BRUSH_SIGNAL_NAME}[1])) : false`,
          value: '#94a3b8',
        },
      } }),
      opacity: hasGroups ? {
        condition: [
          {
            test: `data("brush_selection_store").length > 0 && isArray(${HISTOGRAM_BRUSH_SIGNAL_NAME}) && datum.end > ${HISTOGRAM_BRUSH_SIGNAL_NAME}[0] && datum.start < ${HISTOGRAM_BRUSH_SIGNAL_NAME}[1]`,
            value: 1,
          },
          { test: 'data("brush_selection_store").length === 0 && datum.is_selected', value: 1 },
        ],
        value: 0.3,
      } : {
        condition: [
          {
            test: 'data("selected_bars_store").length === 0 && data("brush_selection_store").length === 0',
            value: 1,
          },
          { param: 'selected_bars', empty: false, value: 1 },
          {
            test: `isArray(${HISTOGRAM_BRUSH_SIGNAL_NAME}) && datum.end > ${HISTOGRAM_BRUSH_SIGNAL_NAME}[0] && datum.start < ${HISTOGRAM_BRUSH_SIGNAL_NAME}[1]`,
            value: 1,
          },
        ],
        value: 0.3,
      },
      tooltip: [
        { field: 'label', type: 'nominal' as const, title: 'Range' },
        ...(hasGroups ? [{ field: 'group_label', type: 'nominal' as const, title: 'Group' }] : []),
        { field: 'count', type: 'quantitative' as const, title: 'Rows' },
        ...(isNormalized ? [{ field: 'display_label', type: 'nominal' as const, title: 'Percentage' }] : []),
      ],
    },
  }
}

function buildCenteredHistogramAxisTicks(
  histogram: PreparedHistogramPayload,
  maxTickLabels: number,
): { values: number[]; labelExpr: string } | null {
  const timeGranularity = histogram.time_granularity
  if (!timeGranularity || !histogram.bins.length) {
    return null
  }
  const selectedBins = selectHistogramAxisBins(histogram.bins, maxTickLabels)
  const includeYear = histogramSpansMultipleYears(histogram)
  const labelsByCenter = Object.fromEntries(
    selectedBins.map((bin) => [
      String((bin.start + bin.end) / 2),
      formatHistogramAxisTickLabel(bin.start, timeGranularity, includeYear),
    ]),
  )
  return {
    values: selectedBins.map((bin) => (bin.start + bin.end) / 2),
    labelExpr: `${JSON.stringify(labelsByCenter)}[toString(datum.value)] || ''`,
  }
}

function selectHistogramAxisBins(bins: PreparedHistogramPayload['bins'], maxTicks: number) {
  const uniqueBins = [...new Map(bins.map((bin) => [bin.index, bin])).values()]
  if (uniqueBins.length <= maxTicks) {
    return uniqueBins
  }
  const step = Math.ceil(uniqueBins.length / Math.max(maxTicks, 1))
  return uniqueBins.filter((_, index) => index % step === 0)
}

function formatHistogramAxisTickLabel(
  start: number,
  granularity: NonNullable<PreparedHistogramPayload['time_granularity']>,
  includeYear: boolean,
): string {
  const timeGranularity = granularity
  const value = new Date(start)
  if (timeGranularity === 'year') {
    return String(value.getUTCFullYear())
  }
  if (timeGranularity === 'month') {
    return `${MONTH_LABELS[value.getUTCMonth()]} ${value.getUTCFullYear()}`
  }
  if (timeGranularity === 'hour') {
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

function isHistogramLegendItem(item: unknown): boolean {
  if (!item || typeof item !== 'object') {
    return false
  }
  const role = (item as { mark?: { role?: unknown } }).mark?.role
  return typeof role === 'string' && role.includes('legend')
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
  return histogram.bins.find((bin) => bin.start === clickedRange.lower && bin.end === clickedRange.upper)?.index ?? null
}

function parseHistogramClickedGroup(item: unknown): string | number | boolean | null {
  if (!item || typeof item !== 'object') return null
  const datum = 'datum' in item ? (item as { datum?: unknown }).datum : null
  if (!datum || typeof datum !== 'object') return null
  const group = (datum as Record<string, unknown>).group
  return typeof group === 'string' || typeof group === 'number' || typeof group === 'boolean' ? group : null
}

function histogramSelectionRangesFromIndexes(
  histogram: PreparedHistogramPayload,
  selectedBarIndexes: number[],
): HistogramSelectionRange[] {
  const uniqueBins = [...new Map(histogram.bins.map((bin) => [bin.index, bin])).values()]
  const indexes = [...new Set(selectedBarIndexes)]
    .filter((index) => Number.isInteger(index) && index >= 0 && index < uniqueBins.length)
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
      const startBin = uniqueBins[startIndex]
      const endBin = uniqueBins[previousIndex]
      if (startBin && endBin) {
        ranges.push({ lower: startBin.start, upper: endBin.end })
      }
      startIndex = currentIndex
    }
    previousIndex = currentIndex
  }
  const finalStartBin = uniqueBins[startIndex]
  const finalEndBin = uniqueBins[previousIndex]
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
  return [...new Map(histogram.bins.map((bin) => [bin.index, bin])).values()]
    .filter((bin) => bin.end > brushRange.lower && bin.start < brushRange.upper)
    .map((bin) => bin.index)
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
