import type { AssetFilter, AssetFilterKind, AssetSort } from '../../lib/types'
import type {
  BarChartChartOverrides,
  BarChartGroupMode,
  ChartAxisOverrides,
  ChartTitleOverrides,
  DatavizAxisScale,
  HistogramChartOverrides,
  HistogramState,
  ModifierColumn,
  PieChartChartOverrides,
  ScatterPlotChartOverrides,
  TableState,
  TimeHistogramGranularity,
} from './types'
import {
  DEFAULT_DATAVIZ_TABLE_PAGE_SIZE,
  DEFAULT_TABLE_PAGE_SIZE,
  MAX_DATAVIZ_CHART_HEIGHT,
  MIN_DATAVIZ_CHART_HEIGHT,
  PAGE_SIZE_OPTIONS,
} from './types'

export function initialTableStateFromModifiers(
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

export function initialHistogramStateFromModifiers(
  defaultModifiers: Record<string, unknown>,
  modifierOverrides: Record<string, unknown>,
): HistogramState {
  const tableState = initialTableStateFromModifiers(defaultModifiers, modifierOverrides, DEFAULT_DATAVIZ_TABLE_PAGE_SIZE)
  const binCount = binCountFromValue(mergedModifierValue(defaultModifiers.bin_count, modifierOverrides.bin_count))
  const granularity = granularityFromValue(mergedModifierValue(defaultModifiers.granularity, modifierOverrides.granularity))
  return {
    ...tableState,
    binCount,
    granularity,
  }
}

export function emptyChartAxisOverrides(): ChartAxisOverrides {
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

export function emptyChartTitleOverrides(): ChartTitleOverrides {
  return {
    size: '',
    text: '',
    hideTitle: true,
    position: 'top',
  }
}

export function defaultBarChartChartOverrides(
  defaultModifiers: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): BarChartChartOverrides {
  const histogram = defaultHistogramChartOverrides(defaultModifiers, modifierSchema)
  return {
    ...histogram,
    groupMode: modifierDefaultValue(defaultModifiers, modifierSchema, 'group_mode') === 'stacked' ? 'stacked' : 'grouped',
    groupNormalize: Boolean(modifierDefaultValue(defaultModifiers, modifierSchema, 'group_normalize')),
    groupSpacing: clampPercentage(modifierDefaultValue(defaultModifiers, modifierSchema, 'group_spacing'), 10),
  }
}

export function barChartChartOverridesFromModifiers(
  defaultModifiers: Record<string, unknown>,
  modifierOverrides: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): BarChartChartOverrides {
  const defaults = defaultBarChartChartOverrides(defaultModifiers, modifierSchema)
  const histogram = histogramChartOverridesFromModifiers(defaultModifiers, modifierOverrides, modifierSchema)
  return {
    ...histogram,
    groupMode: mergedModifierValue(defaultModifiers.group_mode, modifierOverrides.group_mode) === 'stacked' ? 'stacked' : defaults.groupMode,
    groupNormalize: typeof mergedModifierValue(defaultModifiers.group_normalize, modifierOverrides.group_normalize) === 'boolean'
      ? mergedModifierValue(defaultModifiers.group_normalize, modifierOverrides.group_normalize) as boolean
      : defaults.groupNormalize,
    groupSpacing: clampPercentage(mergedModifierValue(defaultModifiers.group_spacing, modifierOverrides.group_spacing), defaults.groupSpacing),
  }
}

export function serializeBarChartModifierValues(overrides: BarChartChartOverrides): Record<string, unknown> {
  return {
    ...serializeHistogramChartModifierValues(overrides),
    group_mode: overrides.groupMode,
    group_normalize: overrides.groupNormalize,
    group_spacing: overrides.groupSpacing,
  }
}

export function defaultHistogramChartOverrides(
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

export function defaultScatterPlotChartOverrides(
  defaultModifiers: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
): ScatterPlotChartOverrides {
  const defaultSizeScalingValue = modifierDefaultValue(defaultModifiers, modifierSchema, 'size_scaling')
  return {
    xAxis: chartAxisOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'x_axis'), emptyChartAxisOverrides()),
    yAxis: chartAxisOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'y_axis'), emptyChartAxisOverrides()),
    title: chartTitleOverridesFromValue(modifierDefaultValue(defaultModifiers, modifierSchema, 'title'), emptyChartTitleOverrides()),
    minPointSize: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'min_point_size'), ''),
    maxPointSize: numericInputString(modifierDefaultValue(defaultModifiers, modifierSchema, 'max_point_size'), ''),
    sizeScaling: clampNumberToRange(defaultSizeScalingValue, 1, 0.1, 3),
    showLegend: typeof modifierDefaultValue(defaultModifiers, modifierSchema, 'show_legend') === 'boolean'
      ? modifierDefaultValue(defaultModifiers, modifierSchema, 'show_legend') as boolean
      : true,
    shapeStyle: modifierDefaultValue(defaultModifiers, modifierSchema, 'shape_style') === 'filled' ? 'filled' : 'outline',
  }
}

