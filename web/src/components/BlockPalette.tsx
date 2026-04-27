import { useMemo, useRef, useState } from 'react'

import type { PaletteEntry, PalettePreviewBlock } from '../appTypes'
import { CONSTANT_NODE_HEIGHT, CONSTANT_NODE_WIDTH } from '../lib/helpers'
import { ChevronDown } from './Icons'

type PaletteTreeNode = PaletteDirectoryNode | PaletteEntryNode

type PaletteEntryNode = {
  kind: 'entry'
  key: string
  entry: PaletteEntry
}

type PaletteDirectoryNode = {
  kind: 'directory'
  key: string
  label: string
  children: PaletteTreeNode[]
  defaultExpanded?: boolean
  emptyLabel?: string
  _directories?: Map<string, PaletteDirectoryNode>
}

export function BlockPalette({
  entries,
  groupTemplatesByProvider,
  searchActive,
  onCreate,
  onInspectEntry,
  onDragStart,
  onDragEnd,
  previewScale,
}: {
  entries: PaletteEntry[]
  groupTemplatesByProvider: boolean
  searchActive: boolean
  onCreate: (entry: PaletteEntry) => Promise<void>
  onInspectEntry: (entry: PaletteEntry) => void
  onDragStart: (entry: PaletteEntry, position?: { x: number; y: number }) => void
  onDragEnd: () => void
  previewScale: number
}) {
  const dragPreviewRef = useRef<HTMLDivElement | null>(null)
  const [expandedDirectories, setExpandedDirectories] = useState<Record<string, boolean>>({})
  const sections = useMemo(
    () => buildPaletteSections(entries, groupTemplatesByProvider),
    [entries, groupTemplatesByProvider],
  )

  function isExpanded(node: PaletteDirectoryNode): boolean {
    if (searchActive) {
      return true
    }
    return expandedDirectories[node.key] ?? node.defaultExpanded ?? false
  }

  function toggleDirectory(node: PaletteDirectoryNode) {
    setExpandedDirectories((current) => ({
      ...current,
      [node.key]: !(current[node.key] ?? node.defaultExpanded ?? false),
    }))
  }

  function renderNode(node: PaletteTreeNode, depth: number): JSX.Element {
    if (node.kind === 'entry') {
      return renderEntryNode(node)
    }
    const expanded = isExpanded(node)
    return (
      <div key={node.key} className={`palette-directory depth-${depth} ${expanded ? 'is-expanded' : 'is-collapsed'}`}>
        <button
          type="button"
          className="palette-directory-toggle"
          onClick={() => toggleDirectory(node)}
          aria-expanded={expanded}
        >
          <ChevronDown />
          <span>{node.label}</span>
        </button>
        {expanded ? (
          <div className="palette-directory-children">
            {node.children.length
              ? node.children.map((child) => renderNode(child, depth + 1))
              : <p className="muted-copy palette-empty-message">{node.emptyLabel ?? 'No matching blocks.'}</p>}
          </div>
        ) : null}
      </div>
    )
  }

  function renderEntryNode(node: PaletteEntryNode): JSX.Element {
    const detail = node.entry.description?.trim() || node.entry.documentation?.split(/\r?\n/, 1)[0]?.trim() || ''
    const title = detail && detail !== node.entry.title ? `${node.entry.title}\n${detail}` : node.entry.title
    return (
      <div key={node.key} className="palette-entry-row">
        <button
          type="button"
          className="palette-entry-action draggable-block"
          title={title}
          onClick={() => void onCreate(node.entry)}
          draggable
          onDragStart={(event) => {
            const preview = createPaletteDragPreview(node.entry, previewScale)
            dragPreviewRef.current?.remove()
            dragPreviewRef.current = preview
            document.body.appendChild(preview)
            event.dataTransfer.effectAllowed = 'copy'
            event.dataTransfer.setData('text/plain', node.entry.key)
            event.dataTransfer.setDragImage(
              preview,
              preview.offsetWidth / 2,
              preview.offsetHeight / 2,
            )
            onDragStart(node.entry, { x: event.clientX, y: event.clientY })
          }}
          onDragEnd={() => {
            dragPreviewRef.current?.remove()
            dragPreviewRef.current = null
            onDragEnd()
          }}
        >
          <span className="palette-entry-label">{node.entry.title}</span>
        </button>
        <button
          type="button"
          className="secondary palette-entry-info"
          aria-label={`Show information for ${node.entry.title}`}
          title={`Show information for ${node.entry.title}`}
          onClick={(event) => {
            event.stopPropagation()
            onInspectEntry(node.entry)
          }}
        >
          <em>i</em>
        </button>
      </div>
    )
  }

  return (
    <div className="block-palette">
      {sections.map((section) => renderNode(section, 0))}
    </div>
  )
}

