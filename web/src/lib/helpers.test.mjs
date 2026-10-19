import assert from 'node:assert/strict'
import { test } from 'node:test'

import { artifactFor, artifactsForRenamedNodes, dependencyNodeIds, outputsForNode } from './helpers.ts'

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

test('organizer lanes remain separate in dependency traversal', () => {
  const snapshot = { graph: {
    nodes: ['nb1', 'nb2', 'nb3', 'nb4'].map((id) => ({ id, kind: 'notebook' })).concat([{ id: 'organizer', kind: 'organizer' }]),
    edges: [['nb1', 'organizer', 'A'], ['organizer', 'nb2', 'A'], ['nb3', 'organizer', 'B'], ['organizer', 'nb4', 'B']]
      .map(([source_node, target_node, port]) => ({ source_node, target_node, source_port: port, target_port: port })),
  } }
  assert.deepEqual(dependencyNodeIds(snapshot, ['nb4'], 'upstream'), new Set(['nb4', 'organizer', 'nb3']))
  assert.deepEqual(dependencyNodeIds(snapshot, ['nb1'], 'downstream'), new Set(['nb1', 'organizer', 'nb2']))
})

test('optimistically created constants expose their output handle before the server responds', () => {
  const outputs = outputsForNode({ id: 'constant', kind: 'constant', interface: null, ui: { artifact_name: 'value', data_type: 'int' } })
  assert.equal(outputs[0].name, 'value')
  assert.equal(outputs[0].direction, 'output')
  assert.equal(outputs[0].data_type, 'int')
})
