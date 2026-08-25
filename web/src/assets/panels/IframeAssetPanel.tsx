import { useEffect, useState } from 'react'

import { AssetPanelFrame } from '../shared/layout'
import type { SimpleAssetPanelProps } from '../shared/types'

export function IframeAssetPanel({ asset, panelInfo, viewerMode = 'notebook', onReadyStateChange, sectionId, frameVariant }: SimpleAssetPanelProps) {
  const iframeUrl = typeof asset.definition?.iframe_url === 'string' ? asset.definition.iframe_url : null
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null)

  useEffect(() => {
    onReadyStateChange?.(iframeUrl === null || loadedUrl === iframeUrl)
  }, [iframeUrl, loadedUrl, onReadyStateChange])

  if (!iframeUrl) {
    return (
      <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId} frameVariant={frameVariant} showExportActions={viewerMode === 'dashboard'}>
        <div className="asset-panel-placeholder">
          <p>This Iframe asset is missing its URL payload.</p>
        </div>
      </AssetPanelFrame>
    )
  }

  return (
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId} frameVariant={frameVariant} showExportActions={viewerMode === 'dashboard'}>
      <div className="asset-iframe-panel">
        <iframe
          className="asset-iframe-frame"
          src={iframeUrl}
          title={asset.title || asset.asset_name}
          loading="lazy"
          onLoad={() => setLoadedUrl(iframeUrl)}
          onError={() => setLoadedUrl(iframeUrl)}
          allowFullScreen
        />
      </div>
    </AssetPanelFrame>
  )
}
