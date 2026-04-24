import { appUrl } from './api'
import { GRID_SIZE, inputBindingSource } from './helpers'
import type { ArtifactRecord, GraphPatchOperation, LayoutRecord, NodeRecord, ProjectSnapshot, TemplateRecord } from './types'
import type { AppNotice, ConstantValueType, GraphMutationPlan, OptimisticGraphState, PaletteEntry, PortActionMenuState, SnapshotLike } from '../appTypes'

type EditorSessionNoticeDetails = {
  session_id: string
  session_url: string
  ready?: boolean
}

const MARKDOWN_CODE_SPAN_PATTERN = /(`[^`]*`)/g
const MARKDOWN_VALUE_PATTERN = /(^|[^A-Za-z0-9`])([A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)+)(?=$|[^A-Za-z0-9`])/g

export const DATAFRAME_CSV_DOWNLOAD_MAX_BYTES = 100_000_000

export function blockCreateMode(entry: PaletteEntry): 'notebook' | 'constant_value' | 'file' | 'pipeline' | null {
  if (entry.kind === 'pipeline') {
    return 'pipeline'
  }
  if (entry.kind === 'value_input') {
    return 'constant_value'
  }
  if (entry.kind === 'file_input') {
    return 'file'
  }
  if (entry.kind === 'organizer' || entry.kind === 'area') {
    return null
  }
  return 'notebook'
}

export function normalizeNodeId(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

export function edgeIdForPorts(sourceNode: string, sourcePort: string, targetNode: string, targetPort: string): string {
  return `${sourceNode}.${sourcePort}__${targetNode}.${targetPort}`
}

export function copiedTitle(title: string): string {
  return title.endsWith(' Copy') ? title : `${title} Copy`
}

export function uniqueCopiedNodeId(baseNodeId: string, existingNodeIds: Set<string>): string {
  const baseCopyId = normalizeNodeId(`${baseNodeId}_copy`) || 'node_copy'
  if (!existingNodeIds.has(baseCopyId)) {
    return baseCopyId
  }
  let index = 2
  while (existingNodeIds.has(`${baseCopyId}_${index}`)) {
    index += 1
  }
  return `${baseCopyId}_${index}`
}

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

function prefixedNodeId(nodeIdPrefix: string | null | undefined, templateNodeId: string): string {
  const normalizedPrefix = normalizeNodeId(nodeIdPrefix ?? '')
  return normalizedPrefix ? `${normalizedPrefix}_${templateNodeId}` : templateNodeId
}

export function artifactEndpoint(artifact: ArtifactRecord, action: 'download' | 'content'): string {
  const nodeId = encodeURIComponent(artifact.node_id)
  const artifactName = encodeURIComponent(artifact.artifact_name)
  return appUrl(`/api/v1/artifacts/${nodeId}/${artifactName}/${action}`)
}

export function createClientNotice(
  issueId: string,
  severity: 'error' | 'warning',
  code: string,
  message: string,
  options: { nodeId?: string | null; details?: Record<string, unknown> } = {},
): AppNotice {
  return {
    issue_id: issueId,
    node_id: options.nodeId ?? null,
    severity,
    code,
    message: autoFormatMarkdownValues(message),
    details: options.details ?? {},
    created_at: new Date().toISOString(),
    origin: 'client',
  }
}

export function formatMarkdownCode(value: string): string {
  return `\`${value.replace(/`/g, "'")}\``
}

export function autoFormatMarkdownValues(text: string): string {
  return text
    .split(MARKDOWN_CODE_SPAN_PATTERN)
    .map((segment) => {
      if (segment.startsWith('`') && segment.endsWith('`')) {
        return segment
      }
      return segment.replace(MARKDOWN_VALUE_PATTERN, (_match, prefix: string, value: string) => `${prefix}${formatMarkdownCode(value)}`)
    })
    .join('')
}

function describeNodeLabel(title: string, nodeId: string): string {
  if (title === nodeId) {
    return formatMarkdownCode(nodeId)
  }
  return `${formatMarkdownCode(title)} (${formatMarkdownCode(nodeId)})`
}

export function runFailureMessage(response: Record<string, unknown>, fallback: string): string {
  const directError = response.error
  if (typeof directError === 'string' && directError.trim()) {
    return directError
  }
  const nodeResults = response.node_results
  if (nodeResults && typeof nodeResults === 'object') {
    const nestedError = (nodeResults as { error?: unknown }).error
    if (typeof nestedError === 'string' && nestedError.trim()) {
      return nestedError
    }
  }
  return fallback
}

