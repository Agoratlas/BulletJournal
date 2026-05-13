import type { AppNotice } from '../appTypes'
import { formatTimestamp } from '../lib/helpers'
import { editorSessionDetails } from '../lib/appHelpers'
import { SimpleMarkdown } from './SimpleMarkdown'

export function NoticeOverlay({
  notices,
  onDismiss,
  onFocusNotice,
  onHoverNoticeNode,
  onOpenEditor,
  onKillEditor,
}: {
  notices: AppNotice[]
  onDismiss: (notice: AppNotice) => void
  onFocusNotice: (notice: AppNotice) => void
  onHoverNoticeNode: (nodeId: string | null) => void
  onOpenEditor: (notice: AppNotice) => void
  onKillEditor: (notice: AppNotice) => void
}) {
  if (!notices.length) {
    return null
  }

  return (
    <div className="notice-overlay" aria-live="polite" aria-label="Errors and warnings">
      {notices.map((notice) => {
        const dismissible = notice.severity === 'warning' || notice.origin === 'client' || notice.code === 'run_failed'
        const editorDetails = notice.code === 'editor_already_open' ? editorSessionDetails(notice.details) : null
        return (
          <article
            key={notice.issue_id}
            className={`notice-card ${notice.severity} ${notice.node_id ? 'linked-to-node' : ''}`}
            onClick={() => onFocusNotice(notice)}
            onPointerEnter={() => onHoverNoticeNode(notice.node_id)}
            onPointerLeave={() => onHoverNoticeNode(null)}
          >
            <div className="notice-card-head">
              <div className="notice-card-copy">
                <p className="notice-label">{notice.severity === 'error' ? 'Error' : 'Warning'}</p>
                <strong>{notice.code}</strong>
              </div>
              {dismissible ? <button className="secondary small" onClick={(event) => {
                event.stopPropagation()
                onDismiss(notice)
              }}>Dismiss</button> : null}
            </div>
            <SimpleMarkdown className="notice-message" text={notice.message} />
            <div className="notice-card-foot">
              <span>{formatTimestamp(notice.created_at)}</span>
              <div className="notice-card-actions">
                {editorDetails ? <button className="secondary small" onClick={(event) => {
                  event.stopPropagation()
                  onOpenEditor(notice)
                }}>Open editor</button> : null}
                {editorDetails ? <button className="secondary small" onClick={(event) => {
                  event.stopPropagation()
                  onKillEditor(notice)
                }}>Kill editor</button> : null}
              </div>
            </div>
          </article>
        )
      })}
    </div>
  )
}
