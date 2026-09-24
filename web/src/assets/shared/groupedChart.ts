export type GroupNormalization = 'none' | 'max' | 'sum'

export function resolveGroupMode(value: unknown): 'grouped' | 'stacked' {
  return value === 'stacked' ? 'stacked' : 'grouped'
}

export function resolveGroupNormalization(value: unknown): GroupNormalization {
  if (value === true || value === 'sum') return 'sum'
  if (value === 'max') return 'max'
  return 'none'
}

export function normalizeGroupedValues(
  entries: Array<{ bucketIndex: number; value: number }>,
  mode: GroupNormalization,
): number[] {
  if (mode === 'none') return entries.map((entry) => entry.value)
  const denominators = new Map<number, number>()
  for (const entry of entries) {
    const previous = denominators.get(entry.bucketIndex) ?? 0
    denominators.set(
      entry.bucketIndex,
      mode === 'max' ? Math.max(previous, Math.abs(entry.value)) : previous + entry.value,
    )
  }
  return entries.map(({ bucketIndex, value }) => {
    const denominator = denominators.get(bucketIndex) ?? 0
    return denominator === 0 ? 0 : value / denominator * 100
  })
}

export function topStackSegmentIndexes(
  entries: Array<{ bucketIndex: number; groupIndex: number; value: number }>,
): Set<number> {
  const topByBucket = new Map<number, { index: number; groupIndex: number }>()
  entries.forEach(({ bucketIndex, groupIndex, value }, index) => {
    if (value <= 0) return
    const top = topByBucket.get(bucketIndex)
    if (!top || groupIndex > top.groupIndex) {
      topByBucket.set(bucketIndex, { index, groupIndex })
    }
  })
  return new Set([...topByBucket.values()].map(({ index }) => index))
}

export function groupedHistogramBounds(
  start: number,
  end: number,
  barWidth: number,
  groupSpacing: number,
  groupIndex: number,
  groupCount: number,
): { start: number; end: number } {
  const center = (start + end) / 2
  const width = (end - start) * barWidth / 100 * (1 - groupSpacing / 100)
  const groupWidth = width / groupCount
  const left = center - width / 2 + groupIndex * groupWidth
  return { start: left, end: left + groupWidth }
}

export function barChartCategoryPadding(
  hasGroup: boolean,
  isStacked: boolean,
  barWidth: number,
  groupSpacing: number,
): number {
  return hasGroup && !isStacked ? groupSpacing / 100 : Math.max(0.02, 1 - barWidth / 100)
}

export function stackStarts(values: number[], binIndexes: number[]): number[] {
  const positiveTotals = new Map<number, number>()
  const negativeTotals = new Map<number, number>()
  return values.map((value, index) => {
    const binIndex = binIndexes[index]
    const totals = value < 0 ? negativeTotals : positiveTotals
    const start = totals.get(binIndex) ?? 0
    totals.set(binIndex, start + value)
    return start
  })
}