export function runFailureNodeId(response: Record<string, unknown>): string | null {
  if (typeof response.node_id === 'string' && response.node_id.trim()) {
    return response.node_id
  }
  const nodeResults = response.node_results
  if (nodeResults && typeof nodeResults === 'object') {
    const nestedNodeId = (nodeResults as { node_id?: unknown }).node_id
    if (typeof nestedNodeId === 'string' && nestedNodeId.trim()) {
      return nestedNodeId
    }
  }
  return null
}

export function formatRunFailureMessage(snapshot: ProjectSnapshot | null | undefined, response: Record<string, unknown>, fallback: string): string {
  const message = autoFormatMarkdownValues(runFailureMessage(response, fallback))
  const nodeId = runFailureNodeId(response)
  if (!nodeId) {
    return message
  }
  const failedNode = snapshot?.graph.nodes.find((node) => node.id === nodeId)
  if (!failedNode) {
    return `Run failed in ${formatMarkdownCode(nodeId)}. ${message}`
  }
  return `Run failed in ${describeNodeLabel(failedNode.title, failedNode.id)}. ${message}`
}

function describeNode(snapshot: ProjectSnapshot | null | undefined, nodeId: string): string {
  const node = snapshot?.graph.nodes.find((entry) => entry.id === nodeId)
  if (!node) {
    return formatMarkdownCode(nodeId)
  }
  return describeNodeLabel(node.title, node.id)
}

export function formatRunBlockedMessage(
  snapshot: ProjectSnapshot | null | undefined,
  nodeId: string | null | undefined,
  response: Record<string, unknown>,
): string {
  const blockedInputs = Array.isArray(response.blocked_inputs) ? response.blocked_inputs : []
  const runLabel = typeof nodeId === 'string' && nodeId.trim() ? `Run for ${describeNode(snapshot, nodeId)} ` : 'This run '
  if (!blockedInputs.length) {
    return `${runLabel}is blocked by missing or pending inputs.`
  }
  const summaries = blockedInputs
    .map((blockedInput) => {
      if (!blockedInput || typeof blockedInput !== 'object') {
        return null
      }
      const record = blockedInput as { name?: unknown; source?: unknown; state?: unknown }
      const name = typeof record.name === 'string' && record.name.trim() ? record.name : 'unknown input'
      const source = typeof record.source === 'string' && record.source.trim() ? ` from \`${record.source}\`` : ''
      const state = typeof record.state === 'string' && record.state.trim() ? record.state : 'missing'
      return `\`${name}\`${source} is ${state}`
    })
    .filter((summary): summary is string => summary !== null)
  if (!summaries.length) {
    return `${runLabel}is blocked by missing or pending inputs.`
  }
  return `${runLabel}is blocked by missing or pending inputs: ${summaries.join(', ')}.`
}

export function isManagedRunFailure(response: Record<string, unknown>): boolean {
  return response.status === 'failed' && typeof response.run_id === 'string'
}

export function isEditorOpenConflict(message: string): boolean {
  return message.includes('An editor is open for this notebook.')
}

export function isFreezeConflict(message: string): boolean {
  return message.toLowerCase().includes('frozen') && message.includes('Unfreeze')
}

export function editorSessionDetails(details: Record<string, unknown>): EditorSessionNoticeDetails | null {
  if (typeof details.session_id !== 'string' || typeof details.session_url !== 'string') {
    return null
  }
  return {
    session_id: details.session_id,
    session_url: details.session_url,
    ready: typeof details.ready === 'boolean' ? details.ready : undefined,
  }
}

export function artifactTargetForPort(snapshot: ProjectSnapshot, menu: PortActionMenuState): { nodeId: string; artifactName: string } | null {
  if (menu.side === 'output') {
    return { nodeId: menu.nodeId, artifactName: menu.portName }
  }
  const binding = inputBindingSource(snapshot, menu.nodeId, menu.portName)
  if (!binding) {
    return null
  }
  return { nodeId: binding.source_node, artifactName: binding.source_port }
}

export function edgeIdsForPort(snapshot: ProjectSnapshot, menu: PortActionMenuState): string[] {
  return snapshot.graph.edges
    .filter((edge) => {
      if (menu.side === 'output') {
        return edge.source_node === menu.nodeId && edge.source_port === menu.portName
      }
      return edge.target_node === menu.nodeId && edge.target_port === menu.portName
    })
    .map((edge) => edge.id)
}

