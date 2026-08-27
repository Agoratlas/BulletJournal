import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { useQueries, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { ApiError, appUrl, getDashboard, listNodeAssets, patchDashboard } from '../lib/api'
import { useDocumentMetadata } from '../lib/documentMetadata'
import type { AssetRecord, DashboardPanelRecord, DashboardRecord } from '../lib/types'
import { AssetPanel, type PersistedAssetPanelState } from '../assets/AssetPanel'
import { DashboardSidebar, normalizeDashboardPanels } from './DashboardSidebar'

const DASHBOARD_SAVE_DEBOUNCE_MS = 250

export function DashboardPage({
  dashboardId,
  projectTitle,
}: {
  dashboardId: string
  projectTitle?: string | null
}) {
  const queryClient = useQueryClient()
  const dashboardQuery = useQuery({
    queryKey: ['dashboard', dashboardId],
    queryFn: () => getDashboard(dashboardId),
    retry: false,
    staleTime: 30_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  })
  const [draftDashboard, setDraftDashboard] = useState<DashboardRecord | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved'>('idle')
  const draftRef = useRef<DashboardRecord | null>(null)
  const dirtyRef = useRef(false)
  const saveTimerRef = useRef<number | null>(null)
  const saveInFlightRef = useRef(false)

  useEffect(() => {
    if (!dashboardQuery.data || dirtyRef.current) {
      return
    }
    if (draftRef.current === dashboardQuery.data) {
      return
    }
    draftRef.current = dashboardQuery.data
    setDraftDashboard(dashboardQuery.data)
  }, [dashboardQuery.data, draftDashboard])

  useEffect(() => () => {
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
    }
  }, [])

  const dashboard = draftDashboard ?? dashboardQuery.data ?? null
  useDocumentMetadata(dashboard?.title ?? dashboardId, 'dashboard')
  const sources = dashboard?.sources ?? []
  const sourceAssetQueries = useQueries({
    queries: sources.map((source) => ({
      queryKey: ['node-assets', source.node_id],
      queryFn: () => listNodeAssets(source.node_id),
      retry: false,
    })),
  })
  const assetsError = sourceAssetQueries.find((query) => query.isError)?.error ?? null
  const hasAllAssetsData = sources.length === 0 || sources.every((_, index) => sourceAssetQueries[index]?.data !== undefined)
  const assetsLoading = sources.length > 0 && !hasAllAssetsData && sourceAssetQueries.some((query) => query.isLoading)
  const assetsByNode = useMemo(() => {
    const next: Record<string, AssetRecord[]> = {}
    for (const [index, source] of sources.entries()) {
      const assets = sourceAssetQueries[index]?.data
      if (assets !== undefined) {
        next[source.node_id] = assets
      }
    }
    return next
  }, [sourceAssetQueries, sources])

  useEffect(() => {
    if (!draftDashboard || !dirtyRef.current || dashboardQuery.isLoading || dashboardQuery.isError) {
      return
    }
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current)
    }
    saveTimerRef.current = window.setTimeout(() => {
      void persistDashboardDraft()
    }, DASHBOARD_SAVE_DEBOUNCE_MS)
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current)
      }
    }
  }, [dashboardId, dashboardQuery.isError, dashboardQuery.isLoading, draftDashboard])

  const assetsByLogicalId = useMemo(() => {
    const assetMap = new Map<string, AssetRecord>()
    for (const [nodeId, assets] of Object.entries(assetsByNode)) {
      for (const asset of assets) {
        assetMap.set(`${nodeId}/${asset.asset_name}`, asset)
      }
    }
    return assetMap
  }, [assetsByNode])

  const orderedPanels = useMemo(() => {
    if (!dashboard) {
      return []
    }
    return normalizeDashboardPanels(dashboard.panels)
  }, [dashboard])

  const visiblePanels = orderedPanels
    .filter((panel) => panel.visible)
    .map((panel) => ({
      panel,
      asset: assetsByLogicalId.get(`${panel.node_id}/${panel.asset_name}`) ?? null,
    }))

  useEffect(() => {
    if (!dashboard || assetsLoading || assetsError || !hasAllAssetsData) {
      return
    }
    const reconciledPanels = reconcileDashboardPanels(dashboard, assetsByNode)
    if (dashboardPanelsKey(dashboard.panels) === dashboardPanelsKey(reconciledPanels)) {
      return
    }
    updateDraftPanels(() => reconciledPanels)
  }, [assetsByNode, assetsError, assetsLoading, dashboard, hasAllAssetsData])

  function updateDraftPanels(updater: (panels: DashboardPanelRecord[]) => DashboardPanelRecord[]) {
    setSaveError(null)
    setSaveState('idle')
    setDraftDashboard((current) => {
      const base = current ?? dashboardQuery.data
      if (!base) {
        return current
      }
      const next = {
        ...base,
        panels: normalizeDashboardPanels(updater(base.panels)),
      }
      draftRef.current = next
      dirtyRef.current = true
      return next
    })
  }

  async function persistDashboardDraft() {
    if (saveInFlightRef.current || !dirtyRef.current) {
      return
    }
    const currentDraft = draftRef.current
    if (!currentDraft) {
      return
    }
    saveInFlightRef.current = true
    setSaveError(null)
    setSaveState('saving')

    try {
      const baseDashboard = await loadDashboardBase(queryClient, dashboardId, dashboardQuery.data)
      let savedDashboard: DashboardRecord
      try {
        savedDashboard = await patchDashboard(dashboardId, {
          dashboard_version: baseDashboard.version,
          title: baseDashboard.title,
          sources: baseDashboard.sources,
          panels: currentDraft.panels,
        })
      } catch (error) {
        const conflictDashboard = dashboardConflictDashboard(error)
        if (!conflictDashboard) {
          throw error
        }
        savedDashboard = await patchDashboard(dashboardId, {
          dashboard_version: conflictDashboard.version,
          title: conflictDashboard.title,
          sources: conflictDashboard.sources,
          panels: currentDraft.panels,
        })
      }
      const nextDashboard = {
        ...savedDashboard,
        panels: mergeSavedDashboardPanels(savedDashboard.panels, currentDraft.panels),
      }
      queryClient.setQueryData(['dashboard', dashboardId], nextDashboard)
      if (draftRef.current === currentDraft) {
        dirtyRef.current = false
        draftRef.current = nextDashboard
        setDraftDashboard(nextDashboard)
        setSaveState('saved')
      }
    } catch (error) {
      setSaveState('idle')
      setSaveError(error instanceof Error ? error.message : 'Could not save dashboard changes.')
    } finally {
      saveInFlightRef.current = false
      if (dirtyRef.current && draftRef.current !== currentDraft) {
        void persistDashboardDraft()
      }
    }
  }

  function handlePanelLinkClick(event: MouseEvent<HTMLAnchorElement>, panelId: string) {
    event.preventDefault()
    const sectionId = dashboardPanelDomId(panelId)
    const target = document.getElementById(sectionId)
    if (!target) {
      return
    }
    target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    window.history.replaceState(null, '', dashboardPanelHref(dashboardId, panelId))
    event.currentTarget.blur()
  }

  return (
    <div className="assets-page-shell dashboard-page-shell">
      <div className="canvas-underlay" />

      {dashboard ? (
        <DashboardSidebar panels={orderedPanels} assetsByLogicalId={assetsByLogicalId} panelHref={(panelId) => dashboardPanelHref(dashboardId, panelId)} onPanelNavigate={handlePanelLinkClick} onPanelsChange={updateDraftPanels} />
      ) : null}

      <div className="assets-page dashboard-page">
        <header className="panel assets-page-header">
          <div>
            <p className="eyebrow">Saved dashboard</p>
            <h1>{dashboard?.title ?? dashboardId}</h1>
            <p className="assets-page-subtitle">
              {projectTitle || 'BulletJournal'}
              {' · '}
              <code>{dashboardId}</code>
            </p>
          </div>
          <div className="assets-page-header-actions">
            <div className={`assets-page-sync-pill is-${saveState}`}>
              {saveState === 'saving' ? 'Saving changes' : saveState === 'saved' ? 'All changes saved' : 'Live dashboard'}
            </div>
            <div className="assets-page-stat">
              <strong>{dashboard?.sources.length ?? 0}</strong>
              <span>{(dashboard?.sources.length ?? 0) === 1 ? 'source' : 'sources'}</span>
            </div>
            <div className="assets-page-stat">
              <strong>{dashboard?.panels.length ?? 0}</strong>
              <span>{(dashboard?.panels.length ?? 0) === 1 ? 'panel' : 'panels'}</span>
            </div>
          </div>
        </header>

        {saveError ? (
          <div className="panel assets-empty-state error">
            <h2>Could not save dashboard changes</h2>
            <p>{saveError}</p>
          </div>
        ) : null}

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

        {!dashboardQuery.isLoading && !dashboardQuery.isError && assetsLoading ? (
          <div className="panel assets-empty-state">
            <h2>Loading dashboard assets</h2>
            <p>Fetching assets for the configured notebook sources.</p>
          </div>
        ) : null}

        {assetsError ? (
          <div className="panel assets-empty-state error">
            <h2>Could not load dashboard assets</h2>
            <p>{assetsError instanceof Error ? assetsError.message : 'Unknown error.'}</p>
          </div>
        ) : null}

        <div className="assets-panel-list">
          {!dashboardQuery.isLoading && !dashboardQuery.isError && !visiblePanels.length ? (
            <div className="panel assets-empty-state">
              <h2>No visible panels</h2>
              <p>This dashboard does not currently expose any visible asset panels.</p>
            </div>
          ) : null}

          {visiblePanels.map(({ panel, asset }) => asset ? (
            <AssetPanel
              key={panel.panel_id}
              sectionId={dashboardPanelDomId(panel.panel_id)}
              panelId={panel.panel_id}
              nodeId={panel.node_id}
              asset={asset}
              viewerMode="dashboard"
              persistedState={{
                modifier_overrides: panel.modifier_overrides,
                override_schema_hash: panel.override_schema_hash,
              }}
              panelHeight={panel.panel_height}
              onPersistedStateChange={(state: PersistedAssetPanelState) => {
                updateDraftPanels((panels) => panels.map((entry) => entry.panel_id === panel.panel_id ? {
                  ...entry,
                  modifier_overrides: state.modifier_overrides,
                  override_schema_hash: state.override_schema_hash,
                } : entry))
              }}
              onPanelHeightChange={(panelHeight) => {
                updateDraftPanels((panels) => panels.map((entry) => entry.panel_id === panel.panel_id ? {
                  ...entry,
                  panel_height: panelHeight,
                } : entry))
              }}
            />
          ) : (
            <section id={dashboardPanelDomId(panel.panel_id)} key={panel.panel_id} className="panel asset-panel-card">
              <div className="asset-panel-header">
                <div className="asset-panel-heading">
                  <div className="asset-panel-title-row">
                    <span className="asset-state-bubble is-pending" aria-hidden="true" />
                    <h2>{panel.panel_id}</h2>
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

async function loadDashboardBase(
  queryClient: QueryClient,
  dashboardId: string,
  fallbackDashboard: DashboardRecord | undefined,
): Promise<DashboardRecord> {
  const cached = queryClient.getQueryData<DashboardRecord>(['dashboard', dashboardId])
  if (cached) {
    return cached
  }
  if (fallbackDashboard) {
    return fallbackDashboard
  }
  return getDashboard(dashboardId)
}

function dashboardConflictDashboard(error: unknown): DashboardRecord | null {
  if (!(error instanceof ApiError) || error.status !== 409 || !error.payload || typeof error.payload !== 'object') {
    return null
  }
  const record = error.payload as { dashboard?: unknown }
  return record.dashboard && typeof record.dashboard === 'object' ? record.dashboard as DashboardRecord : null
}


function reconcileDashboardPanels(
  dashboard: DashboardRecord,
  assetsByNode: Record<string, AssetRecord[]>,
): DashboardPanelRecord[] {
  const existingPanels = normalizeDashboardPanels(dashboard.panels)
  const existingPanelsByLogicalId = new Map<string, DashboardPanelRecord>(
    existingPanels.map((panel) => [`${panel.node_id}/${panel.asset_name}`, panel] as const),
  )
  const availableAssetsByLogicalId = new Map<string, { nodeId: string, asset: AssetRecord }>()
  const discoveredLogicalIds: string[] = []
  for (const source of dashboard.sources) {
    for (const asset of assetsByNode[source.node_id] ?? []) {
      const logicalId = `${source.node_id}/${asset.asset_name}`
      if (!availableAssetsByLogicalId.has(logicalId)) {
        discoveredLogicalIds.push(logicalId)
      }
      availableAssetsByLogicalId.set(logicalId, { nodeId: source.node_id, asset })
    }
  }
  const nextPanels: DashboardPanelRecord[] = []

  for (const existingPanel of existingPanels) {
    const logicalId = `${existingPanel.node_id}/${existingPanel.asset_name}`
    const available = availableAssetsByLogicalId.get(logicalId)
    if (!available) {
      continue
    }
    nextPanels.push({
      panel_id: logicalId,
      node_id: available.nodeId,
      asset_name: available.asset.asset_name,
      visible: existingPanel.visible,
      position: nextPanels.length,
      panel_height: existingPanel.panel_height,
      modifier_overrides: existingPanel.modifier_overrides,
      override_schema_hash: existingPanel.override_schema_hash ?? available.asset.override_schema_hash,
    })
  }

  for (const logicalId of discoveredLogicalIds) {
    if (existingPanelsByLogicalId.has(logicalId)) {
      continue
    }
    const available = availableAssetsByLogicalId.get(logicalId)
    if (!available) {
      continue
    }
    nextPanels.push({
      panel_id: logicalId,
      node_id: available.nodeId,
      asset_name: available.asset.asset_name,
      visible: true,
      position: nextPanels.length,
      panel_height: null,
      modifier_overrides: {},
      override_schema_hash: available.asset.override_schema_hash,
    })
  }

  return normalizeDashboardPanels(nextPanels)
}

function dashboardPanelsKey(panels: DashboardPanelRecord[]): string {
  return JSON.stringify(
    normalizeDashboardPanels(panels).map((panel) => ({
      panel_id: panel.panel_id,
      node_id: panel.node_id,
      asset_name: panel.asset_name,
      visible: panel.visible,
      position: panel.position,
      panel_height: panel.panel_height,
      modifier_overrides: panel.modifier_overrides,
      override_schema_hash: panel.override_schema_hash,
    })),
  )
}

function mergeSavedDashboardPanels(
  savedPanels: DashboardPanelRecord[],
  currentPanels: DashboardPanelRecord[],
): DashboardPanelRecord[] {
  const orderedCurrentPanels = normalizeDashboardPanels(currentPanels)
  const orderedSavedPanels = normalizeDashboardPanels(savedPanels)
  const savedPanelsById = new Map<string, DashboardPanelRecord>(
    orderedSavedPanels.map((panel) => [panel.panel_id, panel] as const),
  )
  const currentPanelIds = new Set(orderedCurrentPanels.map((panel) => panel.panel_id))
  const nextPanels: DashboardPanelRecord[] = []

  for (const currentPanel of orderedCurrentPanels) {
    const savedPanel = savedPanelsById.get(currentPanel.panel_id)
    if (!savedPanel) {
      continue
    }
    nextPanels.push({ ...savedPanel, position: nextPanels.length })
  }

  for (const savedPanel of orderedSavedPanels) {
    if (currentPanelIds.has(savedPanel.panel_id)) {
      continue
    }
    nextPanels.push({ ...savedPanel, position: nextPanels.length })
  }

  return nextPanels
}

function dashboardPanelDomId(panelId: string): string {
  return `dashboard-panel-${panelId.replace(/[^a-zA-Z0-9_-]+/g, '-')}`
}

function dashboardPanelHref(dashboardId: string, panelId: string): string {
  return appUrl(`/dashboards/${encodeURIComponent(dashboardId)}#${dashboardPanelDomId(panelId)}`)
}
