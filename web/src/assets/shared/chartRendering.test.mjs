import assert from 'node:assert/strict'
import { before, test } from 'node:test'
import { View, changeset, parse } from 'vega'
import { compile } from 'vega-lite'
import { createServer } from 'vite'

let charts
let defaults
let theme
let buildAxisSpec
let enforceTickLabelBounds
let tickLabelPolygon
let tickPolygonsOverlap

before(async () => {
  const server = await createServer({ configFile: false, server: { middlewareMode: true } })
  try {
    charts = await Promise.all(['BarChart', 'PieChart', 'Histogram', 'ScatterPlot'].map(
      (name) => server.ssrLoadModule(`/src/assets/panels/${name}AssetPanel.tsx`),
    ))
    defaults = await server.ssrLoadModule('/src/assets/shared/modifiers.ts')
    ;({ buildAxisSpec, enforceTickLabelBounds } = await server.ssrLoadModule('/src/assets/shared/chart.ts'))
    ;({ tickLabelPolygon, tickPolygonsOverlap } = await server.ssrLoadModule('/src/assets/shared/tickOverlap.ts'))
    theme = {
      axisDomainColor: '#888', axisLabelColor: '#666', axisTitleColor: '#444',
      gridColor: '#ddd', legendLabelColor: '#666', legendTitleColor: '#444',
      selectionColor: '#2563eb', fallbackPointColor: '#888',
    }
  } finally {
    await server.close()
  }
})

async function render(spec) {
  const view = new View(parse(enforceTickLabelBounds(compile(spec).spec)), { renderer: 'none' })
  await view.runAsync()
  return view
}

test('chart geometry stays fixed across tooltip dataflow runs, including overflowing titles', async () => {
  const barDefaults = defaults.defaultBarChartChartOverrides({}, [])
  const pieDefaults = defaults.defaultPieChartOverrides({}, [])
  const histogramDefaults = defaults.defaultHistogramChartOverrides({}, [])
  const scatterDefaults = defaults.defaultScatterPlotChartOverrides({}, [])
  for (const title of ['Short title', 'Very long title '.repeat(40)]) {
    for (const overrides of [barDefaults, pieDefaults, histogramDefaults, scatterDefaults]) {
      overrides.title = { ...overrides.title, hideTitle: false, text: title }
    }
    const specs = [
      charts[0].buildBarChartVegaLiteSpec({
        category_column: 'category', value_column: 'value', aggregation: 'sum',
        bars: [{ value: 'a', label: 'a', aggregate_value: 2, color: '#369' }],
      }, [], theme, 320, barDefaults, barDefaults, 1),
      charts[1].buildPieChartVegaLiteSpec([
        { label: 'a', count: 2, share: 0.5, color: '#369', rawValues: ['a'] },
        { label: 'b', count: 2, share: 0.5, color: '#963', rawValues: ['b'] },
      ], [], theme, 320, 480, pieDefaults, pieDefaults, 1),
      charts[2].buildHistogramVegaLiteSpec({
        column: 'value', bins: [{ index: 0, start: 0, end: 1, count: 2 }],
      }, theme, 320, histogramDefaults, histogramDefaults, 1, [], []),
      charts[3].buildScatterPlotVegaLiteSpec({
        x_column: 'x', y_column: 'y', points: [{ x: 1, y: 2, row_index: 0 }],
      }, null, null, null, theme, 320, scatterDefaults, scatterDefaults, 1),
    ]
    for (const spec of specs) {
      const view = await render({ ...spec, width: 480 })
      try {
        assert.ok(view.width() > 100)
        assert.ok(view.height() > 100)
        const geometry = await view.toSVG()
        const mark = view.scenegraph().root.items[0].items.find((mark) => ['arc', 'rect', 'symbol'].includes(mark.marktype))
        assert.ok(mark?.items.length)
        for (let hover = 0; hover < 3; hover += 1) {
          view.pulse(mark.source, changeset().encode(mark.items[0], 'hover'))
          await view.runAsync()
          assert.equal(await view.toSVG(), geometry)
        }
        view.width(360).height(260).resize()
        await view.runAsync()
        assert.ok(view.width() > 100)
        assert.ok(view.height() > 100)
        assert.notEqual(await view.toSVG(), geometry)
      } finally {
        view.finalize()
      }
    }
  }
})

