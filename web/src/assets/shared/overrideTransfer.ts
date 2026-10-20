import { MAX_DATAVIZ_CHART_HEIGHT, MIN_DATAVIZ_CHART_HEIGHT } from './types.ts'

type ModifierSchema = Array<Record<string, unknown>>

export function settingsOverrideTransfer(overrides: Record<string, unknown>, panelHeight: number, isPanelResized: boolean): Record<string, unknown> {
  return isPanelResized ? { ...overrides, panel_height: panelHeight } : { ...overrides }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function invalid(path: string, requirement: string): never {
  throw new Error(`${path} ${requirement}.`)
}

function validateShape(value: unknown, fallback: unknown, path: string): void {
  if (isRecord(fallback)) {
    if (!isRecord(value)) invalid(path, 'must be an object')
    for (const [key, child] of Object.entries(value)) {
      if (!Object.prototype.hasOwnProperty.call(fallback, key)) invalid(`${path}.${key}`, 'is not supported by this asset')
      validateShape(child, fallback[key], `${path}.${key}`)
    }
  } else if (fallback === null || typeof fallback === 'number') {
    if ((value === null && fallback !== null) || (value !== null && (typeof value !== 'number' || !Number.isFinite(value)))) invalid(path, fallback === null ? 'must be a number or null' : 'must be a number')
  } else if (typeof value !== typeof fallback || Array.isArray(value) !== Array.isArray(fallback)) {
    invalid(path, `must be ${Array.isArray(fallback) ? 'an array' : typeof fallback}`)
  }
  if (path.endsWith('.scale') && !['lin', 'log'].includes(String(value))) invalid(path, 'must be "lin" or "log"')
  if (path.endsWith('.tick_angle') && ![0, 45, 90].includes(Number(value))) invalid(path, 'must be 0, 45, or 90')
  if (path.endsWith('.position') && !['top', 'bottom'].includes(String(value))) invalid(path, 'must be "top" or "bottom"')
  if (path.endsWith('.tick_count') && value !== null && (!Number.isInteger(value) || Number(value) < 1)) invalid(path, 'must be a positive integer or null')
  if (typeof value === 'number' && /\.(label_size|tick_size|size)$/.test(path) && value <= 0) invalid(path, 'must be positive')
}

export function parseSettingsOverrides(text: string, schema: ModifierSchema, supportedKeys: string[], panelHeightMinimum: number | null = null): {
  modifier_overrides: Record<string, unknown>
  panel_height: number | null
} {
  const value: unknown = JSON.parse(text)
  if (!isRecord(value)) invalid('Overrides', 'must be a JSON object')
  const { panel_height: panelHeight = null, ...modifierOverrides } = value
  if (Object.prototype.hasOwnProperty.call(value, 'panel_height')) {
    if (panelHeightMinimum === null) invalid('panel_height', 'is not supported by this asset overrides panel')
    const minimum = Math.max(MIN_DATAVIZ_CHART_HEIGHT, panelHeightMinimum)
    if (panelHeight !== null && (typeof panelHeight !== 'number' || !Number.isInteger(panelHeight) || panelHeight < minimum || panelHeight > MAX_DATAVIZ_CHART_HEIGHT)) {
      invalid('panel_height', `must be an integer between ${minimum} and ${MAX_DATAVIZ_CHART_HEIGHT}, or null`)
    }
  }
  for (const [key, modifier] of Object.entries(modifierOverrides)) {
    const entry = schema.find((entry) => entry.id === key)
    if (!entry || !supportedKeys.includes(key)) invalid(key, 'is not supported by this asset overrides panel')
    if (key === 'group_normalize') {
      if (!['none', 'max', 'sum'].includes(String(modifier)) && typeof modifier !== 'boolean') invalid(key, 'must be "none", "max", "sum", or a boolean')
    } else if (entry.kind === 'enum') {
      const options = Array.isArray(entry.options) ? entry.options.map((option) => isRecord(option) ? option.value : option) : []
      if (!options.includes(modifier)) invalid(key, 'must be one of the supported options')
    } else {
      validateShape(modifier, entry.default_value, key)
    }
    if (entry.kind === 'int' && !Number.isInteger(modifier)) invalid(key, 'must be an integer')
    if (typeof modifier === 'number') {
      if (typeof entry.min_value === 'number' && modifier < entry.min_value) invalid(key, `must be at least ${entry.min_value}`)
      if (typeof entry.max_value === 'number' && modifier > entry.max_value) invalid(key, `must be at most ${entry.max_value}`)
    }
    if (key === 'group_mode' && !['grouped', 'stacked'].includes(String(modifier))) invalid(key, 'must be "grouped" or "stacked"')
  }
  return { modifier_overrides: modifierOverrides, panel_height: panelHeight as number | null }
}
