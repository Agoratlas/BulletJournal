import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseStrictJson } from './strictJson.ts'

test('duplicate keys are rejected, including nested and escaped keys', () => {
  for (const text of ['{"a":1,"a":2}', '{"nested":{"a":1,"\\u0061":2}}', '[{"a":1,"a":2}]']) {
    assert.throws(() => parseStrictJson(text), /Duplicate JSON object key/)
  }
})

test('keys in distinct objects and punctuation inside strings are allowed', () => {
  assert.deepEqual(parseStrictJson('{"a":{"key":1},"b":{"key":2},"text":"{\\"key\\":3}"}'), {
    a: { key: 1 }, b: { key: 2 }, text: '{"key":3}',
  })
})