test('pie radius scales to the space remaining after top or bottom titles', async () => {
  const overrides = defaults.defaultPieChartOverrides({}, [])
  const slices = [
    { label: 'Top', count: 1, share: 1, color: '#369', rawValues: ['Top'] },
  ]
  const radii = []
  for (const title of [
    { ...overrides.title, hideTitle: true },
    { ...overrides.title, hideTitle: false, text: 'Title', size: '60', position: 'top' },
    { ...overrides.title, hideTitle: false, text: 'Title', size: '60', position: 'bottom' },
  ]) {
    const spec = charts[1].buildPieChartVegaLiteSpec(slices, [], theme, 320, 480,
      { ...overrides, title }, overrides, 1)
    const view = await render(spec)
    try {
      const arc = view.scenegraph().root.items[0].items.find((mark) => mark.marktype === 'arc').items[0]
      radii.push(arc.outerRadius)
      const svg = await view.toSVG()
      assert.match(svg, /height="320"/)
    } finally {
      view.finalize()
    }
  }
  assert.ok(radii[1] < radii[0])
  assert.ok(radii[2] < radii[0])
  assert.equal(radii[1], radii[2])
})

test('outside pie labels stay inside the fitted plot on narrow panels with large titles', async () => {
  const defaultsValue = defaults.defaultPieChartOverrides({}, [])
  for (const position of ['top', 'bottom']) {
    const overrides = {
      ...defaultsValue,
      labelPosition: 200,
      title: { ...defaultsValue.title, hideTitle: false, text: 'Large title', size: '48', position },
    }
    const slices = Array.from({ length: 4 }, (_, index) => ({
      label: `Category ${index}`, count: 1, share: 0.25, color: '#369', rawValues: [index],
    }))
    const view = await render(charts[1].buildPieChartVegaLiteSpec(slices, [], theme, 260, 280,
      overrides, defaultsValue, 1))
    try {
      const labels = view.scenegraph().root.items[0].items.find((mark) => mark.marktype === 'text').items
      assert.equal(labels.length, 4)
      for (const label of labels) {
        assert.ok(label.bounds.x1 >= 0)
        assert.ok(label.bounds.y1 >= 0)
        assert.ok(label.bounds.x2 <= view.width())
        assert.ok(label.bounds.y2 <= view.height())
      }
    } finally {
      view.finalize()
    }
  }
})

test('horizontal X labels are centered below their ticks with or without category grid lines', async () => {
  const published = defaults.defaultBarChartChartOverrides({}, [])
  for (const showGridLines of [false, true]) {
    const overrides = { ...published, xAxis: { ...published.xAxis, tickAngle: 0, showGridLines } }
    const spec = charts[0].buildBarChartVegaLiteSpec({
      category_column: 'category', value_column: 'value', aggregation: 'sum',
      bars: [{ value: 'Category', label: 'Category', aggregate_value: 1, color: '#369' }],
    }, [], theme, 320, overrides, published, 1)
    const view = await render({ ...spec, width: 480 })
    try {
      const labels = []
      function visit(item) {
        if (item.mark?.role === 'axis-label' && item.text?.[0] === 'Category') labels.push(item)
        for (const child of item.items ?? []) visit(child)
      }
      visit(view.scenegraph().root)
      assert.equal(labels.length, 1)
      assert.equal(labels[0].align, 'center')
      assert.equal(labels[0].baseline, 'top')
    } finally {
      view.finalize()
    }
  }
})

