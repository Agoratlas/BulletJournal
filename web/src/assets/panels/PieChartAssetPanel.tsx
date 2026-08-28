import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import embed, { type Result as VegaEmbedResult, type VisualizationSpec } from 'vega-embed'

import { prepareAsset } from '../../lib/api'
import type { AssetFilter, AssetHighlight, AssetSort, PreparedPieChartPayload } from '../../lib/types'
import {
  buildChartPadding,
  buildChartTitle,
  buildVegaLiteChartConfig,
  eventHasShiftKey,
  formatPieChartShare,
  opaqueColor,
  useAssetChartTheme,
} from '../shared/chart'
import {
  AssetPanelFrame,
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
  clampNumberToRange,
  defaultPieChartOverrides,
  filterKindsForDataType,
  initialTableStateFromModifiers,
  modifierFieldLabelClassName,
  modifierTitle,
  modifierColumnsFromSchema,
  nextSortForColumn,
  replaceHighlightsForColumn,
  normalizePanelHeight,
  optionalNonNegativeNumberFromInput,
  optionalNumberFromInput,
  optionalPositiveNumberFromInput,
  pieChartOverridesFromModifiers,
  removeFilter,
  serializePieChartModifierValues,
  stableValueKey,
  tableStateKey,
  upsertFilter,
  valuesEqual,
} from '../shared/modifiers'
import type { DatavizAssetPanelProps, PieChartChartOverrides, PieChartDisplaySlice, PieChartSelectionValue } from '../shared/types'
import { DEFAULT_DATAVIZ_TABLE_PAGE_SIZE, DEFAULT_PIE_CHART_HEIGHT } from '../shared/types'

