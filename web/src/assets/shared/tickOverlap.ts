import { Transform, transforms, type Spec } from 'vega'
import * as Vega from 'vega'

type Bounds = {
  x1: number; x2: number; y1: number; y2: number
  clear: () => Bounds
  union: (bounds: Bounds) => Bounds
}

type LabelItem = {
  x: number
  y: number
  angle?: number
  dy?: number
  baseline?: string
  text: string | string[]
  fontSize: number
  lineHeight: number
  datum: { label: string }
  bounds: Bounds
  opacity: number
  mark: { bounds: Bounds }
}

const scene = Vega as unknown as {
  Bounds: new () => Bounds
  Marks: { text: { bound: (bounds: Bounds, item: LabelItem, mode?: number) => Bounds | number[] } }
  textMetrics: { width: (item: LabelItem, text: string) => number }
}

type Point = [number, number]

export function tickLabelPolygon(item: LabelItem): Point[] {
  const points = scene.Marks.text.bound(new scene.Bounds(), item, 2) as number[]
  return [0, 2, 6, 4].map((index) => [points[index], points[index + 1]])
}

export function tickPolygonsOverlap(a: Point[], b: Point[], separation = 0): boolean {
  // Separating-axis test on oriented text rectangles, not their oversized
  // axis-aligned envelopes (which wrongly hide parallel 45-degree labels).
  for (const polygon of [a, b]) {
    for (let i = 0; i < 2; i += 1) {
      const start = polygon[i]
      const end = polygon[i + 1]
      const length = Math.hypot(end[0] - start[0], end[1] - start[1])
      if (!length) continue
      const nx = -(end[1] - start[1]) / length
      const ny = (end[0] - start[0]) / length
      const project = (points: Point[]) => points.map(([x, y]) => x * nx + y * ny)
      const ap = project(a)
      const bp = project(b)
      if (Math.max(...ap) + separation <= Math.min(...bp) || Math.max(...bp) + separation <= Math.min(...ap)) return false
    }
  }
  return true
}

function wrapLabel(item: LabelItem, text: string, width: number): string[] {
  const lines: string[] = []
  for (const paragraph of text.split(/\r?\n/)) {
    let line = ''
    for (const word of paragraph.split(/\s+/)) {
      if (line && scene.textMetrics.width(item, `${line} ${word}`) > width) {
        lines.push(line)
        line = ''
      }
      for (const character of Array.from(`${line ? ' ' : ''}${word}`)) {
        if (line && scene.textMetrics.width(item, line + character) > width) {
          lines.push(line)
          line = ''
        }
        line += character
      }
    }
    lines.push(line)
  }
  return lines
}

function ellipsize(item: LabelItem, text: string, width: number): string {
  let result = text
  while (result && scene.textMetrics.width(item, `${result}...`) > width) {
    result = Array.from(result).slice(0, -1).join('')
  }
  return `${result}...`
}

const originalOffsets = new WeakMap<LabelItem, number>()

function centerLabelBlock(item: LabelItem) {
  if (!originalOffsets.has(item)) originalOffsets.set(item, item.dy ?? 0)
  item.dy = originalOffsets.get(item)!
  if (item.baseline === 'middle') {
    const unrotated = scene.Marks.text.bound(new scene.Bounds(), item, 1) as Bounds
    item.dy -= (unrotated.y1 + unrotated.y2) / 2 - item.y - item.dy
  }
  scene.Marks.text.bound(item.bounds.clear(), item)
}