function downstreamNodeIds(snapshot: ProjectSnapshot, rootNodeIds: string[]): Set<string> {
  const queue = [...rootNodeIds]
  const visited = new Set(rootNodeIds)
  const downstreamByNodeId = new Map<string, string[]>()
  for (const edge of snapshot.graph.edges) {
    const targets = downstreamByNodeId.get(edge.source_node) ?? []
    targets.push(edge.target_node)
    downstreamByNodeId.set(edge.source_node, targets)
  }
  while (queue.length) {
    const nodeId = queue.shift() as string
    for (const targetNodeId of downstreamByNodeId.get(nodeId) ?? []) {
      if (visited.has(targetNodeId)) {
        continue
      }
      visited.add(targetNodeId)
      queue.push(targetNodeId)
    }
  }
  return visited
}

export function frozenBlockBlockersForStaleRoots(snapshot: ProjectSnapshot, rootNodeIds: string[]): NodeRecord[] {
  const affectedNodeIds = downstreamNodeIds(snapshot, rootNodeIds)
  return snapshot.graph.nodes.filter((node) => Boolean(node.ui?.frozen) && affectedNodeIds.has(node.id))
}

export function frozenBlockBlockersForDelete(snapshot: ProjectSnapshot, nodeId: string): NodeRecord[] {
  const blockers: NodeRecord[] = []
  const seen = new Set<string>()
  const node = snapshot.graph.nodes.find((entry) => entry.id === nodeId) ?? null
  if (node?.ui?.frozen) {
    blockers.push(node)
    seen.add(node.id)
  }
  const staleRoots = Array.from(new Set(
    snapshot.graph.edges
      .filter((edge) => edge.source_node === nodeId)
      .map((edge) => edge.target_node),
  ))
  for (const blocker of frozenBlockBlockersForStaleRoots(snapshot, staleRoots)) {
    if (seen.has(blocker.id)) {
      continue
    }
    blockers.push(blocker)
    seen.add(blocker.id)
  }
  return blockers
}

export function frozenBlockBlockersForRemovedEdges(snapshot: ProjectSnapshot, edgeIds: string[]): NodeRecord[] {
  const staleRoots = Array.from(new Set(
    snapshot.graph.edges
      .filter((edge) => edgeIds.includes(edge.id))
      .map((edge) => edge.target_node),
  ))
  return frozenBlockBlockersForStaleRoots(snapshot, staleRoots)
}

export function freezeBlockMessage(blockers: NodeRecord[]): string {
  const labels = blockers.map((node) => describeNodeLabel(node.title, node.id)).join(', ')
  if (blockers.length === 1) {
    return `This change is blocked because it would affect the frozen block ${labels}. Unfreeze it first.`
  }
  return `This change is blocked because it would affect frozen blocks ${labels}. Unfreeze them first.`
}

export function frozenFileBlockMessage(node: NodeRecord): string {
  return `This block is frozen. Unfreeze ${describeNodeLabel(node.title, node.id)} before replacing the file.`
}

export function cloneSnapshot(snapshot: ProjectSnapshot): ProjectSnapshot {
  return {
    ...snapshot,
    project: { ...snapshot.project },
    graph: {
      meta: { ...snapshot.graph.meta },
      nodes: snapshot.graph.nodes.map((node) => ({
        ...node,
        template: node.template ? { ...node.template } : node.template,
        ui: cloneUiState(node.ui),
        interface: node.interface
          ? {
              ...node.interface,
              inputs: node.interface.inputs.map((port) => ({ ...port })),
              outputs: node.interface.outputs.map((port) => ({ ...port })),
              assets: node.interface.assets.map((port) => ({ ...port })),
              issues: node.interface.issues.map((issue) => ({ ...issue })),
            }
          : node.interface,
      })),
      edges: snapshot.graph.edges.map((edge) => ({ ...edge })),
      layout: snapshot.graph.layout.map((entry) => ({ ...entry })),
    },
    validation_issues: snapshot.validation_issues.map((issue) => ({ ...issue })),
    notices: snapshot.notices.map((notice) => ({ ...notice })),
    artifacts: snapshot.artifacts.map((artifact) => ({ ...artifact, warnings: artifact.warnings.map((warning) => ({ ...warning })) })),
    runs: snapshot.runs.map((run) => ({ ...run, target_json: run.target_json, source_snapshot_json: run.source_snapshot_json })),
    checkpoints: snapshot.checkpoints.map((checkpoint) => ({ ...checkpoint })),
    templates: snapshot.templates.map((template) => ({ ...template })),
  }
}

