import assert from 'node:assert/strict'
import { test } from 'node:test'

import { histogramGroupFromLegendLabel, histogramSegmentIsSelected, histogramSelectionFromGroupClick, histogramSelectionFromRange, nextHistogramGroupSelection } from './histogramSelection.ts'

test('a group click selects, toggles off and replaces a selection; shift-click adds and removes groups', () => {
  assert.deepEqual(nextHistogramGroupSelection([], 'English', false), ['English'])
  assert.deepEqual(nextHistogramGroupSelection(['English'], 'English', false), [])
  assert.deepEqual(nextHistogramGroupSelection(['English'], 'Other', false), ['Other'])
  assert.deepEqual(nextHistogramGroupSelection(['English'], 'Other', true), ['English', 'Other'])
  assert.deepEqual(nextHistogramGroupSelection(['English', 'Other'], 'English', true), ['Other'])
})

test('group and bin selection dim segments outside either active selection', () => {
  assert.equal(histogramSegmentIsSelected('English', 0, [], []), true)
  assert.equal(histogramSegmentIsSelected('English', 0, ['English'], []), true)
  assert.equal(histogramSegmentIsSelected('Other', 0, ['English'], []), false)
  assert.equal(histogramSegmentIsSelected('English', 1, ['English'], [0]), false)
  assert.equal(histogramSegmentIsSelected('English', 0, ['English'], [0]), true)
})

test('clicking a grouped bar replaces the brush range; a new range replaces group selection', () => {
  assert.deepEqual(histogramSelectionFromGroupClick([], 'English', false), {
    selectedBarIndexes: [], selectedGroups: ['English'],
  })
  assert.deepEqual(histogramSelectionFromRange([1, 2]), {
    selectedBarIndexes: [1, 2], selectedGroups: [],
  })
})

test('legend labels resolve to the underlying group value used by table filters', () => {
  const bins = [{ group: 2020, group_label: 'Year 2020' }, { group: false, group_label: 'Other' }]
  assert.equal(histogramGroupFromLegendLabel(bins, 'Year 2020'), 2020)
  assert.equal(histogramGroupFromLegendLabel(bins, 'Other'), false)
  assert.equal(histogramGroupFromLegendLabel(bins, 'Unknown'), null)
})
