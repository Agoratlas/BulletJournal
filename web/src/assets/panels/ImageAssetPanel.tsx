import { useEffect, useState } from 'react'

import { appUrl } from '../../lib/api'
import { AssetPanelFrame } from '../shared/layout'
import type { AssetPanelPrepareTarget, SimpleAssetPanelProps } from '../shared/types'

type ImageAssetPanelProps = SimpleAssetPanelProps & { target: AssetPanelPrepareTarget }

export function ImageAssetPanel({ asset, target, panelInfo, onReadyStateChange, sectionId, frameVariant }: ImageAssetPanelProps) {
  const query = new URLSearchParams({ asset_version_id: String(asset.current_asset_version_id) })
  const childName = target.panelContext?.collection_child_name
  if (typeof childName === 'string') {
    query.set('collection_child_name', childName)
  }
  const imageUrl = asset.current_asset_version_id !== null
    ? appUrl(`/api/v1/assets/${encodeURIComponent(target.nodeId)}/${encodeURIComponent(target.assetName)}/content?${query}`)
    : null
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)

  useEffect(() => {
    onReadyStateChange?.(imageUrl === null || loadedUrl === imageUrl || failedUrl === imageUrl)
  }, [imageUrl, loadedUrl, failedUrl, onReadyStateChange])

  return (
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId} frameVariant={frameVariant} showExportActions>
      {!imageUrl || failedUrl === imageUrl ? (
        <div className="asset-panel-placeholder"><p>This image could not be loaded.</p></div>
      ) : (
        <div className="asset-image-panel">
          <img
            className="asset-image"
            src={imageUrl}
            alt={typeof asset.definition?.alt === 'string' ? asset.definition.alt : asset.title || asset.asset_name}
            onLoad={() => setLoadedUrl(imageUrl)}
            onError={() => setFailedUrl(imageUrl)}
          />
          <a href={imageUrl} download>Download image</a>
        </div>
      )}
    </AssetPanelFrame>
  )
}
