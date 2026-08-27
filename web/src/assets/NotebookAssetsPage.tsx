import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { useQuery } from '@tanstack/react-query'

import { saveNotebookDashboard, listNodeAssets } from '../lib/api'
import { useDocumentMetadata } from '../lib/documentMetadata'
import { SaveDashboardDialog } from '../components/Dialogs'
import { DashboardSidebar, normalizeDashboardPanels } from '../dashboard/DashboardSidebar'
import type { DashboardPanelRecord } from '../lib/types'
import { AssetPanel } from './AssetPanel'

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
  useDocumentMetadata(nodeTitle || nodeId, 'dashboard')
  const [panels, setPanels] = useState<DashboardPanelRecord[]>([])
  const initializedNodeIdRef = useRef<string | null>(null)
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
  const assetsByLogicalId = useMemo(() => new Map(assets.map((asset) => [`${asset.node_id}/${asset.asset_name}`, asset] as const)), [assets])
  const orderedPanels = useMemo(() => normalizeDashboardPanels(panels), [panels])
  const visiblePanels = orderedPanels.filter((panel) => panel.visible)

  useEffect(() => {
    if (!assetsQuery.data || initializedNodeIdRef.current === nodeId) return
    initializedNodeIdRef.current = nodeId
    setPanels(assetsQuery.data.map((asset, index) => ({
      panel_id: `${asset.node_id}/${asset.asset_name}`,
      node_id: asset.node_id,
      asset_name: asset.asset_name,
      visible: true,
      position: index,
      panel_height: null,
      modifier_overrides: {},
      override_schema_hash: asset.override_schema_hash,
    })))
  }, [assetsQuery.data, nodeId])

  async function handleSaveDashboard(title: string, dashboardId: string) {
    setSaveBusy(true)
    setSaveError(null)
    try {
      const created = await saveNotebookDashboard(nodeId, {
        dashboard_id: dashboardId,
        title,
        panels: orderedPanels,
      })
      onOpenDashboard?.(created.dashboard_id)
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : 'Could not save this dashboard.')
    } finally {
      setSaveBusy(false)
    }
  }

  function handlePanelNavigate(event: MouseEvent<HTMLAnchorElement>, panelId: string) {
    event.preventDefault()
    const sectionId = dashboardPanelDomId(panelId)
    const target = document.getElementById(sectionId)
    if (!target) return
    target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    window.history.replaceState(null, '', `#${sectionId}`)
    event.currentTarget.blur()
  }

  return (
    <div className="assets-page-shell dashboard-page-shell">
      <div className="canvas-underlay" />
      <DashboardSidebar panels={panels} assetsByLogicalId={assetsByLogicalId} panelHref={(panelId) => `#${dashboardPanelDomId(panelId)}`} onPanelNavigate={handlePanelNavigate} onPanelsChange={(updater) => setPanels((current) => normalizeDashboardPanels(updater(current)))} />
      <div className="assets-page dashboard-page">
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
          {!assetsQuery.isLoading && !assetsQuery.isError && assets.length > 0 && visiblePanels.length === 0 ? (
            <div className="panel assets-empty-state">
              <h2>No visible panels</h2>
              <p>This dashboard does not currently expose any visible asset panels.</p>
            </div>
          ) : null}
          {visiblePanels.map((panel) => {
            const asset = assetsByLogicalId.get(`${panel.node_id}/${panel.asset_name}`)
            if (!asset) return null
            const panelId = panel.panel_id
            return (
              <AssetPanel
                key={panelId}
                sectionId={dashboardPanelDomId(panelId)}
                panelId={panelId}
                nodeId={nodeId}
                asset={asset}
                viewerMode="notebook"
                persistedState={{ modifier_overrides: panel.modifier_overrides, override_schema_hash: panel.override_schema_hash }}
                onPersistedStateChange={(state) => {
                  setPanels((current) => current.map((entry) => entry.panel_id === panelId ? { ...entry, modifier_overrides: state.modifier_overrides, override_schema_hash: state.override_schema_hash } : entry))
                }}
                panelHeight={panel.panel_height}
                onPanelHeightChange={(panelHeight) => {
                  setPanels((current) => current.map((entry) => entry.panel_id === panelId ? { ...entry, panel_height: panelHeight } : entry))
                }}
              />
            )
          })}
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

function dashboardPanelDomId(panelId: string): string {
  return `dashboard-panel-${panelId.replace(/[^a-zA-Z0-9_-]+/g, '-')}`
}
