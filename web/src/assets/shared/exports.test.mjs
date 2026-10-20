import assert from 'node:assert/strict'
import { test } from 'node:test'

import { chartBackgroundColor, copyPng, downloadPng, downloadSvg } from './exports.ts'

test('export backgrounds are pure opaque black or white for the active theme', (t) => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'document')
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { documentElement: { dataset: {} } } })
  t.after(() => {
    if (original) Object.defineProperty(globalThis, 'document', original)
    else delete globalThis.document
  })
  assert.equal(chartBackgroundColor(), '#ffffff')
  document.documentElement.dataset.theme = 'dark'
  assert.equal(chartBackgroundColor(), '#000000')
  document.documentElement.dataset.theme = 'light'
  assert.equal(chartBackgroundColor(), '#ffffff')
})

test('SVG, PNG downloads and clipboard exports paint a full opaque background without modifying the chart', async (t) => {
  function mockGlobal(name, value) {
    const original = Object.getOwnPropertyDescriptor(globalThis, name)
    Object.defineProperty(globalThis, name, { configurable: true, value })
    t.after(() => {
      if (original) Object.defineProperty(globalThis, name, original)
      else delete globalThis[name]
    })
  }
  const backgrounds = []
  const fills = []
  const clones = []
  const context = {
    fillRect(...bounds) { fills.push({ color: this.fillStyle, bounds }) },
    drawImage() { assert.equal(fills.length, clones.length - 1) },
  }
  const svg = {
    viewBox: { baseVal: { x: 0, y: 0, width: 200, height: 100 } },
    getBoundingClientRect: () => ({ width: 200, height: 100 }),
    getAttribute: () => null,
    querySelectorAll: () => [],
    cloneNode() {
      const clone = {
        firstChild: null,
        hasAttribute: () => true,
        querySelectorAll: () => [],
        insertBefore(rect) { backgrounds.push(rect) },
      }
      clones.push(clone)
      return clone
    },
  }
  mockGlobal('document', {
    documentElement: { dataset: { theme: 'dark' } },
    fonts: { load: async () => [{}] },
    createElementNS: () => ({ attributes: {}, setAttribute(key, value) { this.attributes[key] = value } }),
    createElement: (tag) => tag === 'canvas'
      ? { getContext: () => context, toBlob: (callback) => callback(new Blob(['png'], { type: 'image/png' })) }
      : { click() {} },
  })
  mockGlobal('XMLSerializer', class { serializeToString() { return '<svg />' } })
  mockGlobal('Image', class { set src(value) { this.onload() } })
  mockGlobal('ClipboardItem', class { constructor(items) { this.items = items } })
  mockGlobal('navigator', { clipboard: { async write(items) { assert.equal((await items[0].items['image/png']).type, 'image/png') } } })

  await downloadSvg(svg, 'chart.svg')
  await downloadPng(svg, 'chart.png')
  document.documentElement.dataset.theme = 'light'
  await copyPng(svg)
  assert.deepEqual(backgrounds.map((rect) => rect.attributes), [
    { x: '0', y: '0', width: '200', height: '100', fill: '#000000', 'pointer-events': 'none' },
    { x: '0', y: '0', width: '200', height: '100', fill: '#000000', 'pointer-events': 'none' },
    { x: '0', y: '0', width: '200', height: '100', fill: '#ffffff', 'pointer-events': 'none' },
  ])
  assert.deepEqual(fills, [
    { color: '#000000', bounds: [0, 0, 4000, 2000] },
    { color: '#ffffff', bounds: [0, 0, 4000, 2000] },
  ])
  assert.equal(svg.firstChild, undefined)
})
