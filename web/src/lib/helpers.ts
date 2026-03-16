import type { ArtifactRecord, ArtifactState, NodeRecord, Port, ProjectSnapshot, TemplateRecord } from './types'

export const GRID_SIZE = 20

export function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '') || 'node'
}

export function hiddenInputNames(node: NodeRecord): Set<string> {
  return new Set(node.ui?.hidden_inputs ?? [])
}

export function visibleInputs(node: NodeRecord): Port[] {
  const hidden = hiddenInputNames(node)
  return (node.interface?.inputs ?? []).filter((port) => !hidden.has(port.name))
}

export function hiddenInputs(node: NodeRecord): Port[] {
  const hidden = hiddenInputNames(node)
  return (node.interface?.inputs ?? []).filter((port) => hidden.has(port.name))
}

export function outputsForNode(node: NodeRecord): Port[] {
  return node.interface?.outputs ?? []
}

export function assetsForNode(node: NodeRecord): Port[] {
  return node.interface?.assets ?? []
}

export function artifactFor(snapshot: ProjectSnapshot, nodeId: string, artifactName: string): ArtifactRecord | undefined {
  return snapshot.artifacts.find(
    (artifact) => artifact.node_id === nodeId && artifact.artifact_name === artifactName,
  )
}

export function inputBindingSource(snapshot: ProjectSnapshot, nodeId: string, inputName: string) {
  return snapshot.graph.edges.find(
    (edge) => edge.target_node === nodeId && edge.target_port === inputName,
  )
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
  if (node.ui?.origin === 'constant_value') {
    return { label: 'V', title: 'Constant value node', tone: 'input' }
  }
  if (node.template?.ref) {
    const template = templateByRef(snapshot, node.template.ref)
    const actualHash = node.interface?.source_hash
    const unchanged = Boolean(template?.source_hash && actualHash === template.source_hash)
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

export function buildNodeLookup(snapshot: ProjectSnapshot): Record<string, NodeRecord> {
  return Object.fromEntries(snapshot.graph.nodes.map((node) => [node.id, node]))
}