export function mergeGraphIntoSnapshot(snapshot: SnapshotLike, graph: { meta: ProjectSnapshot['graph']['meta']; nodes: ProjectSnapshot['graph']['nodes']; edges: ProjectSnapshot['graph']['edges']; layout: ProjectSnapshot['graph']['layout'] }): ProjectSnapshot {
  const merged = cloneSnapshot(snapshot as ProjectSnapshot)
  merged.graph = {
    meta: { ...graph.meta },
    nodes: graph.nodes.map((node) => ({
      ...node,
      template: node.template ? { ...node.template } : node.template,
      ui: cloneUiState(node.ui),
      interface: node.interface
        ? {
            ...node.interface,
            inputs: node.interface.inputs.map((port) => ({ ...port })),
            outputs: node.interface.outputs.map((port) => ({ ...port })),
            assets: node.interface.assets.map((port) => ({ ...port })),
            issues: node.interface.issues.map((issue) => ({ ...issue })),
          }
        : node.interface,
    })),
    edges: graph.edges.map((edge) => ({ ...edge })),
    layout: graph.layout.map((entry) => ({ ...entry })),
  }
  return merged
}

export function clampContextMenuPosition(position: { x: number; y: number }, estimatedSize: { width: number; height: number } = { width: 260, height: 320 }) {
  const margin = 12
  return {
    x: Math.max(margin, Math.min(position.x, window.innerWidth - estimatedSize.width - margin)),
    y: Math.max(margin, Math.min(position.y, window.innerHeight - estimatedSize.height - margin)),
  }
}

export function pipelineTemplateNodeRecords(
  snapshot: ProjectSnapshot,
  templateRef: string,
  nodeIdPrefix?: string | null,
): Array<{ nodeId: string; title: string }> {
  const template = snapshot.templates.find((entry) => entry.ref === templateRef && entry.kind === 'pipeline')
  return (template?.definition?.nodes ?? []).map((node) => ({
    nodeId: prefixedNodeId(nodeIdPrefix, node.id),
    title: nodeIdPrefix?.trim() ? `${nodeIdPrefix.trim()} ${node.title}` : node.title,
  }))
}

export function expandMutationPlan(plan: GraphMutationPlan): GraphPatchOperation[] {
  return [...plan.operations, ...(plan.followUpOperations ?? [])]
}

function cloneNodeUi(node: NodeRecord): NonNullable<GraphPatchOperation & { type: 'add_notebook_node' }>['ui'] {
  return {
    origin: node.ui?.origin ?? null,
    frozen: Boolean(node.ui?.frozen),
  }
}

export function notebookAddOperationForNode(
  node: NodeRecord,
  layout: LayoutRecord,
  sourceText: string | null,
  nodeId: string,
  title: string,
): GraphPatchOperation {
  return {
    type: 'add_notebook_node',
    node_id: nodeId,
    title,
    template_ref: sourceText === null ? node.template?.ref : undefined,
    source_text: sourceText ?? undefined,
    ui: cloneNodeUi(node),
    x: layout.x,
    y: layout.y,
    w: layout.w,
    h: layout.h,
  }
}

export function fileInputAddOperationForNode(node: NodeRecord, layout: LayoutRecord, nodeId: string, title: string): GraphPatchOperation {
  return {
    type: 'add_file_input_node',
    node_id: nodeId,
    title,
    artifact_name: node.ui?.artifact_name ?? 'file',
    ui: { frozen: Boolean(node.ui?.frozen) },
    x: layout.x,
    y: layout.y,
    w: layout.w,
    h: layout.h,
  }
}

export function organizerAddOperationForNode(node: NodeRecord, layout: LayoutRecord, nodeId: string, title = node.title): GraphPatchOperation {
  return {
    type: 'add_organizer_node',
    node_id: nodeId,
    title,
    ui: {
      frozen: Boolean(node.ui?.frozen),
      organizer_ports: (node.ui?.organizer_ports ?? []).map((port) => ({ ...port })),
    },
    x: layout.x,
    y: layout.y,
    w: layout.w,
    h: layout.h,
  }
}

