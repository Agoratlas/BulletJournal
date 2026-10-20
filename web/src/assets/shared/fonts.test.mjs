import assert from 'node:assert/strict'
import { test } from 'node:test'

import { embeddedChartFontCss, ensureChartFontsLoaded } from './fonts.ts'

const mediumFont = new Uint8Array([119, 79, 70, 50, 1])
const boldFont = new Uint8Array([119, 79, 70, 50, 2])

function mockGlobal(t, name, value) {
  const original = Object.getOwnPropertyDescriptor(globalThis, name)
  Object.defineProperty(globalThis, name, { configurable: true, value })
  t.after(() => {
    if (original) Object.defineProperty(globalThis, name, original)
    else delete globalThis[name]
  })
}

test('exports embed only used Barlow faces, share fetches, and respect the project base path', async (t) => {
  t.mock.method(globalThis, 'fetch', async (url) => new Response(url.includes('Medium') ? mediumFont : boldFont))
  mockGlobal(t, 'window', { __BULLETJOURNAL_BASE_PATH__: '/p/example/' })

  assert.equal(await embeddedChartFontCss([{ fontFamily: 'sans-serif', fontWeight: '400' }]), '')
  assert.equal(fetch.mock.calls.length, 0)

  const styles = [
    { fontFamily: '"Barlow", sans-serif', fontWeight: '400' },
    { fontFamily: 'Barlow', fontWeight: '500' },
    { fontFamily: "'Barlow'", fontWeight: '600' },
    { fontFamily: 'barlow', fontWeight: 'bold' },
  ]
  const [first, second] = await Promise.all([embeddedChartFontCss(styles), embeddedChartFontCss(styles)])
  assert.equal(first, second)
  assert.equal((first.match(/@font-face/g) ?? []).length, 2)
  assert.match(first, /font-weight: 400 500/)
  assert.match(first, /font-weight: 600 700/)
  assert.ok(first.includes(`data:font/woff2;base64,${Buffer.from(mediumFont).toString('base64')}`))
  assert.ok(first.includes(`data:font/woff2;base64,${Buffer.from(boldFont).toString('base64')}`))
  assert.deepEqual(fetch.mock.calls.map((call) => call.arguments[0]), [
    '/p/example/fonts/Barlow-Medium.woff2',
    '/p/example/fonts/Barlow-Bold.woff2',
  ])

  const mediumOnly = await embeddedChartFontCss(styles.slice(0, 2))
  assert.equal((mediumOnly.match(/@font-face/g) ?? []).length, 1)
  assert.equal(mediumOnly.includes('600 700'), false)
  assert.equal(fetch.mock.calls.length, 2)
})

test('failed font downloads are reported and retried instead of caching a fallback', async (t) => {
  // Use fresh module state so previous successful fetches cannot hide the failure.
  const { embeddedChartFontCss } = await import('./fonts.ts?failure')
  mockGlobal(t, 'window', {})
  const styles = [{ fontFamily: 'Barlow', fontWeight: '400' }]
  t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 404 }))
  await assert.rejects(embeddedChartFontCss(styles), /Could not load the chart font/)

  fetch.mock.mockImplementation(async () => new Response('<html>SPA fallback</html>'))
  await assert.rejects(embeddedChartFontCss(styles), /Could not load the chart font/)

  fetch.mock.mockImplementation(async () => new Response(mediumFont))
  assert.match(await embeddedChartFontCss(styles), /data:font\/woff2;base64,/)
  assert.equal(fetch.mock.calls.length, 3)
})

test('chart rendering waits for all font weights and retries a failed load', async (t) => {
  let finishLoad
  const loading = new Promise((resolve) => { finishLoad = resolve })
  const load = t.mock.fn(() => loading)
  mockGlobal(t, 'document', { fonts: { load } })
  let ready = false
  const pending = ensureChartFontsLoaded().then(() => { ready = true })
  assert.deepEqual(load.mock.calls.map((call) => call.arguments[0]), [
    '400 12px "Barlow"', '500 12px "Barlow"', '600 12px "Barlow"', '700 12px "Barlow"',
  ])
  await Promise.resolve()
  assert.equal(ready, false)
  finishLoad([])
  await assert.rejects(pending, /Could not load the chart font/)

  load.mock.mockImplementation(async () => [{}])
  await ensureChartFontsLoaded()
  assert.equal(load.mock.calls.length, 8)
  await ensureChartFontsLoaded()
  assert.equal(load.mock.calls.length, 8)
})
