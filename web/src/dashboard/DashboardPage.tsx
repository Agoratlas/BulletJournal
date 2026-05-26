import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { getDashboard, listNodeAssets } from '../lib/api'
import type { AssetRecord } from '../lib/types'
import { AssetPanel } from '../assets/AssetPanels'

export function DashboardPage({
  dashboardId,
  projectTitle,
}: {
  dashboardId: string
  projectTitle?: string | null
}) {
  const dashboardQuery = useQuery({
    queryKey: ['dashboard', dashboardId],
    queryFn: () => getDashboard(dashboardId),
    retry: false,
  })
  const sources = dashboardQuery.data?.sources ?? []
  const assetsQuery = useQuery({
    queryKey: ['dashboard-assets', dashboardId, sources.map((source) => source.node_id).sort().join('|')],
    queryFn: async () => {
      const rows = await Promise.all(sources.map(async (source) => ({
        nodeId: source.node_id,
        assets: await listNodeAssets(source.node_id),
      })))
      return Object.fromEntries(rows.map((row) => [row.nodeId, row.assets])) as Record<string, AssetRecord[]>
    },
    enabled: sources.length > 0,
    retry: false,
  })

  const visiblePanels = useMemo(() => {
    const dashboard = dashboardQuery.data
    const assetsByNode = assetsQuery.data ?? {}
    if (!dashboard) {
      return []
    }
    const assetByLogicalId = new Map<string, AssetRecord>()
    for (const [nodeId, assets] of Object.entries(assetsByNode)) {
      for (const asset of assets) {
        assetByLogicalId.set(`${nodeId}/${asset.asset_name}`, asset)
      }
    }
    return dashboard.panels
      .slice()
      .sort((left, right) => left.position - right.position)
      .filter((panel) => panel.visible)
      .map((panel) => ({
        panel,
        asset: assetByLogicalId.get(`${panel.node_id}/${panel.asset_name}`) ?? null,
      }))
  }, [assetsQuery.data, dashboardQuery.data])

  return (
    <div className="assets-page-shell">
      <div className="canvas-underlay" />
      <div className="assets-page">
        <header className="panel assets-page-header">
          <div>
            <p className="eyebrow">Saved dashboard</p>
            <h1>{dashboardQuery.data?.title ?? dashboardId}</h1>
            <p className="assets-page-subtitle">
              {projectTitle || 'BulletJournal'}
              {' · '}
              <code>{dashboardId}</code>
            </p>
          </div>
          <div className="assets-page-header-actions">
            <div className="assets-page-stat">
              <strong>{dashboardQuery.data?.sources.length ?? 0}</strong>
              <span>{(dashboardQuery.data?.sources.length ?? 0) === 1 ? 'source' : 'sources'}</span>
            </div>
            <div className="assets-page-stat">
              <strong>{dashboardQuery.data?.panels.length ?? 0}</strong>
              <span>{(dashboardQuery.data?.panels.length ?? 0) === 1 ? 'panel' : 'panels'}</span>
            </div>
          </div>
        </header>

        {dashboardQuery.isLoading ? (
          <div className="panel assets-empty-state">
            <h2>Loading dashboard</h2>
            <p>Fetching the saved dashboard document.</p>
          </div>
        ) : null}

        {dashboardQuery.isError ? (
          <div className="panel assets-empty-state error">
            <h2>Could not load dashboard</h2>
            <p>{dashboardQuery.error instanceof Error ? dashboardQuery.error.message : 'Unknown error.'}</p>
          </div>
        ) : null}

        {!dashboardQuery.isLoading && !dashboardQuery.isError && assetsQuery.isLoading ? (
          <div className="panel assets-empty-state">
            <h2>Loading dashboard assets</h2>
            <p>Fetching assets for the configured notebook sources.</p>
          </div>
        ) : null}

        {assetsQuery.isError ? (
          <div className="panel assets-empty-state error">
            <h2>Could not load dashboard assets</h2>
            <p>{assetsQuery.error instanceof Error ? assetsQuery.error.message : 'Unknown error.'}</p>
          </div>
        ) : null}

        {!dashboardQuery.isLoading && !dashboardQuery.isError && !assetsQuery.isLoading && !visiblePanels.length ? (
          <div className="panel assets-empty-state">
            <h2>No visible panels</h2>
            <p>This dashboard does not currently expose any visible asset panels.</p>
          </div>
        ) : null}

        <div className="assets-panel-list">
          {visiblePanels.map(({ panel, asset }) => asset ? (
            <AssetPanel
              key={panel.panel_id}
              nodeId={panel.node_id}
              asset={asset}
              persistedState={{
                modifier_overrides: panel.modifier_overrides,
                override_schema_hash: panel.override_schema_hash,
              }}
            />
          ) : (
            <section key={panel.panel_id} className="panel asset-panel-card">
              <div className="asset-panel-header">
                <div className="asset-panel-heading">
                  <div className="asset-panel-title-row">
                    <h2>{panel.panel_id}</h2>
                    <span className="asset-state-badge is-pending">missing</span>
                  </div>
                  <p className="asset-panel-description">The referenced asset could not be loaded from <code>{panel.node_id}</code>.</p>
                </div>
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}
