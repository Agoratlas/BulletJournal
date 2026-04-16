import { formatType } from '../lib/helpers'

export const TYPE_COLORS: Record<string, string> = {
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

type PortLabelProps = {
  name: string
  label?: string | null
  dataType: string
  className: string
  title?: string
}

export function displayPortName({ name, label }: { name: string; label?: string | null }): string {
  return label?.trim() || name
}

export function PortLabel({ name, label, dataType, className, title }: PortLabelProps) {
  const displayName = displayPortName({ name, label })

  return (
    <div className={className} title={title ?? `${displayName} (${dataType})`}>
      <strong>{displayName}</strong>
      <span>{formatType(dataType)}</span>
    </div>
  )
}
