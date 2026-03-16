import type { ChangeEvent, ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'

import { Info, Plus, X } from './Icons'

type ModalProps = {
  title: string
  onClose: () => void
  children: ReactNode
  contentClassName?: string
}

export function Modal({ title, onClose, children, contentClassName }: ModalProps) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal-card${contentClassName ? ` ${contentClassName}` : ''}`} onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="ghost-button" onClick={onClose}>Close</button>
        </div>
        {children}
      </div>
    </div>
  )
}

type CreateNotebookDialogProps = {
  blockLabel: string
  suggestedTitle: string
  existingIds: string[]
  submitLabel?: string
  onClose: () => void
  onCreate: (payload: { nodeId: string; title: string }) => Promise<void>
}

export function CreateNotebookDialog({ blockLabel, suggestedTitle, existingIds, submitLabel = 'Create block', onClose, onCreate }: CreateNotebookDialogProps) {
  const [title, setTitle] = useState(suggestedTitle)
  const [nodeId, setNodeId] = useState(normalizeNodeId(suggestedTitle))
  const [nodeIdTouched, setNodeIdTouched] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setTitle(suggestedTitle)
    setNodeId(normalizeNodeId(suggestedTitle))
    setNodeIdTouched(false)
    setBusy(false)
  }, [suggestedTitle])

  const resolvedId = useMemo(() => normalizeNodeId(nodeId), [nodeId])
  const duplicateId = existingIds.includes(resolvedId)
  const invalidId = !resolvedId
  const invalidTitle = !title.trim()

  async function submit() {
    const resolvedTitle = title.trim()
    if (!resolvedTitle || !resolvedId || duplicateId) {
      return
    }
    setBusy(true)
    try {
      await onCreate({ nodeId: resolvedId, title: resolvedTitle })
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={`Create ${blockLabel}`} onClose={onClose}>
      <div className="form-grid">
        <label>
          <span>Name</span>
          <input
            value={title}
            onChange={(event) => {
              const nextTitle = event.target.value
              setTitle(nextTitle)
              if (!nodeIdTouched) {
                setNodeId(normalizeNodeId(nextTitle))
              }
            }}
            placeholder="Parse user CSV"
          />
          {invalidTitle ? <span className="field-note error">Name is required.</span> : <span className="field-note">Shown on the node card in the editor.</span>}
        </label>
        <label>
          <span>Node ID</span>
          <input
            className={duplicateId || invalidId ? 'invalid' : ''}
            value={nodeId}
            onChange={(event) => {
              setNodeIdTouched(true)
              setNodeId(normalizeNodeId(event.target.value))
            }}
            placeholder="parse_user_csv"
            spellCheck={false}
          />
          {duplicateId ? (
            <span className="field-note error">This ID is already used by another node.</span>
          ) : invalidId ? (
            <span className="field-note error">Node ID is required.</span>
          ) : (
            <span className="field-note">Node IDs are stored as snake_case and used in graph references.</span>
          )}
        </label>
        <div className="dialog-actions">
          <button className="secondary" onClick={onClose}>Cancel</button>
          <button onClick={submit} disabled={busy || invalidTitle || invalidId || duplicateId}>{busy ? 'Creating...' : submitLabel}</button>
        </div>
      </div>
    </Modal>
  )
}

type ConstantValueType = 'int' | 'float' | 'bool' | 'str' | 'list' | 'dict' | 'object'

type ConstantValueOutput = {
  key: string
  name: string
  dataType: ConstantValueType
  value: string
}

type CreateConstantValueDialogProps = {
  suggestedTitle: string
  existingIds: string[]
  onClose: () => void
  onCreate: (payload: { nodeId: string; title: string; outputs: Array<{ name: string; dataType: ConstantValueType; value: string }> }) => Promise<void>
}

export function CreateConstantValueDialog({ suggestedTitle, existingIds, onClose, onCreate }: CreateConstantValueDialogProps) {
  const [title, setTitle] = useState(suggestedTitle)
  const [nodeId, setNodeId] = useState(normalizeNodeId(suggestedTitle))
  const [nodeIdTouched, setNodeIdTouched] = useState(false)
  const [outputs, setOutputs] = useState<ConstantValueOutput[]>([makeDefaultOutput(0)])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setTitle(suggestedTitle)
    setNodeId(normalizeNodeId(suggestedTitle))
    setNodeIdTouched(false)
    setOutputs([makeDefaultOutput(0)])
    setBusy(false)
  }, [suggestedTitle])

  const resolvedId = useMemo(() => normalizeNodeId(nodeId), [nodeId])
  const duplicateId = existingIds.includes(resolvedId)
  const invalidId = !resolvedId
  const invalidTitle = !title.trim()
  const hasInvalidOutput = outputs.some((output) => !normalizeNodeId(output.name))
  const duplicateOutputNames = duplicateNames(outputs.map((output) => normalizeNodeId(output.name)))

  async function submit() {
    const resolvedTitle = title.trim()
    if (!resolvedTitle || !resolvedId || duplicateId || hasInvalidOutput) {
      return
    }
    setBusy(true)
    try {
      await onCreate({
        nodeId: resolvedId,
        title: resolvedTitle,
        outputs: outputs.map((output) => ({
          name: normalizeNodeId(output.name),
          dataType: output.dataType,
          value: output.value,
        })),
      })
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Create Constant Value" onClose={onClose}>
      <div className="form-grid">
        <label>
          <span>Name</span>
          <input
            value={title}
            onChange={(event) => {
              const nextTitle = event.target.value
              setTitle(nextTitle)
              if (!nodeIdTouched) {
                setNodeId(normalizeNodeId(nextTitle))
              }
            }}
            placeholder="User constants"
          />
        </label>
        <label>
          <span>Node ID</span>
          <input
            className={duplicateId || invalidId ? 'invalid' : ''}
            value={nodeId}
            onChange={(event) => {
              setNodeIdTouched(true)
              setNodeId(normalizeNodeId(event.target.value))
            }}
            placeholder="user_constants"
            spellCheck={false}
          />
          {duplicateId ? <span className="field-note error">This ID is already used by another node.</span> : null}
        </label>
        <div className="constant-output-list inline-output-list">
          <div className="panel-header-row compact-row">
            <h4>Outputs</h4>
          </div>
          {outputs.map((output, index) => {
            const normalizedName = normalizeNodeId(output.name)
            const isOther = output.dataType === 'object'
            const duplicateOutput = normalizedName && duplicateOutputNames.has(normalizedName)
            return (
              <div key={output.key} className="constant-output-card output-line-card">
                <div className="output-line-index">
                  <strong>{index + 1}</strong>
                </div>
                <label>
                  <span>Output name</span>
                  <input
                    className={!normalizedName || duplicateOutput ? 'invalid' : ''}
                    value={output.name}
                    onChange={(event) => updateOutput(setOutputs, output.key, { name: normalizeFreeformSnakeCase(event.target.value) })}
                    placeholder="value"
                    spellCheck={false}
                  />
                  {duplicateOutput ? <span className="field-note error">Output names must be unique.</span> : null}
                </label>
                <label>
                  <span>Type</span>
                  <select
                    value={output.dataType}
                    onChange={(event) => handleOutputTypeChange(output.key, event, setOutputs)}
                  >
                    <option value="int">int</option>
                    <option value="float">float</option>
                    <option value="bool">bool</option>
                    <option value="str">str</option>
                    <option value="list">list</option>
                    <option value="dict">dict</option>
                    <option value="object">other</option>
                  </select>
                </label>
                <label className="value-field-cell">
                  <span>Value</span>
                  <div className="value-input-row">
                    <input
                      value={output.value}
                      disabled={isOther}
                      onChange={(event) => updateOutput(setOutputs, output.key, { value: event.target.value })}
                      placeholder={placeholderForType(output.dataType)}
                      spellCheck={false}
                    />
                    {isOther ? <span className="field-icon-note" title="Edit the generated notebook to set a non-builtin value."><Info width={16} height={16} /></span> : null}
                  </div>
                  {isOther ? <span className="field-note">Edit the notebook after creation.</span> : null}
                </label>
                <div className="output-line-actions">
                  {outputs.length > 1 ? (
                    <button className="danger icon-pill small-icon-pill" onClick={() => setOutputs((current) => current.filter((item) => item.key !== output.key))} aria-label={`Delete output ${index + 1}`}>
                      <X width={16} height={16} />
                    </button>
                  ) : null}
                </div>
              </div>
            )
          })}
          <button className="secondary add-output-button" onClick={() => setOutputs((current) => [...current, makeDefaultOutput(current.length)])}>
            <Plus width={16} height={16} />
            Add output
          </button>
        </div>
        <div className="dialog-actions">
          <button className="secondary" onClick={onClose}>Cancel</button>
          <button onClick={submit} disabled={busy || invalidTitle || invalidId || duplicateId || hasInvalidOutput || duplicateOutputNames.size > 0}>{busy ? 'Creating...' : 'Create block'}</button>
        </div>
      </div>
    </Modal>
  )
}

type CreateFileDialogProps = {
  suggestedTitle: string
  existingIds: string[]
  onClose: () => void
  onCreate: (payload: { nodeId: string; title: string; file: File | null; artifactName: string }) => Promise<void>
}

export function CreateFileDialog({ suggestedTitle, existingIds, onClose, onCreate }: CreateFileDialogProps) {
  const [title, setTitle] = useState(suggestedTitle)
  const [nodeId, setNodeId] = useState(normalizeNodeId(suggestedTitle))
  const [nodeIdTouched, setNodeIdTouched] = useState(false)
  const [artifactName, setArtifactName] = useState('file')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setTitle(suggestedTitle)
    setNodeId(normalizeNodeId(suggestedTitle))
    setNodeIdTouched(false)
    setArtifactName('file')
    setFile(null)
    setBusy(false)
  }, [suggestedTitle])

  const resolvedId = useMemo(() => normalizeNodeId(nodeId), [nodeId])
  const duplicateId = existingIds.includes(resolvedId)
  const invalidId = !resolvedId
  const invalidTitle = !title.trim()
  const invalidArtifactName = !normalizeFreeformSnakeCase(artifactName)

  async function submit() {
    const resolvedTitle = title.trim()
    const resolvedArtifactName = normalizeFreeformSnakeCase(artifactName)
    if (!resolvedTitle || !resolvedId || duplicateId || !resolvedArtifactName) {
      return
    }
    setBusy(true)
    try {
      await onCreate({ nodeId: resolvedId, title: resolvedTitle, file, artifactName: resolvedArtifactName })
      onClose()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Create File" onClose={onClose}>
      <div className="form-grid">
        <label>
          <span>Name</span>
          <input
            value={title}
            onChange={(event) => {
              const nextTitle = event.target.value
              setTitle(nextTitle)
              if (!nodeIdTouched) {
                setNodeId(normalizeNodeId(nextTitle))
              }
            }}
            placeholder="Sales CSV"
          />
        </label>
        <label>
          <span>Node ID</span>
          <input
            className={duplicateId || invalidId ? 'invalid' : ''}
            value={nodeId}
            onChange={(event) => {
              setNodeIdTouched(true)
              setNodeId(normalizeNodeId(event.target.value))
            }}
            placeholder="sales_csv"
            spellCheck={false}
          />
        </label>
        <label>
          <span>Output name</span>
          <input
            value={artifactName}
            onChange={(event) => setArtifactName(normalizeFreeformSnakeCase(event.target.value))}
            placeholder="file"
            spellCheck={false}
          />
          {invalidArtifactName ? <span className="field-note error">Output name is required.</span> : <span className="field-note">The uploaded file keeps its original extension.</span>}
        </label>
        <label>
          <span>Upload file</span>
          <input
            type="file"
            onChange={(event) => {
              const nextFile = event.target.files?.[0] ?? null
              setFile(nextFile)
            }}
          />
          {!file ? <span className="field-note">Optional. Leave blank to create a pending file input.</span> : <span className="field-note">The file node is created, then the file is uploaded.</span>}
        </label>
        <div className="dialog-actions">
          <button className="secondary" onClick={onClose}>Cancel</button>
          <button onClick={submit} disabled={busy || invalidTitle || invalidId || duplicateId || invalidArtifactName}>{busy ? 'Creating...' : 'Create file'}</button>
        </div>
      </div>
    </Modal>
  )
}


function normalizeNodeId(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}


function normalizeFreeformSnakeCase(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
}


function makeDefaultOutput(index: number): ConstantValueOutput {
  return {
    key: `output-${index}-${Math.random().toString(36).slice(2, 8)}`,
    name: index === 0 ? 'value' : `value_${index + 1}`,
    dataType: 'int',
    value: literalValueForType('int'),
  }
}


function placeholderForType(dataType: ConstantValueType): string {
  switch (dataType) {
    case 'int':
      return '42'
    case 'float':
      return '3.14'
    case 'bool':
      return 'True'
    case 'str':
      return "'hello'"
    case 'list':
      return '[1, 2, 3]'
    case 'dict':
      return '{"key": "value"}'
    case 'object':
      return 'Edit notebook value'
  }
}


function literalValueForType(dataType: ConstantValueType): string {
  switch (dataType) {
    case 'int':
      return '42'
    case 'float':
      return '3.14'
    case 'bool':
      return 'True'
    case 'str':
      return "'hello'"
    case 'list':
      return '[1, 2, 3]'
    case 'dict':
      return '{"key": "value"}'
    case 'object':
      return 'None'
  }
}


function updateOutput(
  setOutputs: (value: ConstantValueOutput[] | ((current: ConstantValueOutput[]) => ConstantValueOutput[])) => void,
  key: string,
  patch: Partial<ConstantValueOutput>,
) {
  setOutputs((current) => current.map((output) => (output.key === key ? { ...output, ...patch } : output)))
}


function handleOutputTypeChange(
  key: string,
  event: ChangeEvent<HTMLSelectElement>,
  setOutputs: (value: ConstantValueOutput[] | ((current: ConstantValueOutput[]) => ConstantValueOutput[])) => void,
) {
  const dataType = event.target.value as ConstantValueType
  setOutputs((current) => current.map((output) => (
    output.key === key
      ? { ...output, dataType, value: literalValueForType(dataType) }
      : output
  )))
}


function duplicateNames(values: string[]): Set<string> {
  const seen = new Set<string>()
  const duplicates = new Set<string>()
  for (const value of values) {
    if (!value) {
      continue
    }
    if (seen.has(value)) {
      duplicates.add(value)
      continue
    }
    seen.add(value)
  }
  return duplicates
}
