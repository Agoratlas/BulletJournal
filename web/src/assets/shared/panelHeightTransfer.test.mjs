import assert from 'node:assert/strict'
import { before, test } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createServer } from 'vite'

let SettingsOverrideActions
let CollectionAssetPanel
let chartPanels

before(async () => {
  const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false } })
  try {
    ;({ SettingsOverrideActions } = await server.ssrLoadModule('/src/assets/shared/layout.tsx'))
    ;({ CollectionAssetPanel } = await server.ssrLoadModule('/src/assets/panels/CollectionAssetPanel.tsx'))
    chartPanels = await Promise.all(['BarChart', 'Histogram', 'PieChart', 'ScatterPlot'].map(async (name) => {
      const module = await server.ssrLoadModule(`/src/assets/panels/${name}AssetPanel.tsx`)
      return module[`${name}AssetPanel`]
    }))
  } finally {
    await server.close()
  }
})

// Capture the component's elements while running its hooks in a React render.
function capture(Component, props, provider) {
  let tree
  function Wrapper() {
    tree = Component(props)
    return null
  }
  const wrapper = React.createElement(Wrapper)
  renderToStaticMarkup(provider ? provider(wrapper) : wrapper)
  return tree
}

function elements(tree) {
  if (Array.isArray(tree)) return tree.flatMap(elements)
  if (!React.isValidElement(tree)) return []
  return [tree, ...elements(tree.props.children)]
}

test('height-only overrides can reset and export through the settings buttons', async (t) => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  let copied
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { clipboard: { writeText: async (text) => { copied = text } } },
  })
  t.after(() => {
    if (original) Object.defineProperty(globalThis, 'navigator', original)
    else delete globalThis.navigator
  })
  const calls = []
  const props = {
    schema: [], values: {}, overrides: {}, panelHeight: 800, isPanelResized: true, hasOverrides: true,
    onReset: () => calls.push('modifiers reset'), onImport: () => {},
    onPanelHeightChange: (height) => calls.push(height),
  }
  const buttons = elements(capture(SettingsOverrideActions, props)).filter((element) => element.type === 'button')
  const reset = buttons.find((button) => button.props.children === 'Reset to default')
  assert.equal(reset.props.disabled, false)
  reset.props.onClick()
  assert.deepEqual(calls, ['modifiers reset', null])
  await buttons.find((button) => button.props.children === 'Export').props.onClick()
  await Promise.resolve()
  assert.deepEqual(JSON.parse(copied), { panel_height: 800 })

  const defaultButtons = elements(capture(SettingsOverrideActions, { ...props, panelHeight: 300, isPanelResized: false, hasOverrides: false })).filter((element) => element.type === 'button')
  assert.equal(defaultButtons.find((button) => button.props.children === 'Reset to default').props.disabled, true)
  await defaultButtons.find((button) => button.props.children === 'Export').props.onClick()
  await Promise.resolve()
  assert.deepEqual(JSON.parse(copied), {})
})

test('all four chart panels mark resized height as an override and wire it into transfer actions', () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    for (const Panel of chartPanels) {
      const heightChange = () => {}
      const tree = capture(Panel, {
        nodeId: 'notebook',
        asset: { asset_name: 'chart', definition: {}, modifier_schema: [], default_modifiers: {}, current_asset_version_id: null },
        panelInfo: { panelId: 'notebook/chart' }, persistedState: null,
        panelHeight: 800, isPanelResized: true, minPanelHeight: 300, onPanelHeightChange: heightChange,
      }, (wrapper) => React.createElement(QueryClientProvider, { client }, wrapper))
      assert.equal(tree.props.settingsActive, true)
      const actions = elements(tree.props.settingsBody).find((element) => element.type === SettingsOverrideActions)
      assert.ok(actions)
      assert.equal(actions.props.hasOverrides, true)
      assert.equal(actions.props.panelHeight, 800)
      assert.equal(actions.props.isPanelResized, true)
      assert.equal(actions.props.minPanelHeight, 300)
      assert.equal(actions.props.onPanelHeightChange, heightChange)
    }
  } finally {
    client.clear()
  }
})

test('collection children preserve both height and modifiers when updates arrive together in either order', () => {
  for (const displayMode of ['all', '2_columns', '3_columns']) {
    for (const heightFirst of [false, true]) {
      let saved
      const asset = {
        node_id: 'notebook', asset_name: 'collection', current_asset_version_id: 1, override_schema_hash: null,
        definition: {
          display_mode_default: displayMode,
          children: [{ name: 'chart', title: 'Chart', asset_type: 'bar_chart', modifier_schema: [], default_modifiers: {}, override_schema_hash: 'schema' }],
        },
      }
      const child = elements(capture(CollectionAssetPanel, {
        asset, panelInfo: { panelId: 'notebook/collection' }, persistedState: null,
        onPersistedStateChange: (state) => { saved = state },
      })).find((element) => element.props.panelId === 'notebook/collection/chart')
      assert.ok(child)
      assert.equal(child.props.isPanelResized, false)
      const modifierChange = () => child.props.onPersistedStateChange({ modifier_overrides: { bar_width: 65 }, override_schema_hash: 'schema' })
      const heightChange = () => child.props.onPanelHeightChange(400)
      if (heightFirst) { heightChange(); modifierChange() }
      else { modifierChange(); heightChange() }
      assert.deepEqual(saved.modifier_overrides.child_panels.chart, {
        modifier_overrides: { bar_width: 65 }, override_schema_hash: 'schema',
        panel_height: 400 * (displayMode === '2_columns' ? 2 : displayMode === '3_columns' ? 3 : 1),
      })
      const restoredChild = elements(capture(CollectionAssetPanel, {
        asset, panelInfo: { panelId: 'notebook/collection' }, persistedState: saved,
        onPersistedStateChange: (state) => { saved = state },
      })).find((element) => element.props.panelId === 'notebook/collection/chart')
      assert.equal(restoredChild.props.panelHeight, 400)
      assert.equal(restoredChild.props.isPanelResized, true)
      restoredChild.props.onPanelHeightChange(null)
      assert.deepEqual(saved.modifier_overrides.child_panels.chart, {
        modifier_overrides: { bar_width: 65 }, override_schema_hash: 'schema',
      })
    }
  }
})
