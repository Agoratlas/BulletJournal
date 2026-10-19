import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ts from 'typescript'

const source = readFileSync(new URL('../components/SimpleMarkdown.tsx', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
})
const exports = {}
runInNewContext(outputText, { exports, require: createRequire(import.meta.url) })
const render = (text) => renderToStaticMarkup(React.createElement(exports.SimpleMarkdown, { text }))

test('documentation soft wraps render as spaces, while explicit breaks remain', () => {
  assert.equal(render('    This notebook\n    is used for processing data'), '<div class="simple-markdown"><p>This notebook is used for processing data</p></div>')
  assert.match(render('First line  \nSecond line'), /First line<br\/>Second line/)
  assert.match(render('First line\\\nSecond line'), /First line<br\/>Second line/)
})

test('fenced code preserves indentation, blank lines, and literal markup', () => {
  const html = render('    ```python\n    for item in items:\n        print("<tag>")\n\n    **literal**\n    ```\n\n    After')
  assert.match(html, /<pre><code>for item in items:\n    print\(&quot;&lt;tag&gt;&quot;\)\n\n\*\*literal\*\*<\/code><\/pre>/)
  assert.match(html, /<p>After<\/p>/)
  assert.doesNotMatch(html, /<strong>|<tag>/)
})

test('wrapped list items stay in their list and nested items remain nested', () => {
  const html = render('- First part\n  continuation\n  - Nested\n- Second')
  assert.match(html, /<li>First part continuation<ul><li>Nested<\/li><\/ul><\/li><li>Second<\/li>/)
})

test('an unclosed fence renders the remaining lines as code', () => {
  assert.match(render('```\n# literal heading\nlast line'), /<pre><code># literal heading\nlast line<\/code><\/pre>/)
})
