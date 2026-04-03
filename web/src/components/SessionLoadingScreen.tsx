import { X } from './Icons'

export function SessionLoadingScreen({
  sessionId,
  nodeId,
  onCancel,
}: {
  sessionId: string
  nodeId: string
  onCancel: () => void
}) {
  return (
    <div className="session-splash">
      <div className="session-splash-card">
        <p className="eyebrow">Preparing editor</p>
        <h1>Launching Marimo</h1>
        <p className="subhead">
          Waiting for the local editor to become available for `{nodeId}`.
        </p>
        <div className="stack-list subtle">
          <div><span>Session</span><strong>{sessionId}</strong></div>
        </div>
        <div className="spinner" />
        <button className="ghost-button modal-close-button" onClick={() => {
          onCancel()
          window.close()
        }} aria-label="Close loading screen"><X width={18} height={18} /></button>
      </div>
    </div>
  )
}