export function areaAddOperationForNode(node: NodeRecord, layout: LayoutRecord, nodeId: string, title = node.title): GraphPatchOperation {
  return {
    type: 'add_area_node',
    node_id: nodeId,
    title,
    ui: {
      frozen: Boolean(node.ui?.frozen),
      title_position: node.ui?.title_position ?? 'top-left',
      area_color: node.ui?.area_color ?? 'blue',
      area_filled: node.ui?.area_filled ?? true,
    },
    x: layout.x,
    y: layout.y,
    w: layout.w,
    h: layout.h,
  }
}

export function applyOptimisticGraphOperations(snapshot: ProjectSnapshot, operations: Array<Record<string, unknown>>): OptimisticGraphState | null {
  const next = cloneSnapshot(snapshot)
  let changed = false
  let clearSelection = false
  let clearArtifacts = false

  for (const operation of operations) {
    const type = operation.type
    if (type === 'add_pipeline_template') {
      continue
    }
    if (type === 'add_notebook_node' || type === 'add_file_input_node' || type === 'add_organizer_node' || type === 'add_area_node') {
      const nodeId = String(operation.node_id)
      if (!next.graph.nodes.some((node) => node.id === nodeId)) {
        const kind = type === 'add_file_input_node'
          ? 'file_input'
          : type === 'add_organizer_node'
            ? 'organizer'
            : type === 'add_area_node'
              ? 'area'
            : 'notebook'
        next.graph.nodes.push({
          id: nodeId,
          kind,
          title: String(operation.title ?? (kind === 'organizer' ? 'Organizer' : kind === 'area' ? 'Area' : nodeId)),
          path: kind === 'notebook' ? `${nodeId}.py` : null,
          template: null,
          template_status: null,
          ui: type === 'add_file_input_node'
            ? { artifact_name: String(operation.artifact_name ?? 'file'), frozen: false }
            : type === 'add_organizer_node'
              ? {
                frozen: Boolean((operation.ui as { frozen?: unknown } | undefined)?.frozen),
                organizer_ports: Array.isArray((operation.ui as { organizer_ports?: unknown } | undefined)?.organizer_ports)
                  ? ((operation.ui as { organizer_ports: Array<{ key?: unknown; name?: unknown; data_type?: unknown }> }).organizer_ports).map((port) => ({
                    key: String(port.key ?? ''),
                    name: String(port.name ?? ''),
                    data_type: String(port.data_type ?? ''),
                  }))
                  : [],
              }
              : type === 'add_area_node'
                ? {
                  frozen: Boolean((operation.ui as { frozen?: unknown } | undefined)?.frozen),
                  title_position: String((operation.ui as { title_position?: unknown } | undefined)?.title_position ?? 'top-left'),
                  area_color: String((operation.ui as { area_color?: unknown } | undefined)?.area_color ?? 'blue'),
                  area_filled: Boolean((operation.ui as { area_filled?: unknown } | undefined)?.area_filled ?? true),
                }
              : {
                  frozen: Boolean((operation.ui as { frozen?: unknown } | undefined)?.frozen),
                  origin: (operation.ui as { origin?: 'constant_value' | null } | undefined)?.origin ?? null,
                },
          interface: null,
          execution_meta: null,
          orchestrator_state: null,
          state: 'pending',
        })
        next.graph.layout.push({
          node_id: nodeId,
          x: Number(operation.x ?? 80),
          y: Number(operation.y ?? 80),
          w: Number(operation.w ?? (type === 'add_organizer_node' ? 160 : type === 'add_area_node' ? 480 : 360)),
          h: Number(operation.h ?? (type === 'add_organizer_node' ? 140 : type === 'add_area_node' ? 280 : 220)),
        })
        changed = true
      }
      continue
    }
    if (type === 'update_node_layout') {
      const nodeId = String(operation.node_id)
      const layout = next.graph.layout.find((entry) => entry.node_id === nodeId)
      if (layout) {
        layout.x = Number(operation.x)
        layout.y = Number(operation.y)
        if (operation.w !== undefined) {
          layout.w = Number(operation.w)
        }
        if (operation.h !== undefined) {
          layout.h = Number(operation.h)
        }
        changed = true
      }
      continue
    }
    if (type === 'delete_node') {
      const nodeId = String(operation.node_id)
      next.graph.nodes = next.graph.nodes.filter((node) => node.id !== nodeId)
      next.graph.layout = next.graph.layout.filter((entry) => entry.node_id !== nodeId)
      next.graph.edges = next.graph.edges.filter((edge) => edge.source_node !== nodeId && edge.target_node !== nodeId)
      next.artifacts = next.artifacts.filter((artifact) => artifact.node_id !== nodeId)
      next.validation_issues = next.validation_issues.filter((issue) => issue.node_id !== nodeId)
      next.notices = next.notices.filter((notice) => notice.node_id !== nodeId)
      clearSelection = true
      clearArtifacts = true
      changed = true
      continue
    }
    if (type === 'remove_edge') {
      const edgeId = String(operation.edge_id)
      next.graph.edges = next.graph.edges.filter((edge) => edge.id !== edgeId)
      changed = true
      continue
    }
    if (type === 'add_edge') {
      const sourceNode = String(operation.source_node)
      const sourcePort = String(operation.source_port)
      const targetNode = String(operation.target_node)
      const targetPort = String(operation.target_port)
      const id = edgeIdForPorts(sourceNode, sourcePort, targetNode, targetPort)
      if (!next.graph.edges.some((edge) => edge.id === id)) {
        next.graph.edges.push({ id, source_node: sourceNode, source_port: sourcePort, target_node: targetNode, target_port: targetPort })
        changed = true
      }
      continue
    }
    if (type === 'update_node_frozen') {
      const node = next.graph.nodes.find((entry) => entry.id === String(operation.node_id))
      if (node) {
        node.ui = { ...(node.ui ?? {}), frozen: Boolean(operation.frozen) }
        changed = true
      }
      continue
    }
    if (type === 'update_organizer_ports') {
      const node = next.graph.nodes.find((entry) => entry.id === String(operation.node_id))
      if (node) {
        const previousPorts = node.ui?.organizer_ports ?? []
        const nextPorts = Array.isArray(operation.ports)
          ? operation.ports.map((port) => ({
            key: String((port as { key?: unknown }).key ?? ''),
            name: String((port as { name?: unknown }).name ?? ''),
            data_type: String((port as { data_type?: unknown }).data_type ?? ''),
          }))
          : []
        const nextPortByKey = new Map(nextPorts.map((port) => [port.key, port]))
        const removedKeys = previousPorts
          .filter((port) => {
            const nextPort = nextPortByKey.get(port.key)
            return !nextPort || nextPort.data_type !== port.data_type
          })
          .map((port) => port.key)
        node.ui = {
          ...(node.ui ?? {}),
          organizer_ports: nextPorts,
        }
        if (removedKeys.length) {
          next.graph.edges = next.graph.edges.filter((edge) => {
            if (edge.source_node === node.id && removedKeys.includes(edge.source_port)) {
              return false
            }
            if (edge.target_node === node.id && removedKeys.includes(edge.target_port)) {
              return false
            }
            return true
          })
        }
        changed = true
      }
    }
    if (type === 'update_area_style') {
      const node = next.graph.nodes.find((entry) => entry.id === String(operation.node_id))
      if (node) {
        node.ui = {
          ...(node.ui ?? {}),
          title_position: String(operation.title_position ?? 'top-left'),
          area_color: String(operation.color ?? 'blue'),
          area_filled: Boolean(operation.filled),
        }
        changed = true
      }
    }
  }

  if (!changed) {
    return null
  }

  next.graph.meta = {
    ...next.graph.meta,
    graph_version: next.graph.meta.graph_version + 1,
    updated_at: new Date().toISOString(),
  }

  return {
    snapshot: next,
    clearSelection,
    clearArtifacts,
  }
}