export function defaultPieChartOverrides(
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

export function histogramChartOverridesFromModifiers(
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

export function scatterPlotChartOverridesFromModifiers(
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
    sizeScaling: clampNumberToRange(mergedModifierValue(defaultModifiers.size_scaling, modifierOverrides.size_scaling), defaults.sizeScaling, 0.1, 3),
    showLegend: typeof mergedModifierValue(defaultModifiers.show_legend, modifierOverrides.show_legend) === 'boolean'
      ? mergedModifierValue(defaultModifiers.show_legend, modifierOverrides.show_legend) as boolean
      : defaults.showLegend,
    shapeStyle: mergedModifierValue(defaultModifiers.shape_style, modifierOverrides.shape_style) === 'filled'
      ? 'filled'
      : defaults.shapeStyle,
  }
}

export function pieChartOverridesFromModifiers(
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

export function chartAxisOverridesFromValue(value: unknown, defaults: ChartAxisOverrides): ChartAxisOverrides {
  if (!value || typeof value !== 'object') {
    return defaults
  }
  const record = value as Record<string, unknown>
  return {
    labelSize: numericInputString(record.label_size ?? record.labelSize, defaults.labelSize),
    label: typeof record.label === 'string' ? record.label : defaults.label,
    hideLabel: typeof (record.hide_label ?? record.hideLabel) === 'boolean' ? Boolean(record.hide_label ?? record.hideLabel) : defaults.hideLabel,
    tickCount: integerInputString(record.tick_count ?? record.tickCount, defaults.tickCount),
    tickSize: numericInputString(record.tick_size ?? record.tickSize, defaults.tickSize),
    showGridLines: typeof (record.show_grid_lines ?? record.showGridLines) === 'boolean'
      ? Boolean(record.show_grid_lines ?? record.showGridLines)
      : defaults.showGridLines,
    scale: record.scale === 'log' ? 'log' : defaults.scale,
  }
}

export function chartTitleOverridesFromValue(value: unknown, defaults: ChartTitleOverrides): ChartTitleOverrides {
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

export function serializeHistogramChartModifierValues(overrides: HistogramChartOverrides): Record<string, unknown> {
  return {
    bar_width: overrides.barWidth,
    border_thickness: optionalNumberFromInput(overrides.borderThickness),
    x_axis: serializeChartAxisModifierValue(overrides.xAxis),
    y_axis: serializeChartAxisModifierValue(overrides.yAxis),
    title: serializeChartTitleModifierValue(overrides.title),
  }
}

export function serializeScatterPlotChartModifierValues(overrides: ScatterPlotChartOverrides): Record<string, unknown> {
  return {
    min_point_size: optionalNumberFromInput(overrides.minPointSize),
    max_point_size: optionalNumberFromInput(overrides.maxPointSize),
    size_scaling: overrides.sizeScaling,
    show_legend: overrides.showLegend,
    shape_style: overrides.shapeStyle,
    x_axis: serializeChartAxisModifierValue(overrides.xAxis),
    y_axis: serializeChartAxisModifierValue(overrides.yAxis),
    title: serializeChartTitleModifierValue(overrides.title),
  }
}

export function serializePieChartModifierValues(overrides: PieChartChartOverrides): Record<string, unknown> {
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

export function serializeChartAxisModifierValue(overrides: ChartAxisOverrides): Record<string, unknown> {
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

export function serializeChartTitleModifierValue(overrides: ChartTitleOverrides): Record<string, unknown> {
  return {
    size: optionalNumberFromInput(overrides.size),
    text: overrides.text,
    hide_title: overrides.hideTitle,
    position: overrides.position,
  }
}

export function pageFromValue(value: unknown, defaultPageSize: number): { index: number; size: number } | null {
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

export function normalizePanelHeight(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return null
  }
  return clampPanelHeight(value)
}

export function clampPanelHeight(value: number): number {
  return Math.min(MAX_DATAVIZ_CHART_HEIGHT, Math.max(MIN_DATAVIZ_CHART_HEIGHT, Math.round(value)))
}

export function binCountFromValue(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > 100) {
    return null
  }
  return value
}

export function granularityFromValue(value: unknown): TimeHistogramGranularity | null {
  if (value === 'auto' || value === 'year' || value === 'month' || value === 'week' || value === 'day' || value === 'hour') {
    return value
  }
  return null
}

export function clampPercentage(value: unknown, fallback: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return fallback
  }
  return Math.min(100, Math.max(0, Math.round(value)))
}

export function clampNumberToRange(
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

export function integerInputString(value: unknown, fallback = ''): string {
  if (typeof value === 'number' && Number.isInteger(value)) {
    return String(value)
  }
  if (typeof value === 'string' && /^-?\d+$/.test(value.trim())) {
    return value
  }
  return fallback
}

export function numericInputString(value: unknown, fallback = ''): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value)
  }
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) {
    return value
  }
  return fallback
}

