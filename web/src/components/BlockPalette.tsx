import { useRef } from 'react'

import type { PaletteEntry, PalettePreviewBlock } from '../appTypes'

export function BlockPalette({
  entries,
  onCreate,
  onInspectTemplate,
  onDragStart,
  onDragEnd,
  previewScale,
}: {
  entries: PaletteEntry[]
  onCreate: (entry: PaletteEntry) => Promise<void>
  onInspectTemplate: (ref: string) => void
  onDragStart: (entry: PaletteEntry, position?: { x: number; y: number }) => void
  onDragEnd: () => void
  previewScale: number
}) {
  const dragPreviewRef = useRef<HTMLDivElement | null>(null)
  const sections = [
    {
      title: 'Core blocks',
      items: entries.filter((entry) => entry.kind === 'empty' || entry.kind === 'value_input' || entry.kind === 'file_input' || entry.kind === 'organizer' || entry.kind === 'area'),
    },
    {
      title: 'Notebook templates',
      items: entries.filter((entry) => entry.kind === 'template'),
    },
    {
      title: 'Pipeline templates',
      items: entries.filter((entry) => entry.kind === 'pipeline'),
    },
  ]

  return (
    <div className="block-palette">
      {sections.map((section) => (
        <section key={section.title} className="palette-section">
          <h3>{section.title}</h3>
          <div className="stack-list templates-list">
            {section.items.map((entry) => {
              return (
                <div key={entry.key} className="template-tile palette-tile">
                  <button
                    className="palette-main draggable-block"
                    onClick={() => void onCreate(entry)}
                    draggable
                    onDragStart={(event) => {
                      const preview = createPaletteDragPreview(entry, previewScale)
                      dragPreviewRef.current?.remove()
                      dragPreviewRef.current = preview
                      document.body.appendChild(preview)
                      event.dataTransfer.effectAllowed = 'copy'
                      event.dataTransfer.setData('text/plain', entry.key)
                      event.dataTransfer.setDragImage(
                        preview,
                        preview.offsetWidth / 2,
                        preview.offsetHeight / 2,
                      )
                      onDragStart(entry, { x: event.clientX, y: event.clientY })
                    }}
                    onDragEnd={() => {
                      dragPreviewRef.current?.remove()
                      dragPreviewRef.current = null
                      onDragEnd()
                    }}
                  >
                    <strong>{entry.title}</strong>
                    <span>{entry.description}</span>
                  </button>
                  {entry.kind === 'template' || entry.kind === 'value_input' || entry.kind === 'pipeline' ? (
                    <button className="secondary small" onClick={(event) => {
                      event.stopPropagation()
                      if (entry.templateRef) {
                        onInspectTemplate(entry.templateRef)
                      }
                    }}>
                      View
                    </button>
                  ) : null}
                </div>
              )
            })}
            {!section.items.length ? <p className="muted-copy">No matching blocks.</p> : null}
          </div>
        </section>
      ))}
    </div>
  )
}

function createPaletteDragPreview(entry: PaletteEntry, previewScale: number): HTMLDivElement {
  const scale = Math.max(previewScale, 0.18)
  const previewBlocks = entry.previewBlocks?.length ? entry.previewBlocks : [defaultPreviewBlock(entry)]
  const preview = document.createElement('div')
  preview.className = `palette-drag-preview kind-${entry.kind}`
  preview.style.width = `${Math.max((entry.previewSize?.width ?? 360) * scale, 1)}px`
  preview.style.height = `${Math.max((entry.previewSize?.height ?? 220) * scale, 1)}px`
  preview.style.setProperty('--palette-preview-scale', String(scale))

  const canvas = document.createElement('div')
  canvas.className = 'palette-drag-preview-canvas'
  preview.appendChild(canvas)

  for (const block of previewBlocks) {
    canvas.appendChild(createPalettePreviewBlock(block, scale))
  }

  return preview
}

function createPalettePreviewBlock(block: PalettePreviewBlock, scale: number): HTMLDivElement {
  const node = document.createElement('div')
  node.className = `palette-drag-preview-node kind-${block.kind}`
  node.style.left = `${block.x * scale}px`
  node.style.top = `${block.y * scale}px`
  node.style.width = `${Math.max(block.width * scale, 1)}px`
  node.style.height = `${Math.max(block.height * scale, 1)}px`

  const header = document.createElement('div')
  header.className = 'palette-drag-preview-node-header'

  const badge = document.createElement('div')
  badge.className = 'palette-drag-preview-badge'
  badge.textContent = block.kind === 'file_input' ? 'F' : block.kind === 'organizer' ? 'O' : block.kind === 'area' ? 'A' : 'N'
  header.appendChild(badge)

  const copy = document.createElement('div')
  copy.className = 'palette-drag-preview-copy'

  const title = document.createElement('strong')
  title.textContent = block.title
  copy.appendChild(title)

  const subtitle = document.createElement('span')
  subtitle.textContent = block.kind === 'file_input' ? 'File input' : block.kind === 'organizer' ? 'Organizer' : block.kind === 'area' ? 'Area' : 'Notebook'
  copy.appendChild(subtitle)
  header.appendChild(copy)

  const body = document.createElement('div')
  body.className = 'palette-drag-preview-node-body'

  const footer = document.createElement('div')
  footer.className = 'palette-drag-preview-node-footer'

  node.appendChild(header)
  node.appendChild(body)
  node.appendChild(footer)
  return node
}

function defaultPreviewBlock(entry: PaletteEntry): PalettePreviewBlock {
  return {
    key: entry.key,
    title: entry.title,
    kind: entry.kind === 'file_input' ? 'file_input' : entry.kind === 'organizer' ? 'organizer' : entry.kind === 'area' ? 'area' : 'notebook',
    x: 0,
    y: 0,
    width: entry.previewSize?.width ?? (entry.kind === 'organizer' ? 160 : entry.kind === 'area' ? 320 : 360),
    height: entry.previewSize?.height ?? (entry.kind === 'organizer' ? 140 : entry.kind === 'area' ? 220 : 220),
  }
}
