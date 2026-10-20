import assert from 'node:assert/strict'
import { test } from 'node:test'
import { draftInputFromConnection, notebookDraftOperations, snapshotWithNotebookDrafts } from './notebookDrafts.ts'

const port = { name: 'value', label: 'Original artifact', data_type: 'int' }
const snapshot = { graph: { nodes: [{ id: 'source', kind: 'constant', interface: { outputs: [port] } }], edges: [], layout: [] } }
const emptyDraft = { id: 'notebook-draft:one', nodeId: 'custom', title: 'Custom', x: 20, y: 40, inputs: [] }
const connection = { source: 'source', sourceHandle: 'out:value', target: emptyDraft.id, targetHandle: 'ghost-in:0' }

test('dragging an output prepares a local input with its ID, type, and unchecked default', () => {
  const pending = draftInputFromConnection(snapshot, [emptyDraft], connection)
  assert.equal(pending.insertIndex, 0)
  assert.equal(pending.input.name, 'value')
  assert.equal(pending.input.dataType, 'int')
  assert.equal(pending.input.defaultToNone, false)
  assert.equal(pending.input.description, '')
  assert.equal(snapshot.graph.nodes.length, 1)
  assert.equal(emptyDraft.inputs.length, 0)
})

test('only actual outputs and valid draft insertion positions can add inputs', () => {
  for (const patch of [{ sourceHandle: 'ghost-out:0' }, { sourceHandle: 'out:missing' }, { targetHandle: 'ghost-in:1' }, { targetHandle: 'ghost-in:NaN' }, { source: 'missing' }, { target: 'source' }]) {
    assert.equal(draftInputFromConnection(snapshot, [emptyDraft], { ...connection, ...patch }), null)
  }
  assert.equal(draftInputFromConnection(snapshot, [{ ...emptyDraft, busy: true }], connection), null)
})

test('canvas overlay is immutable and preserves local inputs when their source disappears', () => {
  const input = draftInputFromConnection(snapshot, [emptyDraft], connection).input
  const draft = { ...emptyDraft, inputs: [{ ...input, name: 'custom_input' }] }
  const canvas = snapshotWithNotebookDrafts(snapshot, [draft])
  assert.equal(canvas.graph.nodes.length, 2)
  assert.equal(canvas.graph.edges.length, 1)
  assert.equal(canvas.graph.nodes[1].interface.inputs[0].label, 'custom_input')
  assert.equal(snapshot.graph.nodes.length, 1)
  assert.equal(snapshot.graph.edges.length, 0)
  const gone = { ...snapshot, graph: { ...snapshot.graph, nodes: [] } }
  assert.equal(snapshotWithNotebookDrafts(gone, [draft]).graph.edges.length, 0)
  assert.equal(draft.inputs.length, 1)
  assert.throws(() => notebookDraftOperations(draft, gone), /no longer available/)
})

test('creation keeps UI order and renamed IDs while wiring to the original output', () => {
  const input = draftInputFromConnection(snapshot, [emptyDraft], connection).input
  const draft = { ...emptyDraft, inputs: [{ ...input, key: 'b', name: 'second', defaultToNone: true }, { ...input, key: 'a', name: 'first', description: 'Docs' }] }
  const operations = notebookDraftOperations(draft, snapshot)
  assert.deepEqual(operations[0].inputs.map((input) => input.name), ['second', 'first'])
  assert.equal(operations[0].inputs[0].default_to_none, true)
  assert.equal(operations[0].inputs[1].description, 'Docs')
  assert.equal(operations[0].ui?.input_labels, undefined)
  assert.deepEqual(operations.slice(1).map((edge) => [edge.source_port, edge.target_port]), [['value', 'second'], ['value', 'first']])
})

test('empty and disconnected inputs can be created, and duplicate IDs are rejected', () => {
  assert.equal(notebookDraftOperations(emptyDraft, snapshot).length, 1)
  const input = draftInputFromConnection(snapshot, [emptyDraft], connection).input
  assert.equal(notebookDraftOperations({ ...emptyDraft, inputs: [{ ...input, sourceNode: null, sourcePort: null }] }, snapshot).length, 1)
  assert.throws(() => notebookDraftOperations({ ...emptyDraft, inputs: [input, input] }, snapshot), /unique artifact ID/)
  assert.throws(() => notebookDraftOperations({ ...emptyDraft, nodeId: 'source' }, snapshot), /unused node ID/)
})
