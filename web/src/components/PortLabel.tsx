import { formatType } from '../lib/helpers'

export const TYPE_COLORS: Record<string, string> = {
  int: '#facc15',
  float: '#facc15',
  bool: '#14b8a6',
  str: '#7dd3fc',
  list: '#8b5cf6',
  dict: '#a78bfa',
  file: '#ef4444',
  object: '#9ca3af',
  'pandas.DataFrame': '#009dff',
  'pandas.Series': '#9ca3af',
  'networkx.Graph': '#ec4899',
  'networkx.DiGraph': '#ec4899',
}

type PortLabelProps = {
  name: string
  label?: string | null
  dataType: string
  className: string
  title?: string
  showTypeDot?: boolean
  typeDotPosition?: 'before' | 'after'
}

export function displayPortName({ name, label }: { name: string; label?: string | null }): string {
  return label?.trim() || name
}

export function PortLabel({ name, label, dataType, className, title, showTypeDot = false, typeDotPosition = 'after' }: PortLabelProps) {
  const displayName = displayPortName({ name, label })
  const typeColor = TYPE_COLORS[dataType] ?? TYPE_COLORS.object

  return (
    <div className={className} title={title ?? `${displayName} (${dataType})`}>
      <strong>{displayName}</strong>
      <span className={`port-type-label ${typeDotPosition === 'before' ? 'dot-before' : 'dot-after'}`}>
        {showTypeDot && typeDotPosition === 'before' ? <span className="port-type-dot" style={{ backgroundColor: typeColor }} aria-hidden="true" /> : null}
        <span className="port-type-text">{formatType(dataType)}</span>
        {showTypeDot && typeDotPosition === 'after' ? <span className="port-type-dot" style={{ backgroundColor: typeColor }} aria-hidden="true" /> : null}
      </span>
    </div>
  )
}
