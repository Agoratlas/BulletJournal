import assert from 'node:assert/strict'
import { test } from 'node:test'

import { filterDashboardPanels } from './dashboardSearch.ts'

test('dashboard search matches literal case-insensitive top-level titles and notebook/asset ids', () => {
  const panels = [
    { panel_id: 'custom', node_id: 'Sales', asset_name: 'overview', visible: false, position: 0 },
    { panel_id: 'collection', node_id: 'reports', asset_name: 'charts', visible: true, position: 1 },
  ]
  const children = [{ name: 'child-only', title: 'Child title' }]
  const assets = new Map([
    ['Sales/overview', { title: 'Revenue [2026]' }],
    ['reports/charts', { title: 'All charts', asset_type: 'collection', definition: { children } }],
  ])
  assert.deepEqual(filterDashboardPanels(panels, assets, ''), panels)
  assert.deepEqual(filterDashboardPanels(panels, assets, 'SALES/OVER'), [panels[0]])
  assert.deepEqual(filterDashboardPanels(panels, assets, '[2026]'), [panels[0]])
  assert.deepEqual(filterDashboardPanels(panels, assets, 'revenue'), [panels[0]])
  assert.deepEqual(filterDashboardPanels(panels, assets, 'custom'), [])
  assert.deepEqual(filterDashboardPanels(panels, assets, 'child'), [])
  assert.deepEqual(filterDashboardPanels(panels, assets, 'all charts'), [panels[1]])
  assert.deepEqual(children, [{ name: 'child-only', title: 'Child title' }])
  assert.equal(panels[0].visible, false)
})
