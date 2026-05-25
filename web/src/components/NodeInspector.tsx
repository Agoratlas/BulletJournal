import { useEffect, useMemo, useRef, useState } from 'react'

import { executionLogDownloadUrl, getExecutionLogs } from '../lib/api'
import { artifactCounts, artifactFor, artifactIsEmpty, badgeForNode, formatBytes, formatDurationSeconds, formatTimestamp, inputBindingSource, inputState, templateByRef } from '../lib/helpers'
import { formatIssueDetails, frozenFileBlockMessage, normalizeNodeId, validationIssuesForNode } from '../lib/appHelpers'
import type { NodeActionItem } from '../appTypes'
import type { ExecutionLogSummary, NodeRecord, ProjectSnapshot } from '../lib/types'
import { ArtifactCounts } from './ArtifactCounts'
import { ActionButtons } from './ActionButtons'
import { Check, Download, Pencil } from './Icons'
import { PortPill } from './PortPill'
import { SimpleMarkdown } from './SimpleMarkdown'

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
  activeRunNodeId,
  queuedRunNodeIds,
  completedRunNodeIds,
  nodeActions,
  onUploadFile,
  onOpenTemplate,
  existingNodeIds,
  onRenameNode,
  nodeIdEditDisabledReason = null,
}: {
  snapshot: ProjectSnapshot
  node: NodeRecord
  serverNowMs: number
  serverNowClientAnchorMs: number
  activeRunNodeId: string | null
  queuedRunNodeIds: string[]
  completedRunNodeIds: string[]
  nodeActions: NodeActionItem[]
  onUploadFile: (nodeId: string, file: File) => Promise<void>
  onOpenTemplate: (templateRef: string) => void
  existingNodeIds: string[]
  onRenameNode: (nodeId: string, payload: { nodeId: string; title: string }) => Promise<void>
  nodeIdEditDisabledReason?: string | null
}) {
  const badge = badgeForNode(snapshot, node)
  const counts = artifactCounts(snapshot, node.id)
  const constantArtifact = node.kind === 'constant' ? artifactFor(snapshot, node.id, node.ui?.artifact_name ?? 'value') ?? null : null
  const template = templateByRef(snapshot, node.template?.ref)
  const validationIssues = validationIssuesForNode(snapshot, node.id)
  const blockingValidationIssues = validationIssues.filter((issue) => issue.severity === 'error')
  const [now, setNow] = useState(() => Date.now())
  const [stdoutLog, setStdoutLog] = useState<ExecutionLogSummary | null>(() => node.execution_meta?.stdout ?? null)
  const [stderrLog, setStderrLog] = useState<ExecutionLogSummary | null>(() => node.execution_meta?.stderr ?? null)
  const [activeIdentityField, setActiveIdentityField] = useState<'title' | 'nodeId' | null>(null)
  const [draftTitle, setDraftTitle] = useState(node.title)
  const [draftNodeId, setDraftNodeId] = useState(node.id)
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
    setActiveIdentityField(null)
    setDraftTitle(node.title)
    setDraftNodeId(node.id)
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
  const displayedState = useMemo(() => {
    if (node.orchestrator_state?.status === 'running' || activeRunNodeId === node.id) {
      return 'running'
    }
    if (node.orchestrator_state?.status === 'queued' || queuedRunNodeIds.includes(node.id)) {
      return 'queued'
    }
    if (node.orchestrator_state?.status === 'succeeded' || completedRunNodeIds.includes(node.id)) {
      return 'ready'
    }
    return node.state
  }, [activeRunNodeId, queuedRunNodeIds, completedRunNodeIds, node.id, node.state, node.orchestrator_state])

  async function commitRename(field: 'title' | 'nodeId') {
    if (renameBusy || (field === 'nodeId' && nodeIdEditDisabledReason)) {
      return
    }
    if (field === 'title' && titleError) {
      return
    }
    if (field === 'nodeId' && (!resolvedNodeId || duplicateId)) {
      return
    }
    const nextTitle = field === 'title' ? draftTitle.trim() : node.title
    const nextNodeId = field === 'nodeId' ? resolvedNodeId : node.id
    const unchanged = nextNodeId === node.id && nextTitle === node.title
    if (unchanged) {
      setActiveIdentityField(null)
      return
    }
    setRenameBusy(true)
    try {
      await onRenameNode(node.id, { nodeId: nextNodeId, title: nextTitle })
      setActiveIdentityField(null)
    } finally {
      setRenameBusy(false)
    }
  }

  function cancelRename(field?: 'title' | 'nodeId') {
    setActiveIdentityField(null)
    if (!field || field === 'title') {
      setDraftTitle(node.title)
    }
    if (!field || field === 'nodeId') {
      setDraftNodeId(node.id)
    }
    setRenameBusy(false)
  }

  function activateIdentityField(field: 'title' | 'nodeId') {
    if (renameBusy || (field === 'nodeId' && nodeIdEditDisabledReason)) {
      return
    }
    setActiveIdentityField(field)
    if (field === 'title') {
      setDraftTitle(node.title)
      return
    }
    setDraftTitle(node.title)
    setDraftNodeId(node.id)
  }

  function handleIdentityKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault()
      if (activeIdentityField) {
        void commitRename(activeIdentityField)
      }
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      cancelRename(activeIdentityField ?? undefined)
    }
  }

  return (
    <div className="inspector-stack">
      <div className="badge-line">
        <span className="rf-badge static" title={badge.title}>{badge.label}</span>
      </div>
      <div className="stack-list subtle">
        <div>
          <span>Name</span>
          <strong className="inspector-inline-edit">
            {activeIdentityField === 'title' ? (
              <input
                value={draftTitle}
                onChange={(event) => setDraftTitle(event.target.value)}
                onKeyDown={handleIdentityKeyDown}
                placeholder="Block name"
              />
            ) : (
              node.title
            )}
            <button
              type="button"
              className="secondary small-icon-pill"
              onClick={() => {
                if (activeIdentityField === 'title') {
                  void commitRename('title')
                  return
                }
                activateIdentityField('title')
              }}
              disabled={renameBusy}
              aria-label={activeIdentityField === 'title' ? 'Save name' : 'Edit name'}
            >
              {activeIdentityField === 'title' ? <Check width={16} height={16} /> : <Pencil width={16} height={16} />}
            </button>
          </strong>
          {activeIdentityField === 'title' && titleError ? <span className="field-note error inspector-inline-note">Name is required.</span> : null}
        </div>
        <div>
          <span>Node ID</span>
          <strong className="inspector-inline-edit">
            {activeIdentityField === 'nodeId' ? (
              <input
                className={duplicateId || !resolvedNodeId ? 'invalid' : ''}
                value={draftNodeId}
                onChange={(event) => setDraftNodeId(normalizeNodeId(event.target.value))}
                onKeyDown={handleIdentityKeyDown}
                placeholder="notebook_id"
                spellCheck={false}
                disabled={Boolean(nodeIdEditDisabledReason)}
              />
            ) : (
              node.id
            )}
            <button
              type="button"
              className="secondary small-icon-pill"
              title={nodeIdEditDisabledReason ?? undefined}
              onClick={() => {
                if (activeIdentityField === 'nodeId') {
                  void commitRename('nodeId')
                  return
                }
                activateIdentityField('nodeId')
              }}
              disabled={renameBusy || Boolean(nodeIdEditDisabledReason)}
              aria-label={activeIdentityField === 'nodeId' ? 'Save node ID' : 'Edit node ID'}
            >
              {activeIdentityField === 'nodeId' ? <Check width={16} height={16} /> : <Pencil width={16} height={16} />}
            </button>
          </strong>
          {duplicateId && activeIdentityField === 'nodeId' ? <span className="field-note error inspector-inline-note">This ID is already used by another node.</span> : null}
          {!duplicateId && activeIdentityField === 'nodeId' && !resolvedNodeId ? <span className="field-note error inspector-inline-note">Node ID is required.</span> : null}
        </div>
        <div><span>Kind</span><strong>{node.kind}</strong></div>
        <div><span>Frozen</span><strong>{node.ui?.frozen ? 'yes' : 'no'}</strong></div>
        <div><span>State</span><strong>{displayedState}</strong></div>
        <div><span>Validation</span><strong>{blockingValidationIssues.length ? `${blockingValidationIssues.length} error${blockingValidationIssues.length === 1 ? '' : 's'}` : 'ok'}</strong></div>
        <div><span>Artifacts</span><ArtifactCounts counts={counts} showLabels /></div>
      </div>

      {node.execution_meta ? (
        <div className="inspector-block">
          <h3>Execution</h3>
          <div className="stack-list subtle">
            <div><span>Origin</span><strong>Orchestrator</strong></div>
            <div><span>Status</span><strong>{node.execution_meta.status}</strong></div>
            <div><span>Started</span><strong>{formatTimestamp(node.execution_meta.started_at)}</strong></div>
            {node.execution_meta.status === 'running' && runningDurationLabel ? <div><span>Elapsed</span><strong>{runningDurationLabel}</strong></div> : null}
            {node.execution_meta.status !== 'running' && typeof node.execution_meta.duration_seconds === 'number' && node.state === 'ready' ? <div><span>Duration</span><strong>{formatDurationSeconds(node.execution_meta.duration_seconds)}</strong></div> : null}
          </div>
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

      {node.template?.ref ? (
        <div className="inspector-block">
          <div className="panel-header-row">
            <h3>Template origin</h3>
            <button className="secondary" onClick={() => onOpenTemplate(node.template?.ref as string)}>View template</button>
          </div>
          <p className="muted-copy">{template?.ref ?? node.template.ref}</p>
        </div>
      ) : null}

      <div className="inspector-block">
        <h3>{node.kind === 'notebook' ? 'Notebook docs' : 'Block docs'}</h3>
        <pre className="code-block docs-block">{node.interface?.docs ?? 'No block docs found.'}</pre>
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
                <PortPill name={port.name} label={port.label} dataType={port.data_type} side="input" compact />
                <div className="inspector-port-meta">
                  <span>{source ? `${source.source_node}/${source.source_port}` : port.has_default ? 'default value' : 'not connected'}</span>
                  {port.has_default ? <span>default: {JSON.stringify(port.default)}</span> : null}
                </div>
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
                <PortPill
                  name={port.name}
                  label={port.label}
                  dataType={port.data_type}
                  side="output"
                  compact
                />
              </div>
            )
          })}
          {!node.interface?.outputs?.length ? <p className="muted-copy">No outputs.</p> : null}
        </div>
      </div>

      <div className="inspector-block">
        <h3>Validation</h3>
        <div className="warning-list">
          {snapshot.notices.filter((issue) => issue.node_id === node.id).map((issue) => {
            const details = formatIssueDetails(issue.details)
            return (
              <div key={issue.issue_id} className={`warning-chip ${issue.severity}`}>
                <strong>{issue.code}</strong>
                <SimpleMarkdown className="warning-chip-message" text={issue.message} />
                {details ? <pre className="warning-details">{details}</pre> : null}
              </div>
            )
          })}
          {!snapshot.notices.some((issue) => issue.node_id === node.id) ? <p className="muted-copy">No active validation issues.</p> : null}
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

      <div className="inspector-block">
        <h3>Actions</h3>
        <div className="stack-list inspector-actions">
          <ActionButtons actions={nodeActions} itemClassName="secondary" />
        </div>
      </div>
    </div>
  )
}
