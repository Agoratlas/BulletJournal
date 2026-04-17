import type { ArtifactRecord, ArtifactState, NodeRecord, Port, ProjectSnapshot, TemplateRecord } from './types'

export const GRID_SIZE = 20

export function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '') || 'node'
}

export function inputsForNode(node: NodeRecord): Port[] {
  return node.interface?.inputs ?? []
}

export function organizerPorts(node: NodeRecord): Array<{ key: string; name: string; data_type: string }> {
  return node.ui?.organizer_ports ?? []
}

export function outputsForNode(node: NodeRecord): Port[] {
  return node.interface?.outputs ?? []
}

export function assetsForNode(node: NodeRecord): Port[] {
  return node.interface?.assets ?? []
}

export function artifactsForDisplay(snapshot: ProjectSnapshot, artifacts: ArtifactRecord[]): ArtifactRecord[] {
  const nodeOrder = new Map(snapshot.graph.nodes.map((node, index) => [node.id, index]))
  const artifactOrder = new Map<string, Map<string, number>>()

  for (const node of snapshot.graph.nodes) {
    const ports = [...(node.interface?.outputs ?? []), ...(node.interface?.assets ?? [])]
    if (!ports.length) {
      continue
    }
    const order = new Map<string, number>()
    ports
      .slice()
      .sort((left, right) => {
        const leftIndex = left.declaration_index ?? Number.MAX_SAFE_INTEGER
        const rightIndex = right.declaration_index ?? Number.MAX_SAFE_INTEGER
        if (leftIndex !== rightIndex) {
          return leftIndex - rightIndex
        }
        return 0
      })
      .forEach((port, index) => {
        order.set(port.name, index)
      })
    artifactOrder.set(node.id, order)
  }

  return artifacts.slice().sort((left, right) => {
    const leftNodeIndex = nodeOrder.get(left.node_id) ?? Number.MAX_SAFE_INTEGER
    const rightNodeIndex = nodeOrder.get(right.node_id) ?? Number.MAX_SAFE_INTEGER
    if (leftNodeIndex !== rightNodeIndex) {
      return leftNodeIndex - rightNodeIndex
    }

    const leftArtifactIndex = artifactOrder.get(left.node_id)?.get(left.artifact_name) ?? Number.MAX_SAFE_INTEGER
    const rightArtifactIndex = artifactOrder.get(right.node_id)?.get(right.artifact_name) ?? Number.MAX_SAFE_INTEGER
    if (leftArtifactIndex !== rightArtifactIndex) {
      return leftArtifactIndex - rightArtifactIndex
    }

    return 0
  })
}

export function artifactFor(snapshot: ProjectSnapshot, nodeId: string, artifactName: string): ArtifactRecord | undefined {
  return snapshot.artifacts.find(
    (artifact) => artifact.node_id === nodeId && artifact.artifact_name === artifactName,
  )
}

export function inputBindingSource(snapshot: ProjectSnapshot, nodeId: string, inputName: string) {
  const direct = snapshot.graph.edges.find(
    (edge) => edge.target_node === nodeId && edge.target_port === inputName,
  )
  if (!direct) {
    return null
  }
  return resolveOutputBinding(snapshot, direct.source_node, direct.source_port)
}

export function resolveOutputBinding(snapshot: ProjectSnapshot, nodeId: string, portName: string): { source_node: string; source_port: string } | null {
  const nodeById = new Map(snapshot.graph.nodes.map((node) => [node.id, node]))
  let currentNodeId = nodeId
  let currentPortName = portName
  const visited = new Set<string>()
  while (true) {
    const signature = `${currentNodeId}:${currentPortName}`
    if (visited.has(signature)) {
      return null
    }
    visited.add(signature)
    const node = nodeById.get(currentNodeId)
    if (!node || node.kind !== 'organizer') {
      return { source_node: currentNodeId, source_port: currentPortName }
    }
    const upstream = snapshot.graph.edges.find(
      (edge) => edge.target_node === currentNodeId && edge.target_port === currentPortName,
    )
    if (!upstream) {
      return null
    }
    currentNodeId = upstream.source_node
    currentPortName = upstream.source_port
  }
}

export function inputState(snapshot: ProjectSnapshot, nodeId: string, port: Port): ArtifactState {
  const edge = inputBindingSource(snapshot, nodeId, port.name)
  if (!edge) {
    return port.has_default ? 'ready' : 'pending'
  }
  return artifactFor(snapshot, edge.source_node, edge.source_port)?.state ?? 'pending'
}

