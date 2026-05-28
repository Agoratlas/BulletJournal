import { SimpleMarkdown } from '../../components/SimpleMarkdown'
import { AssetPanelFrame } from '../shared/layout'
import type { SimpleAssetPanelProps } from '../shared/types'

export function MarkdownAssetPanel({ asset, panelInfo, sectionId }: SimpleAssetPanelProps) {
  const markdownText = typeof asset.definition?.markdown_text === 'string' ? asset.definition.markdown_text : null

  if (!markdownText) {
    return (
      <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId}>
        <div className="asset-panel-placeholder">
          <p>This Markdown asset is missing its text payload.</p>
        </div>
      </AssetPanelFrame>
    )
  }

  return (
    <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId}>
      <div className="asset-markdown-panel">
        <SimpleMarkdown text={markdownText} />
      </div>
    </AssetPanelFrame>
  )
}
