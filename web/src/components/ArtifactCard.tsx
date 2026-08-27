import type { ArtifactRecord, AssetRecord } from '../lib/types'
import { formatBytes, formatTimestamp } from '../lib/helpers'
import { DATAFRAME_CSV_DOWNLOAD_MAX_BYTES, artifactEndpoint } from '../lib/appHelpers'
import { ArtifactPreviewPanel } from './ArtifactPreview'
import { Download } from './Icons'
import { DataFrameAssetPanel } from '../assets/panels/DataFrameAssetPanel'
import { prepareArtifactDataFrame } from '../lib/api'

export function ArtifactCard({ artifact }: { artifact: ArtifactRecord }) {
  const downloadHref = artifactEndpoint(artifact, 'download')
  const imageSrc = artifact.preview?.kind === 'file' && artifact.preview.mime_type?.startsWith('image/')
    && artifact.preview.image_inline
    ? `${artifactEndpoint(artifact, 'content')}?version=${encodeURIComponent(artifact.artifact_hash ?? String(artifact.current_version_id ?? ''))}`
    : null
  const isDataFrame = artifact.data_type === 'pandas.DataFrame'
  const canDownloadCsv = isDataFrame && (artifact.size_bytes ?? 0) <= DATAFRAME_CSV_DOWNLOAD_MAX_BYTES
  const csvDisabledReason = canDownloadCsv ? null : 'Tabular export is limited to DataFrame artifacts up to 100 MB.'
  const csvDownloadHref = `${downloadHref}?format=csv`
  const xlsxDownloadHref = `${downloadHref}?format=xlsx`
  const defaultDownloadLabel = artifact.extension?.toLowerCase() ?? 'file'
  const downloadOptions = isDataFrame
    ? [
        { label: 'Download Parquet', href: downloadHref },
        ...(canDownloadCsv ? [
          { label: 'Download CSV', href: csvDownloadHref },
          { label: 'Download Excel', href: xlsxDownloadHref },
        ] : []),
      ]
    : [{ label: `Download ${defaultDownloadLabel}`, href: downloadHref }]
  const dataFrameAsset: AssetRecord | null = isDataFrame && artifact.current_version_id !== null ? {
    node_id: artifact.node_id,
    asset_name: artifact.artifact_name,
    title: null,
    description: null,
    declared_asset_type: 'dataframe',
    declaration_index: null,
    current_asset_version_id: artifact.current_version_id,
    state: artifact.state,
    asset_type: 'dataframe',
    interactive: true,
    source_hash: artifact.source_hash,
    upstream_code_hash: artifact.upstream_code_hash,
    upstream_data_hash: artifact.upstream_data_hash,
    run_id: artifact.run_id,
    lineage_mode: artifact.lineage_mode,
    definition: {
      row_count: artifact.preview?.kind === 'dataframe' ? artifact.preview.rows : 0,
      table_columns: artifact.preview?.kind === 'dataframe' ? artifact.preview.column_names : [],
    },
    modifier_schema: [],
    default_modifiers: { page: { index: 0, size: 25 }, sort: [], filters: [], highlights: [] },
    override_schema_hash: null,
    warnings: artifact.warnings,
    created_at: artifact.created_at,
    objects: [],
  } : null

  return (
    <article className={`artifact-card state-${artifact.state}`}>
      <div className="artifact-head">
        <div className="artifact-title-block">
          <div className="artifact-title-row">
            <strong>{artifact.node_id}/{artifact.artifact_name}</strong>
            <span className={`artifact-state-label ${artifact.state}`}>{artifact.state}</span>
          </div>
          <span>{artifact.data_type ?? 'unknown'}</span>
        </div>
        <div className="artifact-download-actions">
          {downloadOptions.length === 1 ? (
            <a className="secondary link-button artifact-download-button" href={downloadHref} aria-label={downloadOptions[0].label} title={downloadOptions[0].label}>
              <Download width={16} height={16} />
            </a>
          ) : (
            <details className="artifact-download-menu">
              <summary className="secondary artifact-download-button" aria-label="Download options" title="Download options">
                <Download width={16} height={16} />
              </summary>
              <div className="artifact-download-popover">
                {downloadOptions.map((option) => <a key={option.label} href={option.href}>{option.label}</a>)}
                {!canDownloadCsv ? <p>{csvDisabledReason}</p> : null}
              </div>
            </details>
          )}
        </div>
      </div>
      {dataFrameAsset ? (
        <DataFrameAssetPanel
          nodeId={artifact.node_id}
          asset={dataFrameAsset}
          viewerMode="notebook"
          panelInfo={{
            panelId: `${artifact.node_id}/${artifact.artifact_name}`,
            assetName: artifact.artifact_name,
            assetTitle: null,
            createdLabel: formatTimestamp(artifact.created_at),
            runtimeType: 'dataframe',
          }}
          persistedState={null}
          prepare={prepareArtifactDataFrame}
          frameVariant="inline"
        />
      ) : <ArtifactPreviewPanel preview={artifact.preview} imageSrc={imageSrc} />}
      <div className="artifact-meta-grid">
        <span>Storage: {artifact.storage_kind ?? 'n/a'}</span>
        <span>Lineage: {artifact.lineage_mode ?? 'n/a'}</span>
        <span>Created: {formatTimestamp(artifact.created_at)}</span>
        <span>Size: {formatBytes(artifact.size_bytes)}</span>
      </div>
    </article>
  )
}