export function badgeForNode(snapshot: ProjectSnapshot, node: NodeRecord): { label: string; title: string; tone: 'input' | 'template' | 'template-modified' | 'custom' } {
  if (node.kind === 'file_input') {
    return { label: 'F', title: 'File input node', tone: 'input' }
  }
  if (node.kind === 'organizer') {
    return { label: 'O', title: 'Organizer block', tone: 'custom' }
  }
  if (node.kind === 'area') {
    return { label: 'A', title: 'Area block', tone: 'custom' }
  }
  if (node.ui?.origin === 'constant_value') {
    return { label: 'V', title: 'Constant value node', tone: 'input' }
  }
  if (node.template?.ref) {
    const unchanged = node.template_status === 'template'
    return {
      label: unchanged ? 'T' : 'T*',
      title: unchanged ? 'Template notebook' : 'Template notebook edited after creation',
      tone: unchanged ? 'template' : 'template-modified',
    }
  }
  return { label: 'C', title: 'Custom notebook', tone: 'custom' }
}

export function artifactCounts(snapshot: ProjectSnapshot, nodeId: string) {
  const nodeArtifacts = snapshot.artifacts.filter((artifact) => artifact.node_id === nodeId)
  return {
    ready: nodeArtifacts.filter((artifact) => artifact.state === 'ready').length,
    stale: nodeArtifacts.filter((artifact) => artifact.state === 'stale').length,
    pending: nodeArtifacts.filter((artifact) => artifact.state === 'pending').length,
  }
}

export function globalArtifactCounts(snapshot: ProjectSnapshot) {
  return {
    ready: snapshot.artifacts.filter((artifact) => artifact.state === 'ready').length,
    stale: snapshot.artifacts.filter((artifact) => artifact.state === 'stale').length,
    pending: snapshot.artifacts.filter((artifact) => artifact.state === 'pending').length,
  }
}

export function currentRun(snapshot: ProjectSnapshot) {
  const inflight = snapshot.runs.filter(
    (run) => (run.status === 'queued' || run.status === 'running') && run.mode !== 'edit_run',
  )
  if (!inflight.length) {
    return null
  }
  return inflight.sort((left, right) => {
    const leftTime = new Date(left.started_at ?? left.ended_at ?? 0).getTime()
    const rightTime = new Date(right.started_at ?? right.ended_at ?? 0).getTime()
    return rightTime - leftTime
  })[0]
}

export function runNodeSequence(run: ProjectSnapshot['runs'][number] | null | undefined): string[] {
  if (!run || typeof run.target_json !== 'object' || run.target_json === null) {
    return []
  }
  const sequence: string[] = []
  const target = run.target_json as Record<string, unknown>
  const plan = Array.isArray(target.plan) ? target.plan : null
  const nodeIds = Array.isArray(target.node_ids) ? target.node_ids : null
  const directNodeId = typeof target.node_id === 'string' ? target.node_id : null

  if (plan) {
    sequence.push(...plan.filter((value): value is string => typeof value === 'string'))
  }
  if (!sequence.length && nodeIds) {
    sequence.push(...nodeIds.filter((value): value is string => typeof value === 'string'))
  }
  if (!sequence.length && directNodeId) {
    sequence.push(directNodeId)
  }
  return Array.from(new Set(sequence))
}

export function activeRunNodeId(snapshot: ProjectSnapshot, run: ProjectSnapshot['runs'][number] | null | undefined): string | null {
  const activeNode = snapshot.graph.nodes.find((node) => node.orchestrator_state?.status === 'running')
  return activeNode?.id ?? null
}

export function queuedRunNodeIds(snapshot: ProjectSnapshot, run: ProjectSnapshot['runs'][number] | null | undefined): string[] {
  return snapshot.graph.nodes
    .filter((node) => node.orchestrator_state?.status === 'queued')
    .map((node) => node.id)
}

export function templateByRef(snapshot: ProjectSnapshot, ref: string | null | undefined): TemplateRecord | null {
  if (!ref) {
    return null
  }
  return snapshot.templates.find((template) => template.ref === ref) ?? null
}

export function formatType(type: string): string {
  return type.replace('pandas.', '').replace('networkx.', '')
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return 'Not available'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

export function formatBytes(value: number | null | undefined): string {
  const size = Math.max(0, value ?? 0)
  if (size < 1000) {
    return `${size} B`
  }
  if (size < 1_000_000) {
    return `${(size / 1000).toFixed(size >= 10_000 ? 0 : 1)} kB`
  }
  if (size < 1_000_000_000) {
    return `${(size / 1_000_000).toFixed(size >= 10_000_000 ? 0 : 1)} MB`
  }
  return `${(size / 1_000_000_000).toFixed(1)} GB`
}

export function formatDurationSeconds(value: number): string {
  const clampedValue = Math.max(0, value)
  if (clampedValue < 60) {
    return `${clampedValue.toFixed(1)}s`
  }
  const totalSeconds = Math.floor(clampedValue)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) {
    return `${hours}h${minutes}m${seconds}s`
  }
  if (minutes > 0) {
    return `${minutes}m${seconds}s`
  }
  return `${seconds}s`
}

export function buildNodeLookup(snapshot: ProjectSnapshot): Record<string, NodeRecord> {
  return Object.fromEntries(snapshot.graph.nodes.map((node) => [node.id, node]))
}
