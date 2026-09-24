import { useQuery } from '@tanstack/react-query'

import { listNodeAssets } from '../lib/api'
import { AssetPanel } from './AssetPanel'

export function NotebookAssetPanels({ nodeId }: { nodeId: string }) {
  const assetsQuery = useQuery({
    queryKey: ['node-assets', nodeId],
    queryFn: () => listNodeAssets(nodeId),
    retry: false,
  })
  const assets = assetsQuery.data ?? []

  if (assetsQuery.isLoading) {
    return <div className="assets-empty-state"><h2>Loading assets</h2><p>Fetching the current notebook asset heads.</p></div>
  }
  if (assetsQuery.isError) {
    return <div className="assets-empty-state error"><h2>Could not load assets</h2><p>{assetsQuery.error instanceof Error ? assetsQuery.error.message : 'Unknown error.'}</p></div>
  }
  if (assets.length === 0) {
    return <div className="assets-empty-state"><h2>No assets declared</h2><p>This notebook has not declared any assets with <code>assets.push(...)</code>.</p></div>
  }

  return (
    <div className="assets-panel-list">
      {assets.map((asset) => (
        <AssetPanel
          key={`${asset.node_id}/${asset.asset_name}`}
          nodeId={nodeId}
          asset={asset}
          viewerMode="notebook"
        />
      ))}
    </div>
  )
}