function cloneUiState(ui: NodeRecord['ui'] | undefined): NodeRecord['ui'] | undefined {
  if (!ui) {
    return ui
  }
  return {
    ...ui,
    organizer_ports: ui.organizer_ports ? ui.organizer_ports.map((port) => ({ ...port })) : ui.organizer_ports,
    title_position: ui.title_position,
    area_color: ui.area_color,
    area_filled: ui.area_filled,
  }
}

export const SNAPSHOT_REFRESH_EVENTS = [
  'artifact.state_changed',
  'checkpoint.created',
  'checkpoint.restored',
  'graph.updated',
  'notebook.reparsed',
  'notice.created',
  'notice.dismissed',
  'project.opened',
  'run.failed',
  'run.finished',
  'run.progress',
  'run.queued',
  'run.started',
  'validation.updated',
]

export const SNAPSHOT_REFRESH_THROTTLE_MS = 1000

export function validationIssuesForNode(snapshot: ProjectSnapshot, nodeId: string) {
  return snapshot.validation_issues.filter((issue) => issue.node_id === nodeId)
}

export function formatIssueDetails(details: Record<string, unknown>): string | null {
  const removedEdges = details.removed_edges
  if (Array.isArray(removedEdges) && removedEdges.length > 0) {
    return removedEdges
      .map((edge) => {
        if (!edge || typeof edge !== 'object') {
          return JSON.stringify(edge)
        }
        const record = edge as Record<string, unknown>
        return [
          `Removed edge ${record.id ?? '?'}`,
          `from ${record.source_node ?? '?'}/${record.source_port ?? '?'}`,
          `to ${record.target_node ?? '?'}/${record.target_port ?? '?'}`,
        ].join('\n')
      })
      .join('\n\n')
  }
  const entries = Object.entries(details)
  if (!entries.length) {
    return null
  }
  return JSON.stringify(details, null, 2)
}

