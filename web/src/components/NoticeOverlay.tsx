import type { AppNotice } from '../appTypes'
import { formatTimestamp } from '../lib/helpers'
import { editorSessionDetails } from '../lib/appHelpers'

export function NoticeOverlay({
  notices,
  onDismiss,
  onOpenNode,
  onOpenEditor,
  onKillEditor,
}: {
  notices: AppNotice[]
  onDismiss: (notice: AppNotice) => void
  onOpenNode: (nodeId: string) => void
  onOpenEditor: (notice: AppNotice) => void
  onKillEditor: (notice: AppNotice) => void
}) {
  if (!notices.length) {
    return null
  }

  return (
    <div className="notice-overlay" aria-live="polite" aria-label="Errors and warnings">
      {notices.map((notice) => {
        const dismissible = notice.severity === 'warning' || notice.origin === 'client'
        const editorDetails = notice.code === 'editor_already_open' ? editorSessionDetails(notice.details) : null
        return (
          <article key={notice.issue_id} className={`notice-card ${notice.severity}`}>
            <div className="notice-card-head">
              <div className="notice-card-copy">
                <p className="notice-label">{notice.severity === 'error' ? 'Error' : 'Warning'}</p>
                <strong>{notice.code}</strong>
              </div>
              {dismissible ? <button className="secondary small" onClick={() => onDismiss(notice)}>Dismiss</button> : null}
            </div>
            <p className="notice-message">{notice.message}</p>
            <div className="notice-card-foot">
              <span>{formatTimestamp(notice.created_at)}</span>
              <div className="notice-card-actions">
                {notice.node_id ? (
                  <button className="secondary small" onClick={() => onOpenNode(notice.node_id as string)}>Open node</button>
                ) : null}
                {editorDetails ? <button className="secondary small" onClick={() => onOpenEditor(notice)}>Open editor</button> : null}
                {editorDetails ? <button className="secondary small" onClick={() => onKillEditor(notice)}>Kill editor</button> : null}
              </div>
            </div>
          </article>
        )
      })}
    </div>
  )
}
