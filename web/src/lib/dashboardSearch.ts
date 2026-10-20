import type { AssetRecord, DashboardPanelRecord } from './types'

export function filterDashboardPanels(panels: DashboardPanelRecord[], assets: Map<string, AssetRecord>, search: string): DashboardPanelRecord[] {
  const substring = search.toLowerCase()
  return panels.filter((panel) => {
    const id = `${panel.node_id}/${panel.asset_name}`
    return id.toLowerCase().includes(substring) || (assets.get(id)?.title ?? '').toLowerCase().includes(substring)
  })
}
