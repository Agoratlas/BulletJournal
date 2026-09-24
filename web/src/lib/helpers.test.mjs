import assert from 'node:assert/strict'
import { test } from 'node:test'

import { artifactFor, artifactsForRenamedNodes } from './helpers.ts'

test('a committed node rename keeps its constant artifact and preview under the new ID', () => {
  const original = {
    node_id: 'old_constant',
    artifact_name: 'value',
    state: 'ready',
    preview: { kind: 'simple', repr: '"hello"' },
  }
  const unrelated = { node_id: 'other', artifact_name: 'value', preview: { kind: 'empty' } }
  const artifacts = artifactsForRenamedNodes(
    [original, unrelated],
    [{ type: 'rename_node', node_id: 'old_constant', new_node_id: 'new_constant', title: 'New constant' }],
  )

  assert.equal(artifactFor({ artifacts }, 'new_constant', 'value')?.preview?.repr, '"hello"')
  assert.equal(artifactFor({ artifacts }, 'old_constant', 'value'), undefined)
  assert.equal(artifacts[1], unrelated)
  assert.equal(original.node_id, 'old_constant')
})
