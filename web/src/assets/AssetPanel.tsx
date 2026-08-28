import { useEffect } from 'react'

import { formatTimestamp } from '../lib/helpers'
import type { AssetPanelProps } from './shared/types'
import { AssetPanelFrame } from './shared/layout'
import { BarChartAssetPanel } from './panels/BarChartAssetPanel'
import { CollectionAssetPanel } from './panels/CollectionAssetPanel'
import { DataFrameAssetPanel } from './panels/DataFrameAssetPanel'
import { HistogramAssetPanel } from './panels/HistogramAssetPanel'
import { IframeAssetPanel } from './panels/IframeAssetPanel'
import { MarkdownAssetPanel } from './panels/MarkdownAssetPanel'
import { PieChartAssetPanel } from './panels/PieChartAssetPanel'
import { ScatterPlotAssetPanel } from './panels/ScatterPlotAssetPanel'

export type { PersistedAssetPanelState } from './shared/types'

export function AssetPanel({
  panelId,
  nodeId,
  asset,
  prepareTarget,
  viewerMode = 'notebook',
  persistedState,
  onPersistedStateChange,
  onReadyStateChange,
  panelHeight,
  onPanelHeightChange,
  isPanelResized,
  chartScale,
  minPanelHeight,
  sectionId,
  frameVariant,
}: AssetPanelProps) {
  const createdLabel = asset.created_at ? formatTimestamp(asset.created_at) : 'Not produced yet'
  const runtimeType = asset.asset_type ?? asset.declared_asset_type ?? 'unknown'
  const resolvedPanelId = panelId ?? `${nodeId}/${asset.asset_name}`
  const resolvedPrepareTarget = prepareTarget ?? {
    nodeId,
    assetName: asset.asset_name,
    panelContext: null,
  }
  const resolvedPanelIsResized = isPanelResized ?? isCustomPanelHeight(panelHeight)
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
    frameVariant,
  }
  const isUnsupportedAssetType = asset.current_asset_version_id !== null
    && asset.asset_type !== 'markdown'
    && asset.asset_type !== 'iframe'
    && asset.asset_type !== 'collection'
    && asset.asset_type !== 'dataframe'
    && asset.asset_type !== 'bar_chart'
    && asset.asset_type !== 'histogram'
    && asset.asset_type !== 'pie_chart'
    && asset.asset_type !== 'scatter_plot'

  useEffect(() => {
    if (asset.current_asset_version_id === null || isUnsupportedAssetType) {
      onReadyStateChange?.(true)
    }
  }, [asset.current_asset_version_id, isUnsupportedAssetType, onReadyStateChange])

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
    return <MarkdownAssetPanel asset={asset} panelInfo={panelInfo} viewerMode={viewerMode} onReadyStateChange={onReadyStateChange} sectionId={sectionId} frameVariant={frameVariant} />
  }

  if (asset.asset_type === 'iframe') {
    return <IframeAssetPanel asset={asset} panelInfo={panelInfo} viewerMode={viewerMode} onReadyStateChange={onReadyStateChange} sectionId={sectionId} frameVariant={frameVariant} />
  }

  if (asset.asset_type === 'collection') {
    return (
      <CollectionAssetPanel
        asset={asset}
        panelInfo={panelInfo}
        viewerMode={viewerMode}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        onReadyStateChange={onReadyStateChange}
        sectionId={sectionId}
        frameVariant={frameVariant}
      />
    )
  }

  if (asset.asset_type === 'dataframe') {
    return (
      <DataFrameAssetPanel
        nodeId={nodeId}
        asset={asset}
        prepareTarget={resolvedPrepareTarget}
        viewerMode={viewerMode}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        onReadyStateChange={onReadyStateChange}
        sectionId={sectionId}
        frameVariant={frameVariant}
      />
    )
  }

  if (asset.asset_type === 'bar_chart') {
    return (
      <BarChartAssetPanel
        nodeId={nodeId}
        asset={asset}
        prepareTarget={resolvedPrepareTarget}
        panelInfo={panelInfo}
        viewerMode={viewerMode}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        onReadyStateChange={onReadyStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        isPanelResized={resolvedPanelIsResized}
        chartScale={chartScale}
        minPanelHeight={minPanelHeight}
        sectionId={sectionId}
        frameVariant={frameVariant}
      />
    )
  }

  if (asset.asset_type === 'histogram') {
    return (
      <HistogramAssetPanel
        nodeId={nodeId}
        asset={asset}
        prepareTarget={resolvedPrepareTarget}
        panelInfo={panelInfo}
        viewerMode={viewerMode}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        onReadyStateChange={onReadyStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        isPanelResized={resolvedPanelIsResized}
        chartScale={chartScale}
        minPanelHeight={minPanelHeight}
        sectionId={sectionId}
        frameVariant={frameVariant}
      />
    )
  }

  if (asset.asset_type === 'pie_chart') {
    return (
      <PieChartAssetPanel
        nodeId={nodeId}
        asset={asset}
        prepareTarget={resolvedPrepareTarget}
        panelInfo={panelInfo}
        viewerMode={viewerMode}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        onReadyStateChange={onReadyStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        isPanelResized={resolvedPanelIsResized}
        chartScale={chartScale}
        minPanelHeight={minPanelHeight}
        sectionId={sectionId}
        frameVariant={frameVariant}
      />
    )
  }

  if (asset.asset_type === 'scatter_plot') {
    return (
      <ScatterPlotAssetPanel
        nodeId={nodeId}
        asset={asset}
        prepareTarget={resolvedPrepareTarget}
        viewerMode={viewerMode}
        panelInfo={panelInfo}
        persistedState={persistedState ?? null}
        onPersistedStateChange={onPersistedStateChange}
        onReadyStateChange={onReadyStateChange}
        panelHeight={panelHeight ?? null}
        onPanelHeightChange={onPanelHeightChange}
        isPanelResized={resolvedPanelIsResized}
        chartScale={chartScale}
        minPanelHeight={minPanelHeight}
        sectionId={sectionId}
        frameVariant={frameVariant}
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

function isCustomPanelHeight(panelHeight: number | null | undefined): boolean {
  if (panelHeight === null || panelHeight === undefined) {
    return false
  }
  return panelHeight !== 600
}
