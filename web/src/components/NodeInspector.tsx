import { useEffect, useMemo, useRef, useState } from 'react'

import { executionLogDownloadUrl } from '../lib/api'
import { artifactCounts, artifactFor, badgeForNode, formatDurationSeconds, formatTimestamp, hiddenInputNames, inputBindingSource, inputState, templateByRef } from '../lib/helpers'
import { formatIssueDetails, frozenFileBlockMessage, nodeRunFailures, validationIssuesForNode } from '../lib/appHelpers'
import type { NodeActionItem } from '../appTypes'
import type { NodeRecord, ProjectSnapshot } from '../lib/types'
import { ArtifactCounts } from './ArtifactCounts'
import { ActionButtons } from './ActionButtons'
import { Download } from './Icons'
import { PortPill } from './PortPill'
import { SimpleMarkdown } from './SimpleMarkdown'

function ExecutionLogPanel({
  title,
  log,
  nodeId,
  filenameSuffix,
}: {
  title: string
  log: { text: string; truncated: boolean }
  nodeId: string
  filenameSuffix: 'stdout' | 'stderr'
}) {
  return (
    <div className="inspector-subblock">
      <div className="panel-header-row execution-log-header">
        <strong>{title}</strong>
        <a className="secondary small link-button" href={executionLogDownloadUrl(nodeId, filenameSuffix)}>
          <Download width={14} height={14} />
          Download
        </a>
      </div>
      <pre className="code-block docs-block execution-log-block">{log.text}</pre>
      {log.truncated ? <p className="muted-copy">Preview truncated by the server to 50 lines or 10k characters.</p> : null}
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
  onToggleHiddenInput,
  onUploadFile,
  onOpenTemplate,
}: {
  snapshot: ProjectSnapshot
  node: NodeRecord
  serverNowMs: number
  serverNowClientAnchorMs: number
  activeRunNodeId: string | null
  queuedRunNodeIds: string[]
  completedRunNodeIds: string[]
  nodeActions: NodeActionItem[]
  onToggleHiddenInput: (node: NodeRecord, inputName: string) => Promise<void>
  onUploadFile: (nodeId: string, file: File) => Promise<void>
  onOpenTemplate: (templateRef: string) => void
}) {
  const badge = badgeForNode(snapshot, node)
  const counts = artifactCounts(snapshot, node.id)
  const template = templateByRef(snapshot, node.template?.ref)
  const validationIssues = validationIssuesForNode(snapshot, node.id)
  const blockingValidationIssues = validationIssues.filter((issue) => issue.severity === 'error')
  const runFailures = nodeRunFailures(snapshot, node.id)
  const [now, setNow] = useState(() => Date.now())
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (node.execution_meta?.status !== 'running') {
      return
    }
    const interval = window.setInterval(() => setNow(Date.now()), 100)
    return () => window.clearInterval(interval)
  }, [node.execution_meta?.status])

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

  return (
    <div className="inspector-stack">
      <div className="badge-line">
        <span className="rf-badge static" title={badge.title}>{badge.label}</span>
        <strong>{node.title}</strong>
      </div>
      <div className="stack-list subtle">
        <div><span>Node ID</span><strong>{node.id}</strong></div>
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

      {node.execution_meta?.stdout ? (
        <div className="inspector-block">
          <h3>Stdout</h3>
          <ExecutionLogPanel
            title="Notebook stdout"
            log={node.execution_meta.stdout}
            nodeId={node.id}
            filenameSuffix="stdout"
          />
        </div>
      ) : null}

      {node.execution_meta?.stderr ? (
        <div className="inspector-block">
          <h3>Stderr</h3>
          <ExecutionLogPanel
            title="Notebook stderr"
            log={node.execution_meta.stderr}
            nodeId={node.id}
            filenameSuffix="stderr"
          />
        </div>
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
        <h3>{node.kind === 'organizer' || node.kind === 'area' ? 'Block docs' : 'Notebook docs'}</h3>
        <pre className="code-block docs-block">{node.interface?.docs ?? 'No notebook docs found.'}</pre>
      </div>

      <div className="inspector-block">
        <h3>Inputs</h3>
        <div className="stack-list">
          {(node.interface?.inputs ?? []).map((port) => {
            const state = inputState(snapshot, node.id, port)
            const source = inputBindingSource(snapshot, node.id, port.name)
            const hidden = hiddenInputNames(node).has(port.name)
            return (
              <div key={port.name} className="inspector-port">
                <PortPill name={port.name} label={port.label} dataType={port.data_type} state={state} side="input" compact />
                <div className="inspector-port-meta">
                  <span>{source ? `${source.source_node}/${source.source_port}` : port.has_default ? 'default value' : 'not connected'}</span>
                  {port.has_default ? <span>default: {JSON.stringify(port.default)}</span> : null}
                </div>
                {port.has_default ? (
                  <button className="secondary small" onClick={() => onToggleHiddenInput(node, port.name)}>
                    {hidden ? 'Show on node' : 'Hide on node'}
                  </button>
                ) : null}
              </div>
            )
          })}
          {!node.interface?.inputs?.length ? <p className="muted-copy">No inputs.</p> : null}
        </div>
      </div>

      <div className="inspector-block">
        <h3>Outputs</h3>
        <div className="stack-list">
          {(node.interface?.outputs ?? []).map((port) => (
            <div key={port.name} className="inspector-port">
              <PortPill
                name={port.name}
                label={port.label}
                dataType={port.data_type}
                state={artifactFor(snapshot, node.id, port.name)?.state ?? 'pending'}
                side="output"
                compact
              />
            </div>
          ))}
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

      <div className="inspector-block">
        <h3>Runtime errors</h3>
        <div className="warning-list">
          {runFailures.map((run) => {
            const failure = run.failure_json as Record<string, unknown>
            const traceback = typeof failure.traceback === 'string' ? failure.traceback : null
            const stderr = typeof failure.stderr === 'string' ? failure.stderr : null
            const errorMessage = typeof failure.error === 'string' ? failure.error : 'Run failed.'
            return (
              <div key={run.run_id} className="warning-chip error">
                <strong>{errorMessage}</strong>
                <span>{formatTimestamp(run.ended_at ?? run.started_at)}</span>
                {traceback ? <pre className="warning-details">{traceback}</pre> : null}
                {!traceback && stderr ? <pre className="warning-details">{stderr}</pre> : null}
              </div>
            )
          })}
          {!runFailures.length ? <p className="muted-copy">No recorded runtime errors.</p> : null}
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