test('tick labels wrap long categories and preserve line breaks, with configurable angles and font sizes', async () => {
  const published = defaults.defaultBarChartChartOverrides({}, [])
  assert.equal(published.xAxis.tickAngle, 45)
  assert.equal(published.yAxis.tickAngle, 0)
  const labels = ['A long category name that needs multiple lines', 'First\nSecond', 'x'.repeat(50), '東京']
  for (const angle of [0, 45, 90]) {
    const overrides = defaults.barChartChartOverridesFromModifiers({}, {
      x_axis: { tick_angle: angle, tick_size: 16 },
      y_axis: { tick_angle: angle },
    }, [])
    const serialized = defaults.serializeBarChartModifierValues(overrides)
    assert.equal(serialized.x_axis.tick_angle, angle)
    assert.equal(serialized.x_axis.tick_size, 16)
    const spec = charts[0].buildBarChartVegaLiteSpec({
      category_column: 'category', value_column: 'value', aggregation: 'sum',
      bars: labels.map((label, index) => ({ value: label, label, aggregate_value: index + 1, color: '#369' })),
    }, [], theme, 480, overrides, published, 1)
    const view = await render({ ...spec, width: 640 })
    try {
      const svg = await view.toSVG()
      assert.match(svg, /fill="#ffffff"/)
      assert.match(svg, /font-size="16px"/)
      const axisLabels = []
      function visit(item) {
        if (item.mark?.role === 'axis-label') axisLabels.push(item)
        for (const child of item.items ?? []) visit(child)
      }
      visit(view.scenegraph().root)
      const xLabels = axisLabels.filter((item) => item.fontSize === 16)
      assert.equal(xLabels.length, labels.length)
      assert.ok(xLabels.every((item) => item.angle === (360 - angle) % 360 && item.fill === '#ffffff'))
      assert.ok(xLabels.some((item) => Array.isArray(item.text) && item.text.length > 1))
      assert.deepEqual(xLabels.find((item) => item.text[0] === 'First').text, ['First', 'Second'])
      assert.equal(xLabels.find((item) => item.text[0].startsWith('x')).text.join(''), 'x'.repeat(50))
    } finally {
      view.finalize()
    }
  }
})

test('visible tick-label bounds never overlap across angles, multiline text, and resized panels', async () => {
  const labels = Array.from({ length: 20 }, (_, index) => `${index} Long category name\nwith several lines\nof overflowing text`)
  for (const axis of ['x', 'y']) {
    for (const angle of [0, 45, 90]) {
      for (const showGridLines of [false, true]) {
        const overrides = { ...defaults.emptyChartAxisOverrides(), tickAngle: angle, tickSize: '18', showGridLines }
        const spec = {
          data: { values: labels.map((label, index) => ({ label, value: index })) },
          mark: 'point', width: 420, height: 320,
          encoding: {
            [axis]: { field: 'label', type: 'nominal', axis: buildAxisSpec(overrides, '', 1, axis) },
            [axis === 'x' ? 'y' : 'x']: { field: 'value', type: 'quantitative', axis: null },
          },
        }
        const view = await render(spec)
        try {
          for (const [width, height] of [[420, 320], [240, 180], [640, 480]]) {
            view.width(width).height(height).resize()
            await view.runAsync()
            const visible = []
            function visit(item) {
              if (item.mark?.role === 'axis-label' && item.opacity !== 0) visible.push(item)
              for (const child of item.items ?? []) visit(child)
            }
            visit(view.scenegraph().root)
            assert.ok(visible.length > 0)
            for (let i = 0; i < visible.length; i += 1) {
              for (let j = i + 1; j < visible.length; j += 1) {
                assert.ok(!tickPolygonsOverlap(tickLabelPolygon(visible[i]), tickLabelPolygon(visible[j])),
                  `${axis} axis, ${angle}°, grid=${showGridLines}, ${width}×${height}: labels ${i} and ${j} overlap`)
              }
            }
          }
        } finally {
          view.finalize()
        }
      }
    }
  }
})

