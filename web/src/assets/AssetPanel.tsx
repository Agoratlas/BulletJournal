import { formatTimestamp } from '../lib/helpers'
import type { AssetPanelProps } from './shared/types'
import { AssetPanelFrame } from './shared/layout'
import { BarChartAssetPanel } from './panels/BarChartAssetPanel'
import { DataFrameAssetPanel } from './panels/DataFrameAssetPanel'
import { HistogramAssetPanel } from './panels/HistogramAssetPanel'
import { MarkdownAssetPanel } from './panels/MarkdownAssetPanel'
import { PieChartAssetPanel } from './panels/PieChartAssetPanel'
import { ScatterPlotAssetPanel } from './panels/ScatterPlotAssetPanel'

export type { PersistedAssetPanelState } from './shared/types'

export function AssetPanel({
  panelId,
  nodeId,
  asset,
  persistedState,
  onPersistedStateChange,
  panelHeight,
  onPanelHeightChange,
  sectionId,
}: AssetPanelProps) {
  const createdLabel = asset.created_at ? formatTimestamp(asset.created_at) : 'Not produced yet'
  const runtimeType = asset.asset_type ?? asset.declared_asset_type ?? 'unknown'
  const resolvedPanelId = panelId ?? `${nodeId}/${asset.asset_name}`
  const panelInfo = {
    panelId: resolvedPanelId,
    assetName: asset.asset_name,
    assetTitle: asset.title,
    createdLabel,
    runtimeType,
  }
  const frameProps = {
    asset,
    panelInfo,
    sectionId,
  }

  if (asset.current_asset_version_id === null) {
    return (
      <AssetPanelFrame {...frameProps}>
        <div className="asset-panel-placeholder">
          <p>This asset is declared but has not been produced yet.</p>
        </div>
      </AssetPanelFrame>
    )
  }

  if (asset.asset_type === 'markdown') {
    return <MarkdownAssetPanel asset={asset} panelInfo={panelInfo} sectionId={sectionId} />
  }

  if (asset.asset_type === 'dataframe') {
    return (
      <DataFrameAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        sectionId={sectionId}
      />
    )
  }

  if (asset.asset_type === 'bar_chart') {
    return (
      <BarChartAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        sectionId={sectionId}
      />
    )
  }

  if (asset.asset_type === 'histogram' || asset.asset_type === 'time_histogram') {
    return (
      <HistogramAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        sectionId={sectionId}
      />
    )
  }

  if (asset.asset_type === 'pie_chart') {
    return (
      <PieChartAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        sectionId={sectionId}
      />
    )
  }

  if (asset.asset_type === 'scatter_plot') {
    return (
      <ScatterPlotAssetPanel
        nodeId={nodeId}
        asset={asset}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        sectionId={sectionId}
      />
    )
  }

  return (
    <AssetPanelFrame {...frameProps}>
      <div className="asset-panel-placeholder">
        <p>Asset type <code>{runtimeType}</code> is not supported by this viewer yet.</p>
      </div>
    </AssetPanelFrame>
  )
}