function buildPaletteSections(entries: PaletteEntry[], groupTemplatesByProvider: boolean): PaletteDirectoryNode[] {
  return [
    createSection(
      'palette:core',
      'Core blocks',
      entries.filter((entry) => entry.kind === 'empty' || entry.kind === 'constant' || entry.kind === 'organizer' || entry.kind === 'area'),
    ),
    createTemplateSection(
      'palette:pipelines',
      'Pipeline templates',
      entries.filter((entry) => entry.kind === 'pipeline'),
      groupTemplatesByProvider,
    ),
    createTemplateSection(
      'palette:notebooks',
      'Notebook templates',
      entries.filter((entry) => entry.kind === 'template'),
      groupTemplatesByProvider,
    ),
  ]
}

function createSection(key: string, label: string, entries: PaletteEntry[]): PaletteDirectoryNode {
  return {
    kind: 'directory',
    key,
    label,
    children: entries.map((entry) => ({ kind: 'entry', key: entry.key, entry })),
    defaultExpanded: true,
    emptyLabel: 'No matching blocks.',
  }
}

function createTemplateSection(
  key: string,
  label: string,
  entries: PaletteEntry[],
  groupTemplatesByProvider: boolean,
): PaletteDirectoryNode {
  const root = createDirectoryNode(key, label, true)
  if (groupTemplatesByProvider) {
    const providerEntries = new Map<string, PaletteEntry[]>()
    for (const entry of entries) {
      const provider = entry.templateProvider ?? 'unknown'
      const current = providerEntries.get(provider)
      if (current) {
        current.push(entry)
      } else {
        providerEntries.set(provider, [entry])
      }
    }
    for (const [provider, providerGroup] of providerEntries) {
      root.children.push(buildTemplateDirectoryTree(`${key}/${provider}`, provider, providerGroup))
    }
    return root
  }
  appendTemplateEntries(root, entries)
  return root
}

function buildTemplateDirectoryTree(key: string, label: string, entries: PaletteEntry[]): PaletteDirectoryNode {
  const root = createDirectoryNode(key, label, false)
  appendTemplateEntries(root, entries)
  return root
}

function appendTemplateEntries(root: PaletteDirectoryNode, entries: PaletteEntry[]) {
  for (const entry of entries) {
    const pathSegments = (entry.templateName ?? '')
      .split('/')
      .map((segment) => segment.trim())
      .filter(Boolean)
    let current = root
    for (const segment of pathSegments.slice(0, -1)) {
      current = ensureChildDirectory(current, segment)
    }
    current.children.push({ kind: 'entry', key: entry.key, entry })
  }
}

function createDirectoryNode(key: string, label: string, defaultExpanded: boolean): PaletteDirectoryNode {
  return {
    kind: 'directory',
    key,
    label,
    children: [],
    defaultExpanded,
    emptyLabel: 'No matching blocks.',
    _directories: new Map(),
  }
}

function ensureChildDirectory(parent: PaletteDirectoryNode, label: string): PaletteDirectoryNode {
  const directories = parent._directories ?? new Map<string, PaletteDirectoryNode>()
  parent._directories = directories
  const existing = directories.get(label)
  if (existing) {
    return existing
  }
  const directory = createDirectoryNode(`${parent.key}/${label}`, label, false)
  directories.set(label, directory)
  parent.children.push(directory)
  return directory
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

  if (block.kind === 'area') {
    return node
  }

  if (block.kind === 'constant') {
    node.textContent = 'Constant'
    return node
  }

  const header = document.createElement('div')
  header.className = 'palette-drag-preview-node-header'

  const badge = document.createElement('div')
  badge.className = 'palette-drag-preview-badge'
  badge.textContent = block.kind === 'organizer' ? 'O' : 'N'
  header.appendChild(badge)

  const copy = document.createElement('div')
  copy.className = 'palette-drag-preview-copy'

  const title = document.createElement('strong')
  title.textContent = block.title
  copy.appendChild(title)

  const subtitle = document.createElement('span')
  subtitle.textContent = block.kind === 'organizer' ? 'Organizer' : 'Notebook'
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
    kind: entry.kind === 'constant' ? 'constant' : entry.kind === 'organizer' ? 'organizer' : entry.kind === 'area' ? 'area' : 'notebook',
    x: 0,
    y: 0,
    width: entry.previewSize?.width ?? (entry.kind === 'constant' ? CONSTANT_NODE_WIDTH : entry.kind === 'organizer' ? 160 : entry.kind === 'area' ? 320 : 360),
    height: entry.previewSize?.height ?? (entry.kind === 'constant' ? CONSTANT_NODE_HEIGHT : entry.kind === 'organizer' ? 140 : entry.kind === 'area' ? 220 : 220),
  }
}