test('every bar category remains visible within its slot, including dense years at 45 degrees', async () => {
  const published = defaults.defaultBarChartChartOverrides({}, [])
  for (const [labels, fontSize] of [
    [Array.from({ length: 20 }, (_, index) => `Genre ${index} with a long category description`), 10],
    [Array.from({ length: 41 }, (_, index) => String(1980 + index)), 12],
  ]) {
    for (const angle of [0, 45, 90]) {
      for (const showGridLines of [false, true]) {
        const overrides = { ...published, xAxis: { ...published.xAxis, tickAngle: angle, tickSize: String(fontSize), showGridLines } }
        const spec = charts[0].buildBarChartVegaLiteSpec({
          category_column: 'category', value_column: 'value', aggregation: 'sum',
          bars: labels.map((label, index) => ({ value: label, label, category_index: index, aggregate_value: index + 1, color: '#369' })),
        }, [], theme, 480, overrides, published, 1)
        const view = await render({ ...spec, width: 1000 })
        try {
          for (const width of [1000, 700, 1200]) {
            view.width(width).resize()
            await view.runAsync()
            const categoryLabels = []
            function visit(item) {
              if (item.mark?.role === 'axis-label' && labels.includes(item.datum.label)) categoryLabels.push(item)
              for (const child of item.items ?? []) visit(child)
            }
            visit(view.scenegraph().root)
            assert.equal(categoryLabels.length, labels.length)
            if (labels.length === 20 || width >= 1000 || angle !== 45) {
              assert.ok(categoryLabels.every((item) => item.opacity !== 0), `${labels.length} categories, ${angle}°, width ${width}`)
            }
            for (let i = 0; i < categoryLabels.length; i += 1) {
              const item = categoryLabels[i]
              const rendered = item.text.join('').replace(/\s/g, '')
              assert.ok(rendered.endsWith('...') || rendered === labels[i].replace(/\s/g, ''))
              assert.equal(item.fontSize, fontSize)
              if (angle === 0 && categoryLabels.length > 1) {
                const slot = Math.abs(categoryLabels[1].x - categoryLabels[0].x)
                assert.ok(item.bounds.width() <= slot)
              }
              for (let j = i + 1; j < categoryLabels.length; j += 1) {
                if (item.opacity !== 0 && categoryLabels[j].opacity !== 0) {
                  assert.ok(!tickPolygonsOverlap(tickLabelPolygon(item), tickLabelPolygon(categoryLabels[j])))
                }
              }
            }
          }
        } finally {
          view.finalize()
        }
      }
    }
  }
})

test('vertical multiline tick labels anchor at the center of the whole block', async () => {
  const published = defaults.defaultBarChartChartOverrides({}, [])
  const overrides = { ...published, xAxis: { ...published.xAxis, tickAngle: 90, tickSize: '12' } }
  const spec = charts[0].buildBarChartVegaLiteSpec({
    category_column: 'category', value_column: 'value', aggregation: 'sum',
    bars: [{ value: 'a', label: 'First\nSecond\nThird\nFourth\nFifth', aggregate_value: 1, color: '#369' },
      { value: 'b', label: 'Other', aggregate_value: 2, color: '#369' }],
  }, [], theme, 480, overrides, published, 1)
  const view = await render({ ...spec, width: 1000 })
  try {
    const labels = []
    function visit(item) {
      if (item.mark?.role === 'axis-label' && item.datum.label.startsWith('First')) labels.push(item)
      for (const child of item.items ?? []) visit(child)
    }
    visit(view.scenegraph().root)
    assert.equal(labels.length, 1)
    const label = labels[0]
    assert.equal(label.text.length, 5)
    assert.ok(Math.abs((label.bounds.x1 + label.bounds.x2) / 2 - label.x) < 0.01)
    const firstOffset = label.dy
    view.width(900).resize()
    await view.runAsync()
    assert.equal(label.dy, firstOffset)
  } finally {
    view.finalize()
  }
})

test('large rotated tick sizes remain uniform and overflow becomes ellipsis', async () => {
  const published = defaults.defaultBarChartChartOverrides({}, [])
  for (const angle of [45, 90]) {
    const overrides = { ...published, xAxis: { ...published.xAxis, tickAngle: angle, tickSize: '30' } }
    const spec = charts[0].buildBarChartVegaLiteSpec({
      category_column: 'category', value_column: 'value', aggregation: 'sum',
      bars: Array.from({ length: 8 }, (_, index) => ({ value: index, label: `Year ${index} with a very long description repeated repeatedly`, aggregate_value: index + 1, color: '#369' })),
    }, [], theme, 480, overrides, published, 1)
    const view = await render({ ...spec, width: 1000 })
    try {
      for (const width of [1000, 700, 1200]) {
        view.width(width).resize()
        await view.runAsync()
        const labels = []
        function visit(item) {
          if (item.mark?.role === 'axis-label' && item.datum.label.startsWith('Year')) labels.push(item)
          for (const child of item.items ?? []) visit(child)
        }
        visit(view.scenegraph().root)
        assert.equal(labels.length, 8)
        assert.ok(labels.every((item) => item.fontSize === 30 && item.opacity !== 0))
        assert.ok(labels.some((item) => item.text.join('').endsWith('...')))
      }
    } finally {
      view.finalize()
    }
  }
})
