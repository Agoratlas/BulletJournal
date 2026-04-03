import type { ArtifactRecord } from '../lib/types'
import { formatBytes, formatTimestamp } from '../lib/helpers'
import { DATAFRAME_CSV_DOWNLOAD_MAX_BYTES, artifactEndpoint } from '../lib/appHelpers'
import { ArtifactPreviewPanel } from './ArtifactPreview'
import { Download, Info } from './Icons'

export function ArtifactCard({ artifact }: { artifact: ArtifactRecord }) {
  const downloadHref = artifactEndpoint(artifact, 'download')
  const imageSrc = artifact.preview?.kind === 'file' && artifact.preview.mime_type?.startsWith('image/')
    ? artifactEndpoint(artifact, 'content')
    : null
  const isDataFrame = artifact.data_type === 'pandas.DataFrame'
  const canDownloadCsv = isDataFrame && (artifact.size_bytes ?? 0) <= DATAFRAME_CSV_DOWNLOAD_MAX_BYTES
  const csvDisabledReason = canDownloadCsv ? null : 'CSV export is limited to DataFrame artifacts up to 100 MB.'
  const csvDownloadHref = `${downloadHref}?format=csv`
  const defaultDownloadLabel = artifact.extension?.toLowerCase() ?? 'file'

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
          {isDataFrame ? (
            <>
              <a className="secondary link-button artifact-download-button" href={downloadHref}>
                <Download width={16} height={16} />
                .parquet
              </a>
              <span className="artifact-download-tooltip-shell" title={csvDisabledReason ?? undefined}>
                <a
                  className={`secondary link-button artifact-download-button${canDownloadCsv ? '' : ' disabled'}`}
                  href={canDownloadCsv ? csvDownloadHref : undefined}
                  aria-disabled={!canDownloadCsv}
                  onClick={(event) => {
                    if (!canDownloadCsv) {
                      event.preventDefault()
                    }
                  }}
                >
                  <Download width={16} height={16} />
                  .csv
                </a>
                {!canDownloadCsv ? (
                  <span className="artifact-download-help" tabIndex={0} aria-label={csvDisabledReason ?? undefined}>
                    <Info width={14} height={14} />
                    <span className="artifact-tooltip">{csvDisabledReason}</span>
                  </span>
                ) : null}
              </span>
            </>
          ) : (
            <a className="secondary link-button artifact-download-button" href={downloadHref}>
              <Download width={16} height={16} />
              {defaultDownloadLabel}
            </a>
          )}
        </div>
      </div>
      <ArtifactPreviewPanel preview={artifact.preview} imageSrc={imageSrc} />
      <div className="artifact-meta-grid">
        <span>Storage: {artifact.storage_kind ?? 'n/a'}</span>
        <span>Lineage: {artifact.lineage_mode ?? 'n/a'}</span>
        <span>Created: {formatTimestamp(artifact.created_at)}</span>
        <span>Size: {formatBytes(artifact.size_bytes)}</span>
      </div>
    </article>
  )
}
