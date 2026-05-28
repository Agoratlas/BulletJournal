import { useEffect, useMemo, useState } from 'react'

import type { AssetChartTheme, ChartAxisOverrides, ChartTitleOverrides, HistogramSelectionRange, ScatterPlotSelectionBounds } from './types'
import { optionalIntegerFromInput, optionalNonNegativeNumberFromInput, optionalPositiveNumberFromInput } from './modifiers'

export function buildAxisSpec(overrides: ChartAxisOverrides, defaultLabel: string) {
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

export function buildScaleType(scale: ChartAxisOverrides['scale']): 'linear' | 'log' {
  return scale === 'log' ? 'log' : 'linear'
}

export function buildChartTitle(overrides: ChartTitleOverrides, defaultText: string) {
  if (overrides.hideTitle) {
    return undefined
  }
  return {
    text: overrides.text.trim() || defaultText,
    fontSize: optionalPositiveNumberFromInput(overrides.size),
    orient: overrides.position,
  }
}

export function buildChartPadding(title: ChartTitleOverrides) {
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

export function resolvedAxisLabel(value: string, defaultLabel: string): string {
  if (!value.trim() || value === 'X axis' || value === 'Y axis' || value === 'Rows') {
    return defaultLabel
  }
  return value
}

export function parseSelectionRangeSignal(value: unknown): HistogramSelectionRange | null {
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

export function combineScatterPlotSelection(
  xRange: HistogramSelectionRange | null,
  yRange: HistogramSelectionRange | null,
): ScatterPlotSelectionBounds | null {
  if (!xRange || !yRange) {
    return null
  }
  return { x: xRange, y: yRange }
}

export function eventHasShiftKey(event: Event): boolean {
  return 'shiftKey' in event && Boolean((event as MouseEvent).shiftKey)
}

export function rangesEqual(left: HistogramSelectionRange | null, right: HistogramSelectionRange | null): boolean {
  if (!left && !right) {
    return true
  }
  if (!left || !right) {
    return false
  }
  return left.lower === right.lower && left.upper === right.upper
}

export function scatterPlotSelectionsEqual(left: ScatterPlotSelectionBounds | null, right: ScatterPlotSelectionBounds | null): boolean {
  if (!left && !right) {
    return true
  }
  if (!left || !right) {
    return false
  }
  return rangesEqual(left.x, right.x) && rangesEqual(left.y, right.y)
}

export function useAssetChartTheme(): AssetChartTheme {
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

export function formatHistogramBound(value: number): string {
  if (Number.isInteger(value)) {
    return String(value)
  }
  return value.toFixed(2).replace(/\.00$/, '').replace(/(\.[1-9])0$/, '$1')
}

export function formatPieChartShare(value: number): string {
  const percentage = value * 100
  if (percentage >= 10 || Number.isInteger(percentage)) {
    return `${Math.round(percentage)}%`
  }
  return `${percentage.toFixed(1).replace(/\.0$/, '')}%`
}

export function opaqueColor(color: string): string {
  const rgbMatch = color.trim().match(/^rgba\(([^)]+)\)$/i)
  if (!rgbMatch) {
    return color
  }
  const [red, green, blue] = rgbMatch[1].split(',').map((part) => part.trim())
  return `rgb(${red}, ${green}, ${blue})`
}
