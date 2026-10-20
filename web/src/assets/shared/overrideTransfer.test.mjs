import assert from 'node:assert/strict'
import { test } from 'node:test'

import { parseSettingsOverrides, settingsOverrideTransfer } from './overrideTransfer.ts'

const schema = [
  { id: 'bar_width', kind: 'float', default_value: 90, min_value: 0, max_value: 100 },
  { id: 'bin_count', kind: 'int', default_value: 20, min_value: 1, max_value: 100 },
  { id: 'shape_style', kind: 'enum', default_value: 'outline', options: ['outline', 'filled'] },
  { id: 'group_normalize', kind: 'value', default_value: true },
  { id: 'title', kind: 'chart_title', default_value: { size: 20, text: 'Title', hide_title: true, position: 'top' } },
  { id: 'x_axis', kind: 'chart_axis', default_value: { label: 'X', tick_count: null, scale: 'lin' } },
]
const keys = schema.map((entry) => entry.id)

test('settings exports round trip partial modifiers, enums, and legacy grouping defaults', () => {
  const overrides = { bar_width: 75.5, shape_style: 'filled', title: { text: 'Custom', hide_title: false }, x_axis: { tick_count: 5 }, group_normalize: 'sum' }
  assert.deepEqual(parseSettingsOverrides(JSON.stringify(overrides), schema, keys), { modifier_overrides: overrides, panel_height: null })
  assert.deepEqual(parseSettingsOverrides('{}', schema, keys), { modifier_overrides: {}, panel_height: null })
})

test('imports reject incompatible modifiers and malformed schemas before applying them', () => {
  for (const text of ['null', '[]', '"text"', '{', '{"unknown":1}', '{"title":{"unknown":true}}', '{"title":{"hide_title":"false"}}', '{"title":{"position":"left"}}', '{"bar_width":101}', '{"bar_width":"20"}', '{"bin_count":1.5}', '{"shape_style":"circle"}', '{"x_axis":{"tick_count":{}}}', '{"x_axis":{"scale":"sqrt"}}']) {
    assert.throws(() => parseSettingsOverrides(text, schema, keys), undefined, text)
  }
  assert.throws(() => parseSettingsOverrides('{"bar_width":50}', schema, ['title']), /not supported/)
})

test('a resized panel exports its height separately from chart modifiers and restores it on import', () => {
  const modifiers = { bar_width: 65, title: { text: 'Custom' } }
  const exported = settingsOverrideTransfer(modifiers, 800, true)
  assert.deepEqual(exported, { ...modifiers, panel_height: 800 })
  assert.deepEqual(parseSettingsOverrides(JSON.stringify(exported), schema, keys, 240), {
    modifier_overrides: modifiers,
    panel_height: 800,
  })
  assert.equal(Object.hasOwn(modifiers, 'panel_height'), false)
  assert.deepEqual(parseSettingsOverrides('{"panel_height":800}', schema, keys, 240), {
    modifier_overrides: {}, panel_height: 800,
  })
})

test('default-height exports omit panel_height, including collection layout defaults', () => {
  for (const defaultHeight of [600, 300, 240]) {
    const exported = settingsOverrideTransfer({ bar_width: 65 }, defaultHeight, false)
    assert.deepEqual(exported, { bar_width: 65 })
    assert.deepEqual(parseSettingsOverrides(JSON.stringify(exported), schema, keys, defaultHeight === 300 ? 300 : 240), {
      modifier_overrides: { bar_width: 65 }, panel_height: null,
    })
  }
  assert.deepEqual(settingsOverrideTransfer({}, 600, false), {})
  assert.deepEqual(parseSettingsOverrides('{}', schema, keys, 240), { modifier_overrides: {}, panel_height: null })
  assert.deepEqual(parseSettingsOverrides('{"panel_height":null}', schema, keys, 240), { modifier_overrides: {}, panel_height: null })
})

test('height imports enforce integer bounds and collection minimum height', () => {
  for (const height of ['800', true, {}, [], 0, -1, 239, 961, 400.5]) {
    assert.throws(() => parseSettingsOverrides(JSON.stringify({ panel_height: height }), schema, keys, 240), /panel_height/)
  }
  assert.throws(() => parseSettingsOverrides('{"panel_height":1e999}', schema, keys, 240), /panel_height/)
  assert.throws(() => parseSettingsOverrides('{"panel_height":240}', schema, keys, 300), /between 300 and 960/)
  for (const height of [240, 300, 960]) {
    assert.equal(parseSettingsOverrides(JSON.stringify({ panel_height: height }), schema, keys, 240).panel_height, height)
  }
  assert.throws(() => parseSettingsOverrides('{"panel_height":800}', schema, keys), /not supported/)
  assert.throws(() => parseSettingsOverrides('{"panel_height":800,"unknown":1}', schema, keys, 240), /not supported/)
})
