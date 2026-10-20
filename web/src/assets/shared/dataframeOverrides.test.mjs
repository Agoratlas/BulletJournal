import assert from 'node:assert/strict'
import { before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createServer } from 'vite'

let DataFrameAssetPanel
let ArtifactCard
let layout
let modifiers
let transfer

before(async () => {
  const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false } })
  try {
    ;({ DataFrameAssetPanel } = await server.ssrLoadModule('/src/assets/panels/DataFrameAssetPanel.tsx'))
    ;({ ArtifactCard } = await server.ssrLoadModule('/src/components/ArtifactCard.tsx'))
    layout = await server.ssrLoadModule('/src/assets/shared/layout.tsx')
    modifiers = await server.ssrLoadModule('/src/assets/shared/modifiers.ts')
    transfer = await server.ssrLoadModule('/src/assets/shared/overrideTransfer.ts')
  } finally {
    await server.close()
  }
})

function elements(tree) {
  if (Array.isArray(tree)) return tree.flatMap(elements)
  if (!React.isValidElement(tree)) return []
  return [tree, ...elements(tree.props.children)]
}

function capture(Component, props) {
  const client = new QueryClient()
  let tree
  function Wrapper() {
    tree = Component(props)
    return null
  }
  try {
    renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(Wrapper)))
    return tree
  } finally {
    client.clear()
  }
}

test('DataFrame override menu restores saved lines and supports reset, import, and export', () => {
  for (const publishedDefault of [1, 4]) {
    for (const savedLines of [undefined, 2, 10]) {
      const tree = capture(DataFrameAssetPanel, {
        nodeId: 'notebook',
        asset: {
          asset_name: 'table', definition: { max_text_lines: publishedDefault },
          default_modifiers: {}, modifier_schema: [], current_asset_version_id: null,
        },
        panelInfo: { panelId: 'notebook/table' },
        persistedState: savedLines === undefined ? null : {
          modifier_overrides: { max_text_lines: savedLines }, override_schema_hash: null,
        },
      })
      const expected = savedLines ?? publishedDefault
      assert.equal(tree.props.settingsTitle, 'Modifier overrides')
      assert.equal(tree.props.settingsActive, expected !== publishedDefault)
      const settings = elements(tree.props.settingsBody)
      const slider = settings.find((element) => element.type === 'input' && element.props.type === 'range')
      assert.equal(slider.props.value, expected)
      assert.equal(slider.props.min, 1)
      assert.equal(slider.props.max, 10)
      assert.equal(slider.props.step, 1)
      assert.equal(settings.find((element) => element.type === 'strong').props.children, expected)
      const actions = settings.find((element) => element.type === layout.SettingsOverrideActions)
      assert.ok(actions)
      assert.deepEqual(actions.props.values, { max_text_lines: expected })
      const imported = transfer.parseSettingsOverrides('{"max_text_lines":10}', actions.props.schema, ['max_text_lines'])
      assert.deepEqual(imported.modifier_overrides, { max_text_lines: 10 })
      for (const value of [0, 11, 1.5, true]) {
        assert.throws(() => transfer.parseSettingsOverrides(JSON.stringify({ max_text_lines: value }), actions.props.schema, ['max_text_lines']))
      }
    }
  }
})

test('DataFrame artifact previews expose the menu without a persistence callback', (t) => {
  const originalWindow = globalThis.window
  globalThis.window = {}
  t.after(() => {
    if (originalWindow === undefined) delete globalThis.window
    else globalThis.window = originalWindow
  })
  const artifactTree = ArtifactCard({ artifact: {
    node_id: 'notebook', artifact_name: 'frame', data_type: 'pandas.DataFrame',
    current_version_id: 1, state: 'fresh', preview: { kind: 'dataframe', rows: 2, column_names: ['text'] },
  } })
  const preview = elements(artifactTree).find((element) => element.type === DataFrameAssetPanel)
  assert.ok(preview)
  assert.equal(preview.props.persistedState, null)
  assert.equal(preview.props.onPersistedStateChange, undefined)
  assert.equal(typeof preview.props.prepare, 'function')
  const tree = capture(DataFrameAssetPanel, { ...preview.props, asset: { ...preview.props.asset, current_asset_version_id: null } })
  assert.ok(tree.props.settingsBody)
  assert.equal(tree.props.settingsActive, false)
})

test('shared chart override UI exposes angle and font size on both axes', () => {
  const defaults = modifiers.defaultBarChartChartOverrides({}, [])
  for (const [axis, expectedAngle] of [[defaults.xAxis, 45], [defaults.yAxis, 0]]) {
    let updated
    const tree = capture(layout.AxisOverridesSection, {
      title: 'Axis', overrides: axis, defaultOverrides: axis, defaultLabel: 'Axis',
      onChange: (value) => { updated = value },
    })
    const labels = elements(tree).filter((element) => element.type === 'label')
    const angleField = labels.find((label) => elements(label).some((element) => element.type === 'span' && element.props.children === 'Tick angle'))
    const select = elements(angleField).find((element) => element.type === 'select')
    assert.equal(select.props.value, expectedAngle)
    select.props.onChange({ target: { value: '90' } })
    assert.equal(updated.tickAngle, 90)
    const fontField = labels.find((label) => elements(label).some((element) => element.type === 'span' && element.props.children === 'Tick font size'))
    assert.ok(fontField)
  }
})
