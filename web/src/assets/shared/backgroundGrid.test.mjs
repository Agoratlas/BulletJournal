import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parse } from 'vega'
import { compile } from 'vega-lite'

import { backgroundCategoryGridLayer } from './backgroundGrid.ts'

test('the categorical grid renders before the bars instead of as a foreground x-axis grid', () => {
  const spec = compile({
    data: { values: [
      { category_label: '2020', category_index: 0, group_label: 'A', value: 2 },
      { category_label: '2020', category_index: 0, group_label: 'B', value: 3 },
    ] },
    layer: [
      backgroundCategoryGridLayer('#888', 0.2),
      {
        mark: 'bar',
        encoding: {
          x: { field: 'category_label', type: 'nominal', sort: { field: 'category_index', op: 'min', order: 'ascending' }, axis: { grid: false } },
          y: { field: 'value', type: 'quantitative' },
          color: { field: 'group_label', type: 'nominal' },
        },
      },
    ],
  }).spec
  assert.deepEqual(spec.marks.map((mark) => mark.type), ['rule', 'rect'])
  assert.equal(spec.axes.some((axis) => axis.scale === 'x' && axis.grid), false)
  assert.equal(spec.axes.some((axis) => axis.scale === 'y' && axis.grid), true)
})

test('a layered grouped chart binds the legend selection once', () => {
  const spec = compile({
    data: { values: [{ category_label: '2020', category_index: 0, group_label: 'Action', value: 2 }] },
    layer: [
      backgroundCategoryGridLayer('#888', 0.2),
      {
        params: [{ name: 'legend_group', select: { type: 'point', fields: ['group_label'], toggle: 'true', clear: false }, bind: 'legend' }],
        mark: 'bar',
        encoding: {
          x: { field: 'category_label', type: 'nominal', sort: { field: 'category_index', op: 'min', order: 'ascending' }, axis: { grid: false } },
          y: { field: 'value', type: 'quantitative' },
          color: { field: 'group_label', type: 'nominal' },
        },
      },
    ],
  }).spec
  assert.equal(spec.signals.filter((signal) => signal.name === 'legend_group_tuple').length, 1)
  assert.ok(spec.signals.some((signal) => signal.name === 'legend_group_group_label_legend'))
  assert.doesNotThrow(() => parse(spec))
})
