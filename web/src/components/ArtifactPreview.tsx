import type { ArtifactPreview } from '../lib/types'
import { formatBytes } from '../lib/helpers'

type ArtifactPreviewProps = {
  preview: ArtifactPreview | null
  imageSrc?: string | null
}

export function ArtifactPreviewPanel({ preview, imageSrc = null }: ArtifactPreviewProps) {
  if (!preview) {
    return <div className="artifact-preview empty">No preview available.</div>
  }

  if (preview.kind === 'empty') {
    return <div className="artifact-preview empty">Empty artifact.</div>
  }

  if (preview.kind === 'simple') {
    return <pre className="artifact-preview code-block">{formatSimplePreview(preview.repr)}</pre>
  }

  if (preview.kind === 'object') {
    return <pre className="artifact-preview code-block">{preview.repr}</pre>
  }

  if (preview.kind === 'series') {
    return (
      <div className="artifact-preview meta-preview">
        <div>{preview.rows} rows</div>
        <pre className="code-block">{JSON.stringify(preview.sample, null, 2)}</pre>
      </div>
    )
  }

  if (preview.kind === 'dataframe') {
    return (
      <div className="artifact-preview">
        <div className="preview-stats">
          <span>{preview.rows} rows x {preview.columns} cols</span>
        </div>
        <div className="table-wrap">
          <table className="preview-table">
            <thead>
              <tr>
                {preview.column_names.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.sample.map((row, index) => (
                <tr key={index}>
                  {preview.column_names.map((column) => (
                    <td key={column}>{String(row[column] ?? '')}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  if (preview.kind === 'file' && preview.mime_type?.startsWith('image/') && imageSrc) {
    return (
      <div className="artifact-preview image-preview-shell">
        <img
          className="artifact-preview-image"
          src={imageSrc}
          alt={preview.original_filename ?? preview.filename ?? 'Artifact preview'}
        />
        <div className="meta-preview artifact-file-meta">
          <div>{preview.original_filename ?? preview.filename ?? 'Image artifact'}</div>
          <div>{preview.mime_type}</div>
          <div>{formatBytes(preview.size_bytes)}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="artifact-preview meta-preview">
      <div>{preview.original_filename ?? preview.filename ?? 'File artifact'}</div>
      <div>{preview.mime_type ?? 'Unknown type'}</div>
      <div>{preview.extension ?? 'No extension'}</div>
      <div>{formatBytes(preview.size_bytes)}</div>
    </div>
  )
}

function formatSimplePreview(value: string) {
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    try {
      return JSON.parse(value)
    } catch {
      return value.slice(1, -1).replace(/\\n/g, '\n').replace(/\\t/g, '\t')
    }
  }
  return value.replace(/\\n/g, '\n')
}
