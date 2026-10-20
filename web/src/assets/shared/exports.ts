import { embeddedChartFontCss, ensureChartFontsLoaded } from './fonts.ts'

const PNG_MAX_DIMENSION = 4000

export async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text)
}

export function downloadText(text: string, filename: string, type: string): void {
  downloadBlob(new Blob([text], { type }), filename)
}

export function tableToMarkdown(columns: string[], rows: Array<Record<string, unknown>>): string {
  const header = `| ${columns.map(escapeMarkdownCell).join(' | ')} |`
  const separator = `| ${columns.map(() => '---').join(' | ')} |`
  const body = rows.map((row) => `| ${columns.map((column) => escapeMarkdownCell(formatTableValue(row[column]))).join(' | ')} |`)
  return [header, separator, ...body].join('\n')
}

export function tableToCsv(columns: string[], rows: Array<Record<string, unknown>>): string {
  return [
    columns.map(escapeCsvCell).join(','),
    ...rows.map((row) => columns.map((column) => escapeCsvCell(formatTableValue(row[column]))).join(',')),
  ].join('\r\n')
}

export function assetExportFilename(title: string, extension: string): string {
  const stem = title.trim().replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'asset'
  return `${stem}.${extension}`
}

export async function downloadSvg(svg: SVGSVGElement, filename: string): Promise<void> {
  downloadBlob(new Blob([await serializeSvg(svg, chartBackgroundColor())], { type: 'image/svg+xml;charset=utf-8' }), filename)
}

export async function downloadPng(svg: SVGSVGElement, filename: string): Promise<void> {
  downloadBlob(await svgToPng(svg, chartBackgroundColor()), filename)
}

export async function copyPng(svg: SVGSVGElement): Promise<void> {
  if (typeof ClipboardItem === 'undefined' || !navigator.clipboard.write) {
    throw new Error('Copying images is not supported by this browser.')
  }
  // Pass the promise immediately to preserve the click's user activation in Safari.
  const blob = svgToPng(svg, chartBackgroundColor())
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
}

async function serializeSvg(svg: SVGSVGElement, background: string | null = null): Promise<string> {
  await ensureChartFontsLoaded()
  const clone = svg.cloneNode(true) as SVGSVGElement
  if (!clone.hasAttribute('xmlns')) {
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  }
  const sourceText = [...svg.querySelectorAll<SVGElement>('text, tspan')]
  const clonedText = [...clone.querySelectorAll<SVGElement>('text, tspan')]
  const textStyles = sourceText.map((text, index) => {
    const styles = getComputedStyle(text)
    // Preserve inherited font properties when the SVG leaves the dashboard DOM.
    for (const property of ['font-family', 'font-weight', 'font-style', 'font-size']) {
      clonedText[index].style.setProperty(property, styles.getPropertyValue(property))
    }
    return { fontFamily: styles.fontFamily, fontWeight: styles.fontWeight }
  })
  const fontCss = await embeddedChartFontCss(textStyles)
  if (fontCss) {
    const style = document.createElementNS('http://www.w3.org/2000/svg', 'style')
    style.textContent = fontCss
    clone.insertBefore(style, clone.firstChild)
  }
  if (background) {
    const bounds = svg.getBoundingClientRect()
    const viewBox = svg.viewBox.baseVal
    const width = viewBox.width || bounds.width || Number(svg.getAttribute('width')) || 1
    const height = viewBox.height || bounds.height || Number(svg.getAttribute('height')) || 1
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
    rect.setAttribute('x', String(viewBox.x || 0))
    rect.setAttribute('y', String(viewBox.y || 0))
    rect.setAttribute('width', String(width))
    rect.setAttribute('height', String(height))
    rect.setAttribute('fill', background)
    rect.setAttribute('pointer-events', 'none')
    clone.insertBefore(rect, clone.firstChild)
  }
  return new XMLSerializer().serializeToString(clone)
}

async function svgToPng(svg: SVGSVGElement, background: string): Promise<Blob> {
  const serialized = await serializeSvg(svg, background)
  const bounds = svg.getBoundingClientRect()
  const viewBox = svg.viewBox.baseVal
  const sourceWidth = bounds.width || viewBox.width || Number(svg.getAttribute('width')) || 1
  const sourceHeight = bounds.height || viewBox.height || Number(svg.getAttribute('height')) || 1
  const scale = PNG_MAX_DIMENSION / Math.max(sourceWidth, sourceHeight)
  const width = Math.max(1, Math.round(sourceWidth * scale))
  const height = Math.max(1, Math.round(sourceHeight * scale))
  const url = URL.createObjectURL(new Blob([serialized], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const image = await loadImage(url)
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) {
      throw new Error('Could not create an image canvas.')
    }
    context.fillStyle = background
    context.fillRect(0, 0, width, height)
    context.drawImage(image, 0, 0, width, height)
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('Could not render the PNG.')), 'image/png')
    })
  } finally {
    URL.revokeObjectURL(url)
  }
}

export function chartBackgroundColor(): string {
  return document.documentElement.dataset.theme === 'dark' ? '#000000' : '#ffffff'
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('Could not load the rendered chart.'))
    image.src = url
  })
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

function formatTableValue(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function escapeMarkdownCell(value: unknown): string {
  return String(value).replace(/\\/g, '\\\\').replace(/\|/g, '\\|').replace(/\r?\n/g, '<br>')
}

function escapeCsvCell(value: unknown): string {
  const text = String(value)
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}
