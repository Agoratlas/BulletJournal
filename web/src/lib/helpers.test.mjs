import assert from 'node:assert/strict'
import { test } from 'node:test'

import { artifactFor, artifactsForRenamedNodes, dependencyNodeIds, inputSourcePath, inputsForNode, outputsForNode } from './helpers.ts'

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

test('input source navigation follows the matching lanes through multiple organizers', () => {
  const snapshot = { graph: {
    nodes: [{ id: 'source', kind: 'constant' }, { id: 'other', kind: 'notebook' }, { id: 'one', kind: 'organizer' }, { id: 'two', kind: 'organizer' }, { id: 'target', kind: 'notebook' }],
    edges: [
      { id: 'first', source_node: 'source', source_port: 'value', target_node: 'one', target_port: 'lane_a' },
      { id: 'unrelated', source_node: 'other', source_port: 'frame', target_node: 'one', target_port: 'lane_b' },
      { id: 'middle', source_node: 'one', source_port: 'lane_a', target_node: 'two', target_port: 'renamed_lane' },
      { id: 'last', source_node: 'two', source_port: 'renamed_lane', target_node: 'target', target_port: 'input' },
    ],
  } }
  assert.deepEqual(inputSourcePath(snapshot, 'target', 'input'), { nodeId: 'source', portName: 'value', edgeIds: ['first', 'middle', 'last'] })
  assert.deepEqual(inputSourcePath(snapshot, 'one', 'lane_b'), { nodeId: 'other', portName: 'frame', edgeIds: ['unrelated'] })
  assert.equal(inputSourcePath(snapshot, 'target', 'missing'), null)
  snapshot.graph.edges.shift()
  assert.equal(inputSourcePath(snapshot, 'target', 'input'), null)
})

test('input source navigation rejects organizer cycles', () => {
  const snapshot = { graph: {
    nodes: [{ id: 'organizer', kind: 'organizer' }, { id: 'target', kind: 'notebook' }],
    edges: [
      { id: 'loop', source_node: 'organizer', source_port: 'lane', target_node: 'organizer', target_port: 'lane' },
      { id: 'last', source_node: 'organizer', source_port: 'lane', target_node: 'target', target_port: 'input' },
    ],
  } }
  assert.equal(inputSourcePath(snapshot, 'target', 'input'), null)
})

test('optimistically created constants expose their output handle before the server responds', () => {
  const outputs = outputsForNode({ id: 'constant', kind: 'constant', interface: null, ui: { artifact_name: 'value', data_type: 'int' } })
  assert.equal(outputs[0].name, 'value')
  assert.equal(outputs[0].direction, 'output')
  assert.equal(outputs[0].data_type, 'int')
})

test('persisted source-label metadata cannot override notebook input IDs after reload or copy', () => {
  const inputs = [{ name: 'ratings_ur', label: null, data_type: 'str' }, { name: 'intconst', label: null, data_type: 'int' }]
  for (const id of ['custom_notebook', 'custom_notebook_copy']) {
    const node = { id, kind: 'notebook', ui: { input_labels: { ratings_ur: 'ratings_url', intconst: 'value' } }, interface: { inputs } }
    assert.deepEqual(inputsForNode(node), inputs)
  }
})