export function nodeRunFailures(snapshot: ProjectSnapshot, nodeId: string) {
  return snapshot.runs.filter((run) => {
    if (run.status !== 'failed' || !run.failure_json) {
      return false
    }
    if (typeof run.failure_json.node_id === 'string') {
      return run.failure_json.node_id === nodeId
    }
    const target = run.target_json
    if (typeof target.node_id === 'string') {
      return target.node_id === nodeId
    }
    if (Array.isArray(target.plan)) {
      return target.plan.includes(nodeId)
    }
    if (Array.isArray(target.node_ids)) {
      return target.node_ids.includes(nodeId)
    }
    return false
  })
}

export function pipelineDefinitionNodeIds(template: TemplateRecord | null | undefined): string[] {
  if (!template) {
    return []
  }
  return (template.definition?.nodes ?? []).map((node) => node.id)
}

export function pipelineTopLeftForCenter(template: TemplateRecord, center: { x: number; y: number }): { x: number; y: number } {
  const layout = template.definition?.layout ?? []
  if (!layout.length) {
    return center
  }
  const minX = Math.min(...layout.map((entry) => entry.x))
  const minY = Math.min(...layout.map((entry) => entry.y))
  const maxRight = Math.max(...layout.map((entry) => entry.x + entry.w))
  const maxBottom = Math.max(...layout.map((entry) => entry.y + entry.h))
  const width = maxRight - minX
  const height = maxBottom - minY
  return {
    x: center.x - width / 2,
    y: center.y - height / 2,
  }
}

export function snapToGrid(value: number): number {
  return Math.round(value / GRID_SIZE) * GRID_SIZE
}

function pythonTypeExpression(dataType: Exclude<ConstantValueType, 'object'>): string {
  return dataType
}

export function buildConstantValueNotebookSource(
  title: string,
  outputs: Array<{ name: string; dataType: ConstantValueType; value: string }>,
): string {
  const setupImports = new Set(['from bulletjournal.runtime import artifacts'])

  const cells = outputs.flatMap((output) => {
    const variableName = `${output.name}_value`
    if (output.dataType === 'object') {
      return [
        '@app.cell',
        'def _():',
        `    placeholder_note_${output.name} = \'Edit this notebook to set ${output.name} to a custom object.\'`,
        `    ${variableName} = None`,
        `    artifacts.push(${variableName}, name='${output.name}', data_type='object', description='Constant value output')`,
        `    return placeholder_note_${output.name}, ${variableName}`,
        '',
      ]
    }
    const dataTypeExpression = pythonTypeExpression(output.dataType)
    return [
      '@app.cell',
      'def _():',
      `    ${variableName} = ${output.value}`,
      `    artifacts.push(${variableName}, name='${output.name}', data_type=${dataTypeExpression}, description='Constant value output')`,
      `    return ${variableName}`,
      '',
    ]
  })

  return [
    'import marimo',
    '',
    "__generated_with = '0.20.4'",
    `app = marimo.App(width='medium', app_title=${JSON.stringify(title)})`,
    '',
    'with app.setup:',
    ...Array.from(setupImports).sort().map((line) => `    ${line}`),
    '',
    ...cells,
    "if __name__ == '__main__':",
    '    from bulletjournal.runtime.standalone import run_notebook_app',
    '',
    "    run_notebook_app(app, __file__)",
    '',
  ].join('\n')
}