export function optionalNumberFromInput(value: string): number | undefined {
  if (value.trim() === '') {
    return undefined
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : undefined
}

export function optionalPositiveNumberFromInput(value: string): number | undefined {
  const parsed = optionalNumberFromInput(value)
  return parsed !== undefined && parsed > 0 ? parsed : undefined
}

export function optionalNonNegativeNumberFromInput(value: string): number | undefined {
  const parsed = optionalNumberFromInput(value)
  return parsed !== undefined && parsed >= 0 ? parsed : undefined
}

export function optionalIntegerFromInput(value: string): number | undefined {
  if (value.trim() === '') {
    return undefined
  }
  const parsed = Number(value)
  return Number.isInteger(parsed) ? parsed : undefined
}

export function sortFromValue(value: unknown): AssetSort | null {
  if (!Array.isArray(value) || !value.length || !value[0] || typeof value[0] !== 'object') {
    return null
  }
  const record = value[0] as Record<string, unknown>
  if ((record.direction !== 'asc' && record.direction !== 'desc') || typeof record.column !== 'string' || !record.column) {
    return null
  }
  return { column: record.column, direction: record.direction }
}

export function filtersFromValue(value: unknown): AssetFilter[] | null {
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

export function modifierColumnsFromSchema(modifierSchema: Array<Record<string, unknown>>): ModifierColumn[] {
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

export function filterKindsForDataType(dataType: string): AssetFilterKind[] {
  const category = dataTypeCategory(dataType)
  if (category === 'numeric' || category === 'date' || category === 'datetime' || category === 'time') {
    return ['range', 'value']
  }
  if (category === 'bool') {
    return ['value']
  }
  return ['value', 'regex']
}

export function dataTypeCategory(dataType: string): 'numeric' | 'date' | 'datetime' | 'time' | 'bool' | 'text' {
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

export function tableStateKey(state: TableState): string {
  return stableValueKey({
    page: state.page,
    sort: state.sort,
    filters: state.filters,
  })
}

export function histogramStateKey(state: HistogramState): string {
  return stableValueKey({
    page: state.page,
    sort: state.sort,
    filters: state.filters,
    binCount: state.binCount,
    granularity: state.granularity,
  })
}

export function stableValueKey(value: unknown): string {
  return JSON.stringify(sortValueForKey(value))
}

export function valuesEqual(left: unknown, right: unknown): boolean {
  return stableValueKey(left) === stableValueKey(right)
}

export function sortValueForKey(value: unknown): unknown {
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

export function mergedModifierValue(defaultValue: unknown, overrideValue: unknown): unknown {
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

export function buildModifierOverridesRecord(
  currentModifiers: Record<string, unknown>,
  defaultModifiers: Record<string, unknown>,
): Record<string, unknown> {
  const nextEntries = Object.entries(currentModifiers)
    .map(([key, value]) => [key, diffModifierValue(value, defaultModifiers[key])])
    .filter((entry): entry is [string, unknown] => entry[1] !== undefined)
  return Object.fromEntries(nextEntries)
}

export function diffModifierValue(currentValue: unknown, defaultValue: unknown): unknown {
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

export function modifierSchemaEntry(modifierSchema: Array<Record<string, unknown>>, id: string): Record<string, unknown> | null {
  return modifierSchema.find((entry) => entry.id === id) ?? null
}

export function modifierDefaultValue(
  defaultModifiers: Record<string, unknown>,
  modifierSchema: Array<Record<string, unknown>>,
  id: string,
): unknown {
  if (id in defaultModifiers) {
    return defaultModifiers[id]
  }
  return modifierSchemaEntry(modifierSchema, id)?.default_value
}

export function modifierTitle(modifierSchema: Array<Record<string, unknown>>, id: string, fallback: string): string {
  const candidate = modifierSchemaEntry(modifierSchema, id)?.title
  return typeof candidate === 'string' && candidate ? candidate : fallback
}

export function modifierFieldLabelClassName(active: boolean): string {
  return active ? 'asset-modifier-label is-overridden' : 'asset-modifier-label'
}

export function nextSortForColumn(current: AssetSort | null, column: string): AssetSort | null {
  if (!current || current.column !== column) {
    return { column, direction: 'asc' }
  }
  if (current.direction === 'asc') {
    return { column, direction: 'desc' }
  }
  return null
}

export function upsertFilter(current: AssetFilter[], filter: AssetFilter): AssetFilter[] {
  return [...current.filter((entry) => entry.column !== filter.column), filter]
}

export function removeFilter(current: AssetFilter[], columnId: string): AssetFilter[] {
  return current.filter((entry) => entry.column !== columnId)
}

export function filterDraftFromColumn(column: ModifierColumn, activeFilter: AssetFilter | null) {
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

export function buildFilterFromInputs({
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

export function coerceDraftValue(value: string, category: ReturnType<typeof dataTypeCategory>): string | number | boolean | null {
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

export function coerceRangeDraftValue(value: string, category: ReturnType<typeof dataTypeCategory>): string | number {
  if (category === 'bool' || category === 'text') {
    throw new Error('Range filters are only available for numeric and date-like columns.')
  }
  const resolved = coerceDraftValue(value, category)
  if (typeof resolved === 'number' || typeof resolved === 'string') {
    return resolved
  }
  throw new Error('Range filters need numeric or date-like bounds.')
}

export function formatFilterSummary(filter: AssetFilter, columns: ModifierColumn[]): string {
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

export function filterKindLabel(kind: AssetFilterKind): string {
  if (kind === 'range') {
    return 'Range'
  }
  if (kind === 'regex') {
    return 'Regex'
  }
  return 'Equals'
}

export function rangeFilterPlaceholder(dataType: string, bound: 'lower' | 'upper'): string {
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

export function valueFilterPlaceholder(dataType: string): string {
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

export type { DatavizAxisScale }
