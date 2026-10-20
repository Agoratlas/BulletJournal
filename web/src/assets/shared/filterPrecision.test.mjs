import assert from 'node:assert/strict'
import { before, test } from 'node:test'
import { createServer } from 'vite'

let modifiers
before(async () => {
  const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false }, optimizeDeps: { noDiscovery: true } })
  try {
    modifiers = await server.ssrLoadModule('/src/assets/shared/modifiers.ts')
  } finally {
    await server.close()
  }
})

const column = { id: 'id', title: 'ID', dataType: 'Int64', filterKinds: ['value', 'range'] }
const inputs = { column, rangeLower: '', rangeUpper: '', valueInput: '', includeNull: false, regexPattern: '', regexCaseSensitive: false }

test('exact-value filters preserve large signed and unsigned integer inputs across JSON and editor round trips', () => {
  const values = ['2103982719223071223', '-2103982719223071223', '18446744073709551615', '9007199254740992']
  const filter = modifiers.buildFilterFromInputs({ ...inputs, kind: 'value', valueInput: values.join(', ') })
  assert.deepEqual(filter.values, values)
  const saved = JSON.parse(JSON.stringify({ filters: [filter], highlights: [{ ...filter, highlight_color: '#ff0000', highlight_scope: 'cell' }] }))
  const state = modifiers.initialTableStateFromModifiers({}, saved)
  assert.deepEqual(state.filters, [filter])
  assert.deepEqual(state.highlights, saved.highlights)
  for (const rule of [state.filters[0], state.highlights[0]]) {
    const draft = modifiers.filterDraftFromColumn(column, rule)
    assert.deepEqual(modifiers.buildFilterFromInputs({ ...inputs, ...draft, column }), filter)
  }
})

test('range filters and highlights keep adjacent large bounds distinct after saving and reopening', () => {
  const filter = modifiers.buildFilterFromInputs({ ...inputs, kind: 'range', rangeLower: '2103982719223071223', rangeUpper: '2103982719223071224' })
  assert.equal(filter.lower, '2103982719223071223')
  assert.equal(filter.upper, '2103982719223071224')
  const saved = JSON.parse(JSON.stringify({ filters: [filter], highlights: [{ ...filter, highlight_color: '#ff0000', highlight_scope: 'row' }] }))
  const state = modifiers.initialTableStateFromModifiers({}, saved)
  for (const rule of [state.filters[0], state.highlights[0]]) {
    const draft = modifiers.filterDraftFromColumn(column, rule)
    assert.deepEqual(modifiers.buildFilterFromInputs({ ...inputs, ...draft, column }), filter)
  }
})

test('numeric filters still accept safe integers, decimals, and scientific notation and reject invalid inputs', () => {
  for (const [input, expected] of [
    ['9007199254740991', 9007199254740991], ['-42', -42], ['1.25', 1.25], ['1e3', 1000],
    ['2.103982719223071223e18', '2.103982719223071223e18'], ['2103982719223071223.0', '2103982719223071223.0'],
  ]) {
    assert.equal(modifiers.coerceDraftValue(input, 'numeric'), expected)
  }
  for (const input of ['NaN', 'Infinity', 'word']) {
    assert.throws(() => modifiers.coerceDraftValue(input, 'numeric'), /not a valid number/)
  }
  assert.equal(modifiers.coerceDraftValue('2103982719223071223', 'text'), '2103982719223071223')
  assert.equal(modifiers.coerceDraftValue('true', 'bool'), true)
})
