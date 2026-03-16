import type { ArtifactState } from '../lib/types'
import { formatType } from '../lib/helpers'

const TYPE_COLORS: Record<string, string> = {
  int: '#bf6a02',
  float: '#c05621',
  bool: '#2f855a',
  str: '#0f766e',
  list: '#2563eb',
  dict: '#4c51bf',
  file: '#7c3aed',
  object: '#6b7280',
  'pandas.DataFrame': '#0f766e',
  'pandas.Series': '#3b82f6',
  'networkx.Graph': '#b45309',
  'networkx.DiGraph': '#92400e',
}

const STATE_COLORS: Record<ArtifactState | 'mixed', string> = {
  ready: '#2f855a',
  stale: '#c97c00',
  pending: '#9aa19a',
  mixed: '#2563eb',
}

type PortPillProps = {
  name: string
  dataType: string
  state: ArtifactState | 'mixed'
  side: 'input' | 'output'
  compact?: boolean
}

export function PortPill({ name, dataType, state, side, compact = false }: PortPillProps) {
  const typeColor = TYPE_COLORS[dataType] ?? TYPE_COLORS.object
  const stateColor = STATE_COLORS[state]

  return (
    <div className={`port-pill port-pill-${side} ${compact ? 'compact' : ''}`} title={`${name} (${dataType})`}>
      {side === 'output' ? null : <span className="port-circle" style={{ borderColor: typeColor, backgroundColor: stateColor }} />}
      <div className="port-copy">
        <strong>{name}</strong>
        <span>{formatType(dataType)}</span>
      </div>
      {side === 'output' ? <span className="port-circle" style={{ borderColor: typeColor, backgroundColor: stateColor }} /> : null}
    </div>
  )
}
