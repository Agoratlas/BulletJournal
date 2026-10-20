import { useState, type FormEvent } from 'react'
import type { NotebookDraftInput } from '../lib/notebookDrafts'
import { Modal } from './Dialogs'
import { TYPE_COLORS } from './PortLabel'
import { formatType } from '../lib/helpers'

export function NotebookInputDialog({ input, existingIds, onClose, onSave }: {
  input: NotebookDraftInput
  existingIds: string[]
  onClose: () => void
  onSave: (input: NotebookDraftInput) => void
}) {
  const [name, setName] = useState(input.name)
  const [description, setDescription] = useState(input.description)
  const [defaultToNone, setDefaultToNone] = useState(input.defaultToNone)
  const resolvedName = name.trim()
  const invalid = !/^[a-z0-9_]+$/.test(resolvedName)
  const duplicate = existingIds.includes(resolvedName)

  function submit(event: FormEvent) {
    event.preventDefault()
    if (invalid || duplicate) return
    onSave({ ...input, name: resolvedName, description, defaultToNone })
  }

  return (
    <Modal title={<span className="notebook-input-dialog-title">Add notebook input<span className="port-type-label"><span className="port-type-dot" style={{ backgroundColor: TYPE_COLORS[input.dataType] ?? TYPE_COLORS.object }} aria-hidden="true" /><span>{formatType(input.dataType)}</span></span></span>} onClose={onClose} contentClassName="notebook-input-dialog">
      <form className="form-grid" onSubmit={submit}>
        <label>
          <span>Artifact ID</span>
          <input autoFocus value={name} onChange={(event) => setName(event.target.value)} spellCheck={false} className={invalid || duplicate ? 'invalid' : ''} />
          {invalid || duplicate ? <span className="field-note error">{duplicate ? 'This input ID is already in use.' : 'Use lowercase letters, digits, and underscores.'}</span> : null}
        </label>
        <label><span>Documentation</span><input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        <label className="toggle-row notebook-input-default"><input type="checkbox" checked={defaultToNone} onChange={(event) => setDefaultToNone(event.target.checked)} /><span>Default to None</span></label>
        <div className="dialog-actions"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button type="submit" disabled={invalid || duplicate}>Add input</button></div>
      </form>
    </Modal>
  )
}
