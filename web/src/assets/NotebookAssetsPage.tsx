import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { saveNotebookDashboard, listNodeAssets } from '../lib/api'
import { SaveDashboardDialog } from '../components/Dialogs'
import type { PersistedAssetPanelState } from './AssetPanels'
import { AssetPanel } from './AssetPanels'

export function NotebookAssetsPage({
  nodeId,
  nodeTitle,
  projectTitle,
  existingNodeIds,
  onOpenDashboard,
}: {
  nodeId: string
  nodeTitle?: string | null
  projectTitle?: string | null
  existingNodeIds: string[]
  onOpenDashboard?: (dashboardId: string) => void
}) {
  const [panelStates, setPanelStates] = useState<Record<string, PersistedAssetPanelState>>({})
  const [saveBusy, setSaveBusy] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const assetsQuery = useQuery({
    queryKey: ['node-assets', nodeId],
    queryFn: () => listNodeAssets(nodeId),
    retry: false,
  })
  const assets = assetsQuery.data ?? []
  const readyCount = assets.filter((asset) => asset.current_asset_version_id !== null).length

  async function handleSaveDashboard(title: string, dashboardId: string) {
    setSaveBusy(true)
    setSaveError(null)
    try {
      const created = await saveNotebookDashboard(nodeId, {
        dashboard_id: dashboardId,
        title,
        panels: assets.map((asset, index) => ({
          panel_id: `${asset.node_id}/${asset.asset_name}`,
          node_id: asset.node_id,
          asset_name: asset.asset_name,
          visible: true,
          position: index,
          modifier_overrides: panelStates[`${asset.node_id}/${asset.asset_name}`]?.modifier_overrides ?? {},
          override_schema_hash: panelStates[`${asset.node_id}/${asset.asset_name}`]?.override_schema_hash ?? asset.override_schema_hash,
        })),
      })
      onOpenDashboard?.(created.dashboard_id)
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Could not save this dashboard.')
    } finally {
      setSaveBusy(false)
    }
  }

  return (
    <div className="assets-page-shell">
      <div className="canvas-underlay" />
      <div className="assets-page">
        <header className="panel assets-page-header">
          <div>
            <p className="eyebrow">Notebook assets</p>
            <h1>{nodeTitle || nodeId}</h1>
            <p className="assets-page-subtitle">
              {projectTitle || 'BulletJournal'}
              {' · '}
              <code>{nodeId}</code>
            </p>
          </div>
          <div className="assets-page-header-actions">
            <button type="button" className="secondary" onClick={() => setSaveDialogOpen(true)} disabled={saveBusy || assetsQuery.isLoading || assets.length === 0}>
              {saveBusy ? 'Saving...' : 'Save as dashboard'}
            </button>
            <div className="assets-page-stat">
              <strong>{assets.length}</strong>
              <span>declared</span>
            </div>
            <div className="assets-page-stat">
              <strong>{readyCount}</strong>
              <span>produced</span>
            </div>
          </div>
        </header>

        {assetsQuery.isLoading ? (
          <div className="panel assets-empty-state">
            <h2>Loading assets</h2>
            <p>Fetching the current notebook asset heads.</p>
          </div>
        ) : null}

        {assetsQuery.isError ? (
          <div className="panel assets-empty-state error">
            <h2>Could not load assets</h2>
            <p>{assetsQuery.error instanceof Error ? assetsQuery.error.message : 'Unknown error.'}</p>
          </div>
        ) : null}

        {saveError ? (
          <div className="panel assets-empty-state error">
            <h2>Could not save dashboard</h2>
            <p>{saveError}</p>
          </div>
        ) : null}

        {!assetsQuery.isLoading && !assetsQuery.isError && assets.length === 0 ? (
          <div className="panel assets-empty-state">
            <h2>No assets declared</h2>
            <p>This notebook has not declared any assets with <code>assets.push(...)</code>.</p>
          </div>
        ) : null}

        <div className="assets-panel-list">
          {assets.map((asset) => (
            <AssetPanel
              key={`${asset.node_id}/${asset.asset_name}`}
              nodeId={nodeId}
              asset={asset}
              persistedState={panelStates[`${asset.node_id}/${asset.asset_name}`] ?? null}
              onPersistedStateChange={(state) => {
                setPanelStates((current) => ({
                  ...current,
                  [`${asset.node_id}/${asset.asset_name}`]: state,
                }))
              }}
            />
          ))}
        </div>
      </div>
      {saveDialogOpen ? (
        <SaveDashboardDialog
          initialTitle={`${nodeTitle || nodeId} dashboard`}
          existingIds={existingNodeIds}
          onClose={() => setSaveDialogOpen(false)}
          onSave={async (payload) => {
            await handleSaveDashboard(payload.title, payload.dashboardId)
          }}
        />
      ) : null}
    </div>
  )
}
