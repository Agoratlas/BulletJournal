import assert from 'node:assert/strict'
import { test } from 'node:test'

import { barChartCategoryPadding, groupedHistogramBounds, normalizeGroupedValues, resolveGroupMode, resolveGroupNormalization, stackStarts, topStackSegmentIndexes } from './groupedChart.ts'

test('normalizes bars within each bucket by its maximum or total in grouped and stacked views', () => {
  const values = [
    { bucketIndex: 0, value: 2 }, { bucketIndex: 0, value: 4 },
    { bucketIndex: 1, value: 6 }, { bucketIndex: 1, value: 4 },
  ]
  assert.deepEqual(normalizeGroupedValues(values, 'none'), [2, 4, 6, 4])
  assert.deepEqual(normalizeGroupedValues(values, 'max').map(Math.round), [50, 100, 100, 67])
  const bySum = normalizeGroupedValues(values, 'sum')
  assert.deepEqual(bySum.map(Math.round), [33, 67, 60, 40])
  assert.ok(Math.abs(bySum[0] + bySum[1] - 100) < 1e-10)
  assert.ok(Math.abs(bySum[2] + bySum[3] - 100) < 1e-10)
  assert.deepEqual(normalizeGroupedValues([{ bucketIndex: 0, value: 0 }], 'sum'), [0])
})

test('restores grouped overrides over stacked defaults and supports legacy booleans', () => {
  assert.equal(resolveGroupMode('stacked'), 'stacked')
  assert.equal(resolveGroupMode('grouped'), 'grouped')
  assert.equal(resolveGroupNormalization(true), 'sum')
  assert.equal(resolveGroupNormalization(false), 'none')
  assert.equal(resolveGroupNormalization('max'), 'max')
})

test('spacing separates bin clusters without separating bars inside a cluster', () => {
  const first = groupedHistogramBounds(0, 10, 100, 20, 0, 2)
  const second = groupedHistogramBounds(0, 10, 100, 20, 1, 2)
  const nextBin = groupedHistogramBounds(10, 20, 100, 20, 0, 2)
  assert.deepEqual(first, { start: 1, end: 5 })
  assert.equal(second.start, first.end)
  assert.ok(nextBin.start > second.end)
})

test('bar chart spacing belongs to category clusters, not group offsets', () => {
  assert.equal(barChartCategoryPadding(true, false, 100, 20), 0.2)
  assert.equal(barChartCategoryPadding(true, false, 100, 40), 0.4)
  assert.ok(Math.abs(barChartCategoryPadding(true, true, 90, 20) - 0.1) < 1e-10)
  assert.ok(Math.abs(barChartCategoryPadding(false, false, 90, 20) - 0.1) < 1e-10)
})

test('stacked histogram segments start at the previous segment height for each bin', () => {
  assert.deepEqual(stackStarts([2, 3, 4, 5], [0, 0, 1, 1]), [0, 2, 0, 4])
  assert.deepEqual(stackStarts([2, -3, 4, -2], [0, 0, 0, 0]), [0, 0, 2, -3])
})

test('only the highest nonempty segment in each bucket gets rounded corners', () => {
  const top = topStackSegmentIndexes([
    { bucketIndex: 0, groupIndex: 0, value: 2 },
    { bucketIndex: 0, groupIndex: 1, value: 4 },
    { bucketIndex: 0, groupIndex: 2, value: 0 },
    { bucketIndex: 1, groupIndex: 0, value: 3 },
    { bucketIndex: 1, groupIndex: 1, value: 0 },
  ])
  assert.deepEqual([...top], [1, 3])
})
