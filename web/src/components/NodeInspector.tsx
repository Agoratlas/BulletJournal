import { useEffect, useMemo, useRef, useState } from 'react'

import { executionLogDownloadUrl, getExecutionLogs } from '../lib/api'
import { artifactFor, artifactIsEmpty, formatBytes, formatDurationSeconds, formatType, inputBindingSource, inputState, templateByRef } from '../lib/helpers'
import { frozenFileBlockMessage, normalizeNodeId } from '../lib/appHelpers'
import type { NodeActionItem } from '../appTypes'
import type { ExecutionLogSummary, NodeRecord, ProjectSnapshot } from '../lib/types'
import { ActionButtons } from './ActionButtons'
import { Download, Pencil } from './Icons'
import { TYPE_COLORS } from './PortLabel'
import { SimpleMarkdown } from './SimpleMarkdown'

function formatExecutionTimestamp(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  const parts = [date.getFullYear(), date.getMonth() + 1, date.getDate(), date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((part) => String(part).padStart(2, '0'))
  return `${parts[0]}-${parts[1]}-${parts[2]} ${parts[3]}:${parts[4]}:${parts[5]}`
}

function formatRelativeTimestamp(value: string, nowMs: number): string {
  const elapsedSeconds = Math.max(0, Math.floor((nowMs - Date.parse(value)) / 1000))
  if (!Number.isFinite(elapsedSeconds)) {
    return ''
  }
  if (elapsedSeconds < 60) return `${elapsedSeconds}s ago`
  if (elapsedSeconds < 3600) return `${Math.floor(elapsedSeconds / 60)}m ago`
  if (elapsedSeconds < 86400) return `${Math.floor(elapsedSeconds / 3600)}h ago`
  return `${Math.floor(elapsedSeconds / 86400)}d ago`
}

function ExecutionLogPanel({
  title,
  log,
  nodeId,
  filenameSuffix,
  running,
}: {
  title: string
  log: ExecutionLogSummary | null
  nodeId: string
  filenameSuffix: 'stdout' | 'stderr'
  running: boolean
}) {
  const baseBody = log?.text || (running ? 'Waiting for log output...' : 'No log output.')
  const body = log?.truncated ? `[log truncated]\n${baseBody}` : baseBody
  const sizeLabel = formatBytes(log?.size_bytes ?? 0)
  const disabled = (log?.size_bytes ?? 0) <= 0
  const logRef = useRef<HTMLPreElement | null>(null)
  const shouldFollowRef = useRef(true)

  useEffect(() => {
    shouldFollowRef.current = true
  }, [nodeId, filenameSuffix])

  useEffect(() => {
    const element = logRef.current
    if (!element || !shouldFollowRef.current) {
      return
    }
    element.scrollTop = element.scrollHeight
  }, [body])

  function handleScroll() {
    const element = logRef.current
    if (!element) {
      return
    }
    const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight
    shouldFollowRef.current = distanceFromBottom <= 24
  }

  return (
    <div className="inspector-block">
      <div className="panel-header-row execution-log-header">
        <h3>{title}</h3>
        <a
          className={`secondary small link-button execution-log-download-button${disabled ? ' disabled' : ''}`}
          href={disabled ? undefined : executionLogDownloadUrl(nodeId, filenameSuffix)}
          aria-disabled={disabled}
          onClick={(event) => {
            if (disabled) {
              event.preventDefault()
            }
          }}
        >
          <Download className="execution-log-download-icon" width={16} height={16} />
          <span className="execution-log-download-label">{sizeLabel}</span>
        </a>
      </div>
      <pre
        ref={logRef}
        className="code-block docs-block execution-log-block execution-log-terminal"
        onScroll={handleScroll}
      >
        {body}
      </pre>
    </div>
  )
}

export function NodeInspector({
  snapshot,
  node,
  serverNowMs,
  serverNowClientAnchorMs,
  nodeActions,
  assetCounts,
  onUploadFile,
  existingNodeIds,
  onRenameNode,
  nodeIdEditDisabledReason = null,
}: {
  snapshot: ProjectSnapshot
  node: NodeRecord
  serverNowMs: number
  serverNowClientAnchorMs: number
  nodeActions: NodeActionItem[]
  assetCounts: { pending: number; stale: number; ready: number }
  onUploadFile: (nodeId: string, file: File) => Promise<void>
  existingNodeIds: string[]
  onRenameNode: (nodeId: string, payload: { nodeId: string; title: string }) => Promise<void>
  nodeIdEditDisabledReason?: string | null
}) {
  const constantArtifact = node.kind === 'constant' ? artifactFor(snapshot, node.id, node.ui?.artifact_name ?? 'value') ?? null : null
  const template = templateByRef(snapshot, node.template?.ref)
  const assetCount = assetCounts.pending + assetCounts.stale + assetCounts.ready
  const assetState = assetCount > 0 && assetCounts.ready === assetCount ? 'ready' : assetCounts.stale > 0 ? 'stale' : 'pending'
  const viewAssetsAction = nodeActions.find((action) => action.key === 'view-assets')
  const [now, setNow] = useState(() => Date.now())
  const [stdoutLog, setStdoutLog] = useState<ExecutionLogSummary | null>(() => node.execution_meta?.stdout ?? null)
  const [stderrLog, setStderrLog] = useState<ExecutionLogSummary | null>(() => node.execution_meta?.stderr ?? null)
  const [editingIdentity, setEditingIdentity] = useState(false)
  const [draftTitle, setDraftTitle] = useState(node.title)
  const [draftNodeId, setDraftNodeId] = useState(node.id)
  const [nodeIdTouched, setNodeIdTouched] = useState(false)
  const [renameBusy, setRenameBusy] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const isExecutionRunning = node.execution_meta?.status === 'running'
  const resolvedNodeId = useMemo(() => normalizeNodeId(draftNodeId), [draftNodeId])
  const titleError = !draftTitle.trim()
  const duplicateId = resolvedNodeId !== node.id && existingNodeIds.includes(resolvedNodeId)

  useEffect(() => {
    if (!isExecutionRunning) {
      return
    }
    const interval = window.setInterval(() => setNow(Date.now()), 100)
    return () => window.clearInterval(interval)
  }, [isExecutionRunning])

  useEffect(() => {
    setStdoutLog(node.execution_meta?.stdout ?? null)
    setStderrLog(node.execution_meta?.stderr ?? null)
  }, [node.execution_meta?.stderr, node.execution_meta?.stdout, node.id])

  useEffect(() => {
    setEditingIdentity(false)
    setDraftTitle(node.title)
    setDraftNodeId(node.id)
    setNodeIdTouched(false)
    setRenameBusy(false)
  }, [node.id, node.title])

  useEffect(() => {
    if (!isExecutionRunning) {
      return
    }
    let cancelled = false

    async function refreshLogs() {
      const result = await getExecutionLogs(node.id).catch(() => null)
      if (cancelled) {
        return
      }
      if (result) {
        setStdoutLog(result.stdout)
        setStderrLog(result.stderr)
      }
    }

    void refreshLogs()
    const interval = window.setInterval(() => {
      void refreshLogs()
    }, 3000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [isExecutionRunning, node.id])

  const runningDurationLabel = useMemo(() => {
    if (node.execution_meta?.status !== 'running') {
      return null
    }
    const startedAt = Date.parse(node.execution_meta.started_at)
    if (Number.isNaN(startedAt)) {
      return null
    }
    return formatDurationSeconds((serverNowMs + (now - serverNowClientAnchorMs) - startedAt) / 1000)
  }, [node.execution_meta, now, serverNowMs, serverNowClientAnchorMs])
  const displayedCurrentCell = node.execution_meta?.current_cell
    ? {
        nodeId: node.id,
        cell_number: node.execution_meta.current_cell.cell_number,
        total_cells: node.execution_meta.current_cell.total_cells,
        cell_code: node.execution_meta.current_cell.cell_code,
      }
    : null
  const executionCellNumber = node.execution_meta?.current_cell?.cell_number ?? node.execution_meta?.last_completed_cell_number ?? 0
  async function commitRename() {
    if (renameBusy) {
      return
    }
    if (titleError || !resolvedNodeId || duplicateId) {
      return
    }
    const nextTitle = draftTitle.trim()
    const nextNodeId = nodeIdEditDisabledReason ? node.id : resolvedNodeId
    const unchanged = nextNodeId === node.id && nextTitle === node.title
    if (unchanged) {
      setEditingIdentity(false)
      return
    }
    setRenameBusy(true)
    try {
      await onRenameNode(node.id, { nodeId: nextNodeId, title: nextTitle })
      setEditingIdentity(false)
    } finally {
      setRenameBusy(false)
    }
  }

  function cancelRename() {
    setEditingIdentity(false)
    setDraftTitle(node.title)
    setDraftNodeId(node.id)
    setNodeIdTouched(false)
    setRenameBusy(false)
  }

  return (
    <div className="inspector-stack">
      <div className="inspector-identity-section">
        <div className="inspector-identity">
          <div className="inspector-identity-copy">
            <strong>{node.title}</strong>
            <code>{node.id}</code>
          </div>
          <button type="button" className="secondary small-icon-pill" onClick={() => setEditingIdentity(true)} disabled={renameBusy} aria-label="Edit name and node ID">
            <Pencil width={16} height={16} />
          </button>
        </div>
        {editingIdentity ? (
          <form className="form-grid compact inspector-identity-form" onSubmit={(event) => { event.preventDefault(); void commitRename() }}>
            <label>
              <span>Name</span>
              <input value={draftTitle} onChange={(event) => {
                const nextTitle = event.target.value
                setDraftTitle(nextTitle)
                if (!nodeIdTouched && !nodeIdEditDisabledReason) setDraftNodeId(normalizeNodeId(nextTitle))
              }} placeholder="Block name" autoFocus />
              {titleError ? <span className="field-note error">Name is required.</span> : null}
            </label>
            <label>
              <span>Node ID</span>
              <input className={duplicateId || !resolvedNodeId ? 'invalid' : ''} value={draftNodeId} onChange={(event) => {
                setNodeIdTouched(true)
                setDraftNodeId(normalizeNodeId(event.target.value))
              }} placeholder="notebook_id" spellCheck={false} disabled={Boolean(nodeIdEditDisabledReason)} />
              {duplicateId ? <span className="field-note error">This ID is already used by another node.</span> : !resolvedNodeId ? <span className="field-note error">Node ID is required.</span> : nodeIdEditDisabledReason ? <span className="field-note">{nodeIdEditDisabledReason}</span> : <span className="field-note">Node IDs are stored as snake_case and used in graph references.</span>}
            </label>
            <div className="dialog-actions">
              <button type="button" className="secondary" onClick={cancelRename}>Cancel</button>
              <button type="submit" disabled={renameBusy || titleError || !resolvedNodeId || duplicateId}>{renameBusy ? 'Saving...' : 'Save'}</button>
            </div>
          </form>
        ) : null}
      </div>

      {node.execution_meta ? (
        <div className="inspector-block">
          <h3>Execution</h3>
          <div className={`execution-timeline status-${node.execution_meta.status}`}>
            <div className="execution-timeline-bar"><span /><span /></div>
            <div className="execution-timeline-copy">
              <div className="execution-timeline-endpoint"><span>Started</span> <strong>{formatRelativeTimestamp(node.execution_meta.started_at, now)}</strong> <time>{formatExecutionTimestamp(node.execution_meta.started_at)}</time></div>
              <div className="execution-timeline-duration">
                <span>Elapsed</span> <strong>{node.execution_meta.status === 'running' ? runningDurationLabel : typeof node.execution_meta.duration_seconds === 'number' ? formatDurationSeconds(node.execution_meta.duration_seconds) : '—'}</strong>
                {node.execution_meta.status === 'running' && node.execution_meta.total_cells ? <span> ({executionCellNumber}/{node.execution_meta.total_cells})</span> : null}
              </div>
              <div className="execution-timeline-endpoint">{node.execution_meta.status === 'succeeded' && node.execution_meta.ended_at ? <><span>Finished</span> <strong>{formatRelativeTimestamp(node.execution_meta.ended_at, now)}</strong> <time>{formatExecutionTimestamp(node.execution_meta.ended_at)}</time></> : <span className="execution-timeline-placeholder">—</span>}</div>
            </div>
          </div>
          {node.execution_meta.status === 'failed' && node.execution_meta.error ? <pre className="execution-error-block">{node.execution_meta.error}</pre> : null}
        </div>
      ) : null}

      {displayedCurrentCell ? (
        <div className="inspector-block">
          <h3>Current cell</h3>
          <div className="inspector-subblock">
            <strong>
              Cell {displayedCurrentCell.cell_number ?? '?'}
              /{displayedCurrentCell.total_cells ?? '?'}
            </strong>
            {displayedCurrentCell.cell_code ? <pre className="code-block docs-block">{displayedCurrentCell.cell_code}</pre> : null}
          </div>
        </div>
      ) : null}

      {isExecutionRunning || stdoutLog ? (
        <ExecutionLogPanel
          title="Stdout"
          log={stdoutLog}
          nodeId={node.id}
          filenameSuffix="stdout"
          running={isExecutionRunning}
        />
      ) : null}

      {isExecutionRunning || stderrLog ? (
        <ExecutionLogPanel
          title="Stderr"
          log={stderrLog}
          nodeId={node.id}
          filenameSuffix="stderr"
          running={isExecutionRunning}
        />
      ) : null}

      <div className="inspector-block">
        <h3>{node.kind === 'notebook' ? 'Notebook docs' : 'Block docs'}</h3>
        {node.interface?.docs ? <SimpleMarkdown className="inspector-docs" text={node.interface.docs} /> : <p className="muted-copy">No block docs found.</p>}
      </div>

      {node.kind === 'constant' ? (
        <div className="inspector-block">
          <h3>Value</h3>
          <pre className="code-block docs-block execution-log-block execution-log-terminal">
            {typeof (constantArtifact?.preview as { inspector_text?: unknown } | null)?.inspector_text === 'string'
              ? String((constantArtifact?.preview as { inspector_text?: unknown }).inspector_text)
              : constantArtifact?.state === 'pending'
                ? 'Pending constant value.'
                : 'No value preview available.'}
            {Boolean((constantArtifact?.preview as { inspector_truncated?: unknown } | null)?.inspector_truncated) ? '\n\n[truncated to first 10 kB]' : ''}
          </pre>
        </div>
      ) : null}

      <div className="inspector-block">
        <h3>Inputs</h3>
        <div className="stack-list">
          {(node.interface?.inputs ?? []).map((port) => {
            const state = inputState(snapshot, node.id, port)
            const source = inputBindingSource(snapshot, node.id, port.name)
            const upstreamArtifact = source ? artifactFor(snapshot, source.source_node, source.source_port) : null
            const isMissingRequired = !port.has_default && (!source || artifactIsEmpty(upstreamArtifact))
            return (
              <div key={port.name} className={`inspector-port state-${state} ${isMissingRequired ? 'missing-required' : ''}`}>
                <div className="inspector-port-heading">
                  <span className="port-circle" style={{ backgroundColor: TYPE_COLORS[port.data_type] ?? TYPE_COLORS.object }} />
                  <code>{port.name}</code>
                  <span className="inspector-port-type">{formatType(port.data_type)}</span>
                </div>
                {port.description ? <p className="inspector-port-description">{port.description}</p> : null}
              </div>
            )
          })}
          {!node.interface?.inputs?.length ? <p className="muted-copy">No inputs.</p> : null}
        </div>
      </div>

      <div className="inspector-block">
        <h3>Outputs</h3>
        <div className="stack-list">
          {(node.interface?.outputs ?? []).map((port) => {
            const artifact = artifactFor(snapshot, node.id, port.name)
            const state = artifact?.state ?? 'pending'
            return (
              <div key={port.name} className={`inspector-port state-${state}`}>
                <div className="inspector-port-heading">
                  <span className="port-circle" style={{ backgroundColor: TYPE_COLORS[port.data_type] ?? TYPE_COLORS.object }} />
                  <code>{port.name}</code>
                  <span className="inspector-port-type">{formatType(port.data_type)}</span>
                </div>
                {port.description ? <p className="inspector-port-description">{port.description}</p> : null}
              </div>
            )
          })}
          {!node.interface?.outputs?.length ? <p className="muted-copy">No outputs.</p> : null}
          {assetCount > 0 && viewAssetsAction?.href ? (
            <div className={`inspector-assets-output state-${assetState}`}>
              <strong>+ {assetCount} asset{assetCount === 1 ? '' : 's'}</strong>
              <ActionButtons actions={[{ ...viewAssetsAction, label: 'View', className: `${viewAssetsAction.className ?? ''} inspector-assets-view`.trim() }]} itemClassName="secondary" />
            </div>
          ) : null}
        </div>
      </div>

      {node.kind === 'file_input' ? (
        <div className="inspector-block">
          <h3>File upload</h3>
          <input
            ref={fileInputRef}
            type="file"
            disabled={Boolean(node.ui?.frozen)}
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) {
                void onUploadFile(node.id, file)
              }
            }}
          />
          {node.ui?.frozen ? <p className="muted-copy">{frozenFileBlockMessage(node)}</p> : null}
        </div>
      ) : null}

      {node.template?.ref ? (
        <div className="inspector-block">
          <h3>Template origin</h3>
          <code className="template-origin-id">{template?.ref ?? node.template.ref}</code>
        </div>
      ) : null}

      <div className="inspector-block">
        <h3>Actions</h3>
        <div className="stack-list inspector-actions">
          <ActionButtons actions={nodeActions} itemClassName="secondary" />
        </div>
      </div>
    </div>
  )
}
