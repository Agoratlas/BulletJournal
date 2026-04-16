import { PortLabel, TYPE_COLORS } from './PortLabel'

type PortPillProps = {
  name: string
  label?: string | null
  dataType: string
  side: 'input' | 'output'
  compact?: boolean
}

export function PortPill({ name, label, dataType, side, compact = false }: PortPillProps) {
  const typeColor = TYPE_COLORS[dataType] ?? TYPE_COLORS.object

  return (
    <div className={`port-pill port-pill-${side} ${compact ? 'compact' : ''}`}>
      <span className="port-circle" style={{ backgroundColor: typeColor }} />
      <PortLabel name={name} label={label} dataType={dataType} className="port-copy" />
    </div>
  )
}
