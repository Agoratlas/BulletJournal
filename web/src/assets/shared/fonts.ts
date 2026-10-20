import { appUrl } from '../../lib/api.ts'

export const VEGA_PANEL_FONT_FAMILY = 'Barlow'

const fontFiles = {
  medium: { filename: 'Barlow-Medium.woff2', weights: '400 500' },
  bold: { filename: 'Barlow-Bold.woff2', weights: '600 700' },
} as const

const embeddedFonts = new Map<string, Promise<string>>()
let chartFontsReady: Promise<void> | null = null

export function ensureChartFontsLoaded(): Promise<void> {
  if (chartFontsReady === null) {
    chartFontsReady = Promise.all([400, 500, 600, 700].map(async (weight) => {
      const faces = await document.fonts.load(`${weight} 12px "${VEGA_PANEL_FONT_FAMILY}"`)
      if (!faces.length) {
        throw new Error('Could not load the chart font.')
      }
    })).then(() => undefined).catch((error) => {
      chartFontsReady = null
      throw error
    })
  }
  return chartFontsReady
}

export async function embeddedChartFontCss(textStyles: Array<Pick<CSSStyleDeclaration, 'fontFamily' | 'fontWeight'>>): Promise<string> {
  const usedFonts = new Set<keyof typeof fontFiles>()
  for (const styles of textStyles) {
    const families = styles.fontFamily.split(',').map((family) => family.trim().replace(/^["']|["']$/g, '').toLowerCase())
    if (families.includes(VEGA_PANEL_FONT_FAMILY.toLowerCase())) {
      const weight = styles.fontWeight === 'bold' ? 700 : Number(styles.fontWeight) || 400
      usedFonts.add(weight <= 500 ? 'medium' : 'bold')
    }
  }
  return (await Promise.all([...usedFonts].map(async (name) => {
    const font = fontFiles[name]
    const dataUrl = await embeddedFontDataUrl(font.filename)
    return `@font-face { font-family: '${VEGA_PANEL_FONT_FAMILY}'; font-style: normal; font-weight: ${font.weights}; src: url('${dataUrl}') format('woff2'); }`
  }))).join('\n')
}

function embeddedFontDataUrl(filename: string): Promise<string> {
  let pending = embeddedFonts.get(filename)
  if (!pending) {
    pending = (async () => {
      const response = await fetch(appUrl(`/fonts/${filename}`))
      if (!response.ok) {
        throw new Error(`Could not load the chart font (${filename}).`)
      }
      const bytes = new Uint8Array(await response.arrayBuffer())
      // A missing static asset may return the SPA's HTML with a successful status.
      if (bytes.length < 4 || String.fromCharCode(...bytes.subarray(0, 4)) !== 'wOF2') {
        throw new Error(`Could not load the chart font (${filename}).`)
      }
      let binary = ''
      for (const byte of bytes) binary += String.fromCharCode(byte)
      return `data:font/woff2;base64,${btoa(binary)}`
    })().catch((error) => {
      embeddedFonts.delete(filename)
      throw error
    })
    embeddedFonts.set(filename, pending)
  }
  return pending
}
