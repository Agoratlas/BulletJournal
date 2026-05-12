import { formatType } from '../lib/helpers'

export const TYPE_COLORS: Record<string, string> = {
  int: '#facc15',
  float: '#facc15',
  bool: '#5eead4',
  str: '#818cf8',
  list: '#f472b6',
  dict: '#c084fc',
  file: '#f87171',
  object: '#cbd5e1',
  'pandas.DataFrame': '#009dff',
  'pandas.Series': '#94a3b8',
  'networkx.Graph': '#a78bfa',
  'networkx.DiGraph': '#a78bfa',
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