export function PieChartAssetPanel({
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
  const [highlights, setHighlights] = useState<AssetHighlight[]>(initialTableState.highlights ?? [])
  const [chartOverrides, setChartOverrides] = useState<PieChartChartOverrides>(initialChartOverrides)
  const [selectedCategories, setSelectedCategories] = useState<PieChartSelectionValue[]>([])
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
      ...serializePieChartModifierValues(chartOverrides),
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
      transient_modifiers: selectedCategories.length ? {
        selected_categories: selectedCategories,
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
  const pieChart = mainPayload?.kind === 'pie_chart' ? mainPayload : null
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
  const totalRows = pieChart?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const displayedRows = table?.rows_total ?? totalRows
  const baseRows = typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : totalRows
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const pageCount = Math.max(1, Math.ceil(displayedRows / Math.max(resolvedPage.size, 1)))
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
            onValidChange={(nextValue) => setChartOverrides((current) => ({ ...current, labelSize: nextValue }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({ ...current, labelSize: nextValue }))}
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
              onChange={(event) => setChartOverrides((current) => ({ ...current, mergeThreshold: event.target.value }))}
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
            onValidChange={(nextValue) => setChartOverrides((current) => ({ ...current, borderThickness: nextValue }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({ ...current, borderThickness: nextValue }))}
          />
        </label>

        <label className="asset-dataviz-field">
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.mergedCategoryLabel, chartOverrideDefaults.mergedCategoryLabel))}>{modifierTitle(asset.modifier_schema, 'merged_category_label', 'Merged category label')}</span>
          <DeferredModifierInput
            displayValue={chartOverrides.mergedCategoryLabel}
            isValid={(value) => value.trim() !== ''}
            fallbackValue={chartOverrideDefaults.mergedCategoryLabel}
            onValidChange={(nextValue) => setChartOverrides((current) => ({ ...current, mergedCategoryLabel: nextValue }))}
            onCommit={(nextValue) => setChartOverrides((current) => ({ ...current, mergedCategoryLabel: nextValue }))}
          />
        </label>

        <label className="asset-dataviz-checkbox-field">
          <input
            type="checkbox"
            checked={chartOverrides.showMergedCategory}
            onChange={(event) => setChartOverrides((current) => ({ ...current, showMergedCategory: event.target.checked }))}
          />
          <span className={modifierFieldLabelClassName(!valuesEqual(chartOverrides.showMergedCategory, chartOverrideDefaults.showMergedCategory))}>{modifierTitle(asset.modifier_schema, 'show_merged_category', 'Merged category visibility')}</span>
        </label>

        <label className="asset-dataviz-checkbox-field">
          <input
            type="checkbox"
            checked={chartOverrides.showPercentages}
            onChange={(event) => setChartOverrides((current) => ({ ...current, showPercentages: event.target.checked }))}
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
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} settingsTitle="Modifier overrides" settingsBody={settingsBody} settingsActive={hasSettingsOverrides} sectionId={sectionId} frameVariant={frameVariant} showExportActions={viewerMode === 'dashboard'} isPanelResized={isPanelResized}>
      <div className="asset-dataframe-panel asset-pie-chart-panel">
        {overrideIncompatible ? <OverrideIncompatibleNotice onReset={onPersistedStateChange ? handleResetOverrides : undefined} /> : null}
        <PrepareErrorsNotice errors={prepareErrors} />
        <ResizableDatavizContent height={resolvedPanelHeight} onHeightChange={onPanelHeightChange} isResized={isPanelResized} minHeight={minPanelHeight}>
          {(chartHeight) => (
            <>
              {prepareQuery.isLoading && !pieChart ? <LoadingPlaceholder message="Preparing pie chart view..." /> : null}
              {prepareQuery.isError ? (
                <ErrorPlaceholder message={prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the pie chart view.'} />
              ) : null}
              {pieChart ? (
                <PieChartChart
                  pieChart={pieChart}
                  chartHeight={chartHeight}
                  overrides={chartOverrides}
                  defaultOverrides={chartOverrideDefaults}
                  chartScale={chartScale}
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

function PieChartChart({
  pieChart,
  chartHeight,
  overrides,
  defaultOverrides,
  chartScale,
  selectedCategories,
  onSelectionChange,
}: {
  pieChart: PreparedPieChartPayload
  chartHeight: number
  overrides: PieChartChartOverrides
  defaultOverrides: PieChartChartOverrides
  chartScale: number
  selectedCategories: PieChartSelectionValue[]
  onSelectionChange: (categories: PieChartSelectionValue[]) => void
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [chartError, setChartError] = useState<string | null>(null)
  const viewRef = useRef<VegaEmbedResult | null>(null)
  const initialChartHeightRef = useRef(chartHeight)
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
      initialChartHeightRef.current,
      overrides,
      defaultOverrides,
      chartScale,
    ),
    [chartScale, chartTheme, defaultOverrides, displaySlices, overrides, selectedCategories],
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

  if (!displaySlices.length) {
    return <LoadingPlaceholder message="No non-null rows match the current pie chart filters." />
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

function buildPieChartVegaLiteSpec(
  displaySlices: PieChartDisplaySlice[],
  selectedCategories: PieChartSelectionValue[],
  theme: ReturnType<typeof useAssetChartTheme>,
  chartHeight: number,
  overrides: PieChartChartOverrides,
  defaultOverrides: PieChartChartOverrides,
  chartScale: number,
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
    autosize: { type: 'fit', contains: 'padding', resize: true },
    width: 'container',
    height: chartHeight,
    background: 'transparent',
    padding: buildChartPadding(overrides.title),
    title: buildChartTitle(overrides.title, defaultOverrides.title.text, chartScale),
    config: buildVegaLiteChartConfig(theme, chartScale),
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
    return baseSlices
  }
  const retainedSlices = baseSlices.filter((slice) => slice.share * 100 >= mergeThreshold)
  const mergedSlices = baseSlices.filter((slice) => slice.share * 100 < mergeThreshold)
  if (!mergedSlices.length) {
    return retainedSlices
  }
  if (!overrides.showMergedCategory) {
    return retainedSlices
  }
  return [
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
  ]
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