function fitCategoryLabels(source: LabelItem[], separation: number) {
  const sorted = source.slice().sort((a, b) => a.x - b.x)
  const gaps = sorted.slice(1).map((item, index) => item.x - sorted[index].x)
  const slot = Math.max(1, (gaps.length ? Math.min(...gaps) : 144) - separation)
  for (const item of source) {
    item.lineHeight = item.fontSize * 1.2
    const angle = Math.abs(Math.sin((item.angle ?? 0) * Math.PI / 180))
    const text = String(item.datum.label)
    if (angle < 0.001) {
      // Each horizontal label owns exactly one category slot, regardless of
      // bar width, grouping, or label length. Preserve the full text by wrapping.
      item.text = wrapLabel(item, text, slot).map((line) => (
        scene.textMetrics.width(item, line) > slot ? ellipsize(item, line, slot) : line
      ))
    } else {
      // Rotated labels can extend away from the axis. Their perpendicular
      // thickness, rather than the length of their bounding box, is limited.
      const thickness = Math.max(0, (slot + separation) * angle - separation)
      const maxLines = Math.max(0, 1 + Math.floor((thickness - item.fontSize) / item.lineHeight))
      const lines = wrapLabel(item, text, 140)
      item.text = maxLines === 0 ? ['...'] : lines.slice(0, maxLines)
      if (maxLines > 0 && lines.length > maxLines) {
        item.text[maxLines - 1] = ellipsize(item, item.text[maxLines - 1], 140)
      }
    }
    scene.Marks.text.bound(item.bounds.clear(), item)
  }
}

type LabelPulse = {
  SOURCE: number
  source: LabelItem[]
  materialize: (flags: number) => LabelPulse
  reflow: (force: boolean) => LabelPulse
  modifies: (property: string) => LabelPulse
}

// Vega's built-in overlap transform deliberately preserves two endpoint labels,
// even when they overlap. Axis labels need a strict check with no such exception.
const VegaOverlap = transforms.overlap as unknown as typeof Transform

class StrictTickOverlap extends VegaOverlap {
  transform(params: { method?: string; separation?: number; modified: () => boolean }, pulse: LabelPulse) {
    if (params.method !== 'strict-ticks' && params.method !== 'fit-category-ticks') return super.transform(params, pulse)
    const source = pulse.materialize(pulse.SOURCE).source
    if (!source?.length) return pulse
    let separation = params.separation ?? 0
    if (params.method === 'fit-category-ticks' && source.length > 1) {
      const positions = source.map((item) => item.x).sort((a, b) => a - b)
      const gap = Math.min(...positions.slice(1).map((position, index) => position - positions[index]))
      separation = Math.min(separation, gap * 0.15)
    }
    if (params.method === 'fit-category-ticks') fitCategoryLabels(source, separation)
    source.forEach(centerLabelBlock)
    const visible: Point[][] = []
    const bounds = source[0].mark.bounds.clear()
    for (const item of source) {
      const b = item.bounds
      const polygon = tickLabelPolygon(item)
      const overlaps = visible.some((other) => tickPolygonsOverlap(other, polygon, separation))
      item.opacity = overlaps ? 0 : 1
      if (!overlaps) {
        visible.push(polygon)
        bounds.union(b)
      }
    }
    return pulse.reflow(params.modified()).modifies('opacity').modifies('text').modifies('dy').modifies('lineHeight').modifies('bounds')
  }
}

transforms.overlap = StrictTickOverlap as unknown as Transform

export function enforceTickLabelBounds(spec: Spec): Spec {
  const categoryScales = new Set<string>()
  function collect(value: unknown) {
    if (Array.isArray(value)) value.forEach(collect)
    else if (value && typeof value === 'object') {
      const record = value as Record<string, unknown>
      if (record.type === 'band' && typeof record.name === 'string'
        && JSON.stringify(record.domain).includes('"category_label"')) categoryScales.add(record.name)
      Object.values(record).forEach(collect)
    }
  }
  collect(spec)
  function visit(value: unknown) {
    if (Array.isArray(value)) {
      value.forEach(visit)
    } else if (value && typeof value === 'object') {
      const record = value as Record<string, unknown>
      if (record.labelOverlap === 'greedy') {
        record.labelOverlap = record.orient === 'bottom' && categoryScales.has(String(record.scale))
          ? 'fit-category-ticks' : 'strict-ticks'
      }
      Object.values(record).forEach(visit)
    }
  }
  visit(spec)
  return spec
}
