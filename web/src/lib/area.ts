import type { NodeRecord } from './types'

export const AREA_TITLE_POSITIONS = [
  'top-left',
  'top-center',
  'top-right',
  'right-center',
  'bottom-right',
  'bottom-center',
  'bottom-left',
  'left-center',
] as const

export const AREA_COLOR_KEYS = [
  'red',
  'orange',
  'yellow',
  'green',
  'blue',
  'purple',
  'white',
  'black',
] as const

export type AreaTitlePosition = typeof AREA_TITLE_POSITIONS[number]
export type AreaColorKey = typeof AREA_COLOR_KEYS[number]

export function areaSettings(node: Pick<NodeRecord, 'ui'>): {
  titlePosition: AreaTitlePosition
  color: AreaColorKey
  filled: boolean
} {
  const titlePosition = AREA_TITLE_POSITIONS.includes((node.ui?.title_position ?? 'top-left') as AreaTitlePosition)
    ? (node.ui?.title_position as AreaTitlePosition)
    : 'top-left'
  const color = AREA_COLOR_KEYS.includes((node.ui?.area_color ?? 'blue') as AreaColorKey)
    ? (node.ui?.area_color as AreaColorKey)
    : 'blue'
  return {
    titlePosition,
    color,
    filled: node.ui?.area_filled ?? true,
  }
}
