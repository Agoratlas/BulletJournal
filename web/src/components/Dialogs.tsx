import type { ChangeEvent, DragEvent, FormEvent, ReactNode } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { ConstantValueType } from '../appTypes'
import { AREA_COLOR_KEYS, AREA_TITLE_POSITIONS, type AreaColorKey, type AreaTitlePosition } from '../lib/area'
import { formatType } from '../lib/helpers'
import { X } from './Icons'

type ModalProps = {
  title: string
  onClose: () => void
  children: ReactNode
  contentClassName?: string
  showCloseButton?: boolean
}

export function Modal({ title, onClose, children, contentClassName, showCloseButton = true }: ModalProps) {
  const shouldCloseOnClickRef = useRef(false)

  return (
    <div
      className="modal-backdrop"
      onPointerDown={(event) => {
        shouldCloseOnClickRef.current = event.target === event.currentTarget
      }}
      onClick={(event) => {
        const shouldClose = shouldCloseOnClickRef.current && event.target === event.currentTarget
        shouldCloseOnClickRef.current = false
        if (shouldClose) {
          onClose()
        }
      }}
    >
      <div
        className={`modal-card${contentClassName ? ` ${contentClassName}` : ''}`}
        onPointerDown={() => {
          shouldCloseOnClickRef.current = false
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h3>{title}</h3>
          {showCloseButton ? <button className="ghost-button modal-close-button" onClick={onClose} aria-label="Close dialog"><X width={18} height={18} /></button> : null}
        </div>
        {children}
      </div>
    </div>
  )
}

type ConfirmDialogProps = {
  title: string
  message: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  alternateLabel?: string
  tone?: 'default' | 'danger'
  confirmTone?: 'default' | 'danger' | 'success' | 'warning'
  alternateTone?: 'default' | 'danger' | 'success' | 'warning'
  alternateDisabled?: boolean
  alternateHelpText?: string
  cancelTone?: 'default' | 'danger' | 'success' | 'warning'
  onConfirm: () => void
  onAlternate?: () => void
  onClose: () => void
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  alternateLabel,
  tone = 'default',
  confirmTone,
  alternateTone = 'default',
  alternateDisabled = false,
  alternateHelpText,
  cancelTone = 'default',
  onConfirm,
  onAlternate,
  onClose,
}: ConfirmDialogProps) {
  const resolvedConfirmTone = confirmTone ?? tone
  const actionButtonClassName = (buttonTone: 'default' | 'danger' | 'success' | 'warning') => {
    if (buttonTone === 'danger') {
      return 'danger'
    }
    if (buttonTone === 'success') {
      return 'success'
    }
    if (buttonTone === 'warning') {
      return 'warning'
    }
    return undefined
  }

  const neutralButtonClassName = (buttonTone: 'default' | 'danger' | 'success' | 'warning') => {
    if (buttonTone === 'danger') {
      return 'danger'
    }
    if (buttonTone === 'success') {
      return 'success'
    }
    if (buttonTone === 'warning') {
      return 'warning'
    }
    return 'secondary'
  }

  return (
    <Modal title={title} onClose={onClose} contentClassName="confirm-dialog-card" showCloseButton={false}>
      <div className="confirm-dialog-body">
        <div className="confirm-dialog-copy">{message}</div>
        <div className="dialog-actions">
          <button type="button" className={neutralButtonClassName(cancelTone)} onClick={onClose}>{cancelLabel}</button>
          {alternateLabel && onAlternate ? (
            <span className="confirm-dialog-action-shell">
              <button
                type="button"
                className={`${neutralButtonClassName(alternateTone)}${alternateDisabled ? ' disabled' : ''}`}
                onClick={onAlternate}
                disabled={alternateDisabled}
              >
                {alternateLabel}
              </button>
              {alternateDisabled && alternateHelpText ? (
                <span className="artifact-tooltip confirm-dialog-tooltip" role="tooltip">{alternateHelpText}</span>
              ) : null}
            </span>
          ) : null}
          <button type="button" className={actionButtonClassName(resolvedConfirmTone)} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </Modal>
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

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit()
  }

  return (
    <Modal title={`Create ${blockLabel}`} onClose={onClose}>
      <form className="form-grid" onSubmit={handleSubmit}>
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
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy || invalidTitle || invalidId || duplicateId}>{busy ? 'Creating...' : submitLabel}</button>
        </div>
      </form>
    </Modal>
  )
}

type CreatePipelineDialogProps = {
  pipelineLabel: string
  existingIds: string[]
  templateNodeIds: string[]
  suggestedPrefix: string
  requirePrefix: boolean
  onClose: () => void
  onCreate: (payload: { nodeIdPrefix: string | null }) => Promise<void>
}

export function CreatePipelineDialog({
  pipelineLabel,
  existingIds,
  templateNodeIds,
  suggestedPrefix,
  requirePrefix,
  onClose,
  onCreate,
}: CreatePipelineDialogProps) {
  const [prefix, setPrefix] = useState(suggestedPrefix)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setPrefix(suggestedPrefix)
    setBusy(false)
  }, [suggestedPrefix])

  const resolvedPrefix = useMemo(() => normalizeNodeId(prefix), [prefix])
  const prefixedNodeIds = useMemo(() => {
    return templateNodeIds.map((nodeId) => (resolvedPrefix ? `${resolvedPrefix}_${nodeId}` : nodeId))
  }, [resolvedPrefix, templateNodeIds])
  const duplicateIds = prefixedNodeIds.filter((nodeId) => existingIds.includes(nodeId))
  const missingRequiredPrefix = requirePrefix && !resolvedPrefix
  const invalid = missingRequiredPrefix || duplicateIds.length > 0

  async function submit() {
    if (invalid) {
      return
    }
    setBusy(true)
    try {
      await onCreate({ nodeIdPrefix: resolvedPrefix || null })
      onClose()
    } finally {
      setBusy(false)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit()
  }

  return (
    <Modal title={`Create ${pipelineLabel}`} onClose={onClose}>
      <form className="form-grid" onSubmit={handleSubmit}>
        <label>
          <span>Node ID prefix</span>
          <input
            className={invalid ? 'invalid' : ''}
            value={prefix}
            onChange={(event) => setPrefix(event.target.value)}
            placeholder="study_a"
            spellCheck={false}
          />
          {missingRequiredPrefix ? (
            <span className="field-note error">A prefix is required because this pipeline would reuse existing node IDs.</span>
          ) : duplicateIds.length ? (
            <span className="field-note error">These node IDs are still taken: {duplicateIds.join(', ')}</span>
          ) : requirePrefix ? (
            <span className="field-note">The prefix is added to every node in this pipeline.</span>
          ) : (
            <span className="field-note">Optional. Leave blank to keep the template node IDs as-is.</span>
          )}
        </label>
        <label>
          <span>Resulting node IDs</span>
          <input value={prefixedNodeIds.join(', ')} readOnly spellCheck={false} />
          <span className="field-note">Preview of the nodes created from this pipeline template.</span>
        </label>
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy || invalid}>{busy ? 'Creating...' : 'Create pipeline'}</button>
        </div>
      </form>
    </Modal>
  )
}

type CreateOrganizerPortDialogProps = {
  suggestedName: string
  onClose: () => void
  onCreate: (payload: { name: string }) => Promise<void>
}

export function CreateOrganizerPortDialog({ suggestedName, onClose, onCreate }: CreateOrganizerPortDialogProps) {
  const [name, setName] = useState(suggestedName)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setName(suggestedName)
    setBusy(false)
  }, [suggestedName])

  const resolvedName = normalizeFreeformSnakeCase(name)
  const invalidName = !resolvedName

  async function submit() {
    if (invalidName) {
      return
    }
    setBusy(true)
    try {
      await onCreate({ name: resolvedName })
      onClose()
    } finally {
      setBusy(false)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit()
  }

  function handleJsonKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter') {
      return
    }
    if (event.shiftKey || event.ctrlKey || event.metaKey) {
      return
    }
    event.preventDefault()
    void submit()
  }

  return (
    <Modal title="Create Organizer Lane" onClose={onClose} contentClassName="organizer-lane-dialog-card">
      <form className="form-grid" onSubmit={handleSubmit}>
        <label>
          <span>Port name</span>
          <input
            className={invalidName ? 'invalid' : ''}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="value"
            spellCheck={false}
          />
          {invalidName
            ? <span className="field-note error">Port name is required.</span>
            : <span className="field-note">Shown on both sides of the organizer.</span>}
        </label>
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy || invalidName}>{busy ? 'Creating...' : 'Create lane'}</button>
        </div>
      </form>
    </Modal>
  )
}

type EditOrganizerDialogProps = {
  title: string
  initialPorts: Array<{ key: string; name: string; data_type: string }>
  saveDisabledMessage?: string | null
  onClose: () => void
  onSave: (ports: Array<{ key: string; name: string; data_type: string }>) => Promise<void>
}

export function EditOrganizerDialog({
  title,
  initialPorts,
  saveDisabledMessage = null,
  onClose,
  onSave,
}: EditOrganizerDialogProps) {
  const [ports, setPorts] = useState(initialPorts.map((port) => ({ ...port })))
  const [busy, setBusy] = useState(false)
  const [draggedKey, setDraggedKey] = useState<string | null>(null)

  useEffect(() => {
    setPorts(initialPorts.map((port) => ({ ...port })))
    setBusy(false)
    setDraggedKey(null)
  }, [initialPorts])

  const normalizedPorts = useMemo(
    () => ports.map((port) => ({ ...port, name: normalizeFreeformSnakeCase(port.name) })),
    [ports],
  )
  const duplicatePortNames = duplicateNames(normalizedPorts.map((port) => port.name))
  const hasInvalidPort = normalizedPorts.some((port) => !port.name)
  const changed = JSON.stringify(normalizedPorts) !== JSON.stringify(initialPorts)

  function movePort(dragKey: string, targetKey: string) {
    if (dragKey === targetKey) {
      return
    }
    setPorts((current) => {
      const next = current.map((port) => ({ ...port }))
      const fromIndex = next.findIndex((port) => port.key === dragKey)
      const toIndex = next.findIndex((port) => port.key === targetKey)
      if (fromIndex === -1 || toIndex === -1) {
        return current
      }
      const [moved] = next.splice(fromIndex, 1)
      next.splice(toIndex, 0, moved)
      return next
    })
  }

  async function submit() {
    if (busy || hasInvalidPort || duplicatePortNames.size > 0 || saveDisabledMessage) {
      return
    }
    setBusy(true)
    try {
      await onSave(normalizedPorts)
      onClose()
    } finally {
      setBusy(false)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit()
  }

  function handleRowDragOver(event: DragEvent<HTMLDivElement>, targetKey: string) {
    event.preventDefault()
    if (!draggedKey || draggedKey === targetKey) {
      return
    }
    movePort(draggedKey, targetKey)
  }

  return (
    <Modal title={title} onClose={onClose} contentClassName="organizer-editor-dialog-card">
      <form className="form-grid organizer-editor-form" onSubmit={handleSubmit}>
        <div className="organizer-editor-list">
          {ports.map((port) => {
            const normalizedName = normalizeFreeformSnakeCase(port.name)
            const duplicatePort = normalizedName && duplicatePortNames.has(normalizedName)
            return (
              <div
                key={port.key}
                className={`organizer-editor-row ${draggedKey === port.key ? 'dragging' : ''}`}
                onDragOver={(event) => handleRowDragOver(event, port.key)}
                onDrop={(event) => {
                  event.preventDefault()
                  setDraggedKey(null)
                }}
              >
                <button
                  type="button"
                  className="organizer-editor-grip"
                  draggable={!saveDisabledMessage}
                  disabled={Boolean(saveDisabledMessage)}
                  onDragStart={(event) => {
                    setDraggedKey(port.key)
                    event.dataTransfer.effectAllowed = 'move'
                    event.dataTransfer.setData('text/plain', port.key)
                  }}
                  onDragEnd={() => setDraggedKey(null)}
                  aria-label={`Reorder lane ${port.name || port.key}`}
                >
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                </button>
                <input
                  className={duplicatePort || !normalizedName ? 'invalid' : ''}
                  value={port.name}
                  disabled={Boolean(saveDisabledMessage)}
                  onChange={(event) => setPorts((current) => current.map((item) => (
                    item.key === port.key ? { ...item, name: event.target.value } : item
                  )))}
                  placeholder="lane_name"
                  spellCheck={false}
                />
                <div className="organizer-editor-type">{formatType(port.data_type)}</div>
                <button
                  type="button"
                  className="danger icon-pill small-icon-pill"
                  disabled={Boolean(saveDisabledMessage)}
                  onClick={() => setPorts((current) => current.filter((item) => item.key !== port.key))}
                  aria-label={`Delete lane ${port.name || port.key}`}
                >
                  <X width={16} height={16} />
                </button>
              </div>
            )
          })}
          {!ports.length ? <p className="muted-copy">Connect a port to the organizer to create the first lane.</p> : null}
        </div>
        {saveDisabledMessage ? <p className="field-note">{saveDisabledMessage}</p> : null}
        {!saveDisabledMessage && duplicatePortNames.size > 0 ? <p className="field-note error">Lane names must be unique.</p> : null}
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy || !changed || hasInvalidPort || duplicatePortNames.size > 0 || Boolean(saveDisabledMessage)}>
            {busy ? 'Saving...' : 'Save changes'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

type EditAreaDialogProps = {
  title: string
  initialTitle: string
  initialTitlePosition: AreaTitlePosition
  initialColor: AreaColorKey
  initialFilled: boolean
  submitLabel?: string
  allowUnchangedSubmit?: boolean
  saveDisabledMessage?: string | null
  onClose: () => void
  onSave: (payload: { title: string; titlePosition: AreaTitlePosition; color: AreaColorKey; filled: boolean }) => Promise<void>
}

const AREA_TITLE_POSITION_LABELS: Record<AreaTitlePosition, string> = {
  'top-left': 'Top left',
  'top-center': 'Top center',
  'top-right': 'Top right',
  'right-center': 'Center right',
  'bottom-right': 'Bottom right',
  'bottom-center': 'Bottom center',
  'bottom-left': 'Bottom left',
  'left-center': 'Center left',
}

export function EditAreaDialog({
  title,
  initialTitle,
  initialTitlePosition,
  initialColor,
  initialFilled,
  submitLabel = 'Save changes',
  allowUnchangedSubmit = false,
  saveDisabledMessage = null,
  onClose,
  onSave,
}: EditAreaDialogProps) {
  const [draftTitle, setDraftTitle] = useState(initialTitle)
  const [titlePosition, setTitlePosition] = useState<AreaTitlePosition>(initialTitlePosition)
  const [color, setColor] = useState<AreaColorKey>(initialColor)
  const [filled, setFilled] = useState(initialFilled)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setDraftTitle(initialTitle)
    setTitlePosition(initialTitlePosition)
    setColor(initialColor)
    setFilled(initialFilled)
    setBusy(false)
  }, [initialColor, initialFilled, initialTitle, initialTitlePosition])

  const changed = draftTitle !== initialTitle
    || titlePosition !== initialTitlePosition
    || color !== initialColor
    || filled !== initialFilled
  const swatchRows = useMemo(
    () => [
      { filled: true, label: 'Filled' },
      { filled: false, label: 'Transparent' },
    ] as const,
    [],
  )

  async function submit() {
    if (busy || saveDisabledMessage) {
      return
    }
    setBusy(true)
    try {
      await onSave({ title: draftTitle.trim(), titlePosition, color, filled })
      onClose()
    } finally {
      setBusy(false)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit()
  }

  return (
    <Modal title={title} onClose={onClose} contentClassName="area-editor-dialog-card">
      <form className="form-grid area-editor-form" onSubmit={handleSubmit}>
        <label>
          <span>Title</span>
          <input
            value={draftTitle}
            onChange={(event) => setDraftTitle(event.target.value)}
            placeholder="Optional"
            disabled={Boolean(saveDisabledMessage)}
          />
        </label>
        <label>
          <span>Title position</span>
          <select value={titlePosition} onChange={(event) => setTitlePosition(event.target.value as AreaTitlePosition)} disabled={Boolean(saveDisabledMessage)}>
            {AREA_TITLE_POSITIONS.map((position) => (
              <option key={position} value={position}>{AREA_TITLE_POSITION_LABELS[position]}</option>
            ))}
          </select>
        </label>
        <div className="area-color-section">
          <span>Color</span>
          <div className="area-color-grid">
            {swatchRows.flatMap((row) => AREA_COLOR_KEYS.map((colorKey) => {
              const selected = color === colorKey && filled === row.filled
              return (
                <button
                  key={`${row.label}-${colorKey}`}
                  type="button"
                  className={`area-color-swatch area-color-${colorKey} ${row.filled ? 'filled' : 'transparent'} ${selected ? 'selected' : ''}`}
                  data-area-color={colorKey}
                  data-area-filled={row.filled ? 'true' : 'false'}
                  disabled={Boolean(saveDisabledMessage)}
                  onClick={() => {
                    setColor(colorKey)
                    setFilled(row.filled)
                  }}
                  aria-label={`Use ${row.filled ? 'filled' : 'transparent'} ${colorKey} area color`}
                >
                  {selected ? (
                    <svg className="area-color-check" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                      <path d="M3 8.5L6.5 12L13 4.5" />
                    </svg>
                  ) : null}
                </button>
              )
            }))}
          </div>
        </div>
        {saveDisabledMessage ? <p className="field-note">{saveDisabledMessage}</p> : null}
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy || (!changed && !allowUnchangedSubmit) || Boolean(saveDisabledMessage)}>{busy ? 'Saving...' : submitLabel}</button>
        </div>
      </form>
    </Modal>
  )
}

type EditConstantDialogProps = {
  mode?: 'create' | 'edit'
  initialDataType: ConstantValueType
  allowTypeChange?: boolean
  initialJsonValue?: string
  initialJsonTooLarge?: boolean
  uploadDisabledMessage?: string | null
  onClose: () => void
  onSave: (payload: { dataType: ConstantValueType; jsonText: string; uploadFile: File | null; jsonUploadFile: File | null }) => Promise<void>
}

export function EditConstantDialog({ mode = 'create', initialDataType, allowTypeChange = true, initialJsonValue = '', initialJsonTooLarge = false, uploadDisabledMessage = null, onClose, onSave }: EditConstantDialogProps) {
  const [dataType, setDataType] = useState<ConstantValueType>(initialDataType)
  const [jsonText, setJsonText] = useState(initialJsonValue)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [jsonUploadFile, setJsonUploadFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  useEffect(() => {
    setDataType(initialDataType)
    setJsonText(initialJsonValue)
    setUploadFile(null)
    setJsonUploadFile(null)
    setBusy(false)
    setValidationError(null)
  }, [initialDataType, initialJsonValue])

  const usesUpload = dataType === 'file' || dataType === 'pandas.DataFrame'
  const supportsJsonUpload = dataType === 'list' || dataType === 'dict'
  const usesSingleLineEditor = dataType === 'str' || dataType === 'int' || dataType === 'float' || dataType === 'bool'
  const jsonRows = dataType === 'list' || dataType === 'dict' ? 10 : 4

  async function submit() {
    const validationMessage = await validateConstantInput({ dataType, jsonText, jsonUploadFile })
    if (validationMessage) {
      setValidationError(validationMessage)
      return
    }
    setBusy(true)
    try {
      setValidationError(null)
      await onSave({ dataType, jsonText, uploadFile, jsonUploadFile })
      onClose()
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : 'Invalid constant value.')
    } finally {
      setBusy(false)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit()
  }

  function handleJsonKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter') {
      return
    }
    if (event.shiftKey || event.ctrlKey || event.metaKey) {
      return
    }
    event.preventDefault()
    void submit()
  }

  return (
    <Modal title={mode === 'edit' ? 'Edit Constant' : 'Create Constant'} onClose={onClose}>
      <form className="form-grid" onSubmit={handleSubmit}>
        <label>
          <span>Type</span>
          <select value={dataType} onChange={(event) => setDataType(event.target.value as ConstantValueType)} disabled={!allowTypeChange}>
            <option value="pandas.DataFrame">DataFrame</option>
            <option value="file">file</option>
            <option value="int">int</option>
            <option value="float">float</option>
            <option value="dict">dict</option>
            <option value="list">list</option>
            <option value="str">str</option>
            <option value="bool">bool</option>
          </select>
        </label>

        {usesUpload ? (
          <label>
            <span>{dataType === 'pandas.DataFrame' ? 'Upload CSV' : 'Upload file'}</span>
            <input
              type="file"
              accept={dataType === 'pandas.DataFrame' ? '.csv,text/csv' : undefined}
              disabled={Boolean(uploadDisabledMessage)}
              onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
            />
            {uploadDisabledMessage ? (
              <span className="field-note">{uploadDisabledMessage}</span>
            ) : !uploadFile ? (
              <span className="field-note">{mode === 'edit' ? 'Optional. Leave blank to keep the current value.' : 'Optional. Leave blank to create a pending constant.'}</span>
            ) : (
              <span className="field-note">{uploadFile.name}</span>
            )}
          </label>
        ) : (
          <label>
            <span>JSON-formatted value</span>
            {usesSingleLineEditor ? (
              <input
                className="constant-json-input"
                value={jsonText}
                onChange={(event) => {
                  setJsonText(event.target.value)
                  if (validationError) {
                    setValidationError(null)
                  }
                }}
                placeholder={placeholderForType(dataType)}
                spellCheck={false}
              />
            ) : (
              <textarea
                className="constant-json-editor"
                rows={jsonRows}
                value={jsonText}
                onChange={(event) => {
                  setJsonText(event.target.value)
                  if (validationError) {
                    setValidationError(null)
                  }
                }}
                onKeyDown={handleJsonKeyDown}
                placeholder={placeholderForType(dataType)}
                spellCheck={false}
              />
            )}
            {initialJsonTooLarge && mode === 'edit' ? <span className="field-note">Existing value is larger than 10 kB, so the editor starts blank.</span> : null}
            {!supportsJsonUpload ? <span className="field-note">Leave blank to keep this constant pending.</span> : null}
          </label>
        )}

        {!usesUpload && supportsJsonUpload ? (
          <label>
            <span>Upload JSON</span>
            <input type="file" accept="application/json,.json" onChange={(event) => {
              setJsonUploadFile(event.target.files?.[0] ?? null)
              if (validationError) {
                setValidationError(null)
              }
            }} />
            {!jsonUploadFile ? <span className="field-note">Optional. Upload a `.json` file instead of typing the value.</span> : <span className="field-note">{jsonUploadFile.name}</span>}
          </label>
        ) : null}

        {validationError ? <p className="field-note error">{validationError}</p> : null}

        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy}>{busy ? (mode === 'edit' ? 'Saving...' : 'Creating...') : (mode === 'edit' ? 'Save changes' : 'Create constant')}</button>
        </div>
      </form>
    </Modal>
  )
}

async function validateConstantInput(payload: { dataType: ConstantValueType; jsonText: string; jsonUploadFile: File | null }): Promise<string | null> {
  if (payload.dataType === 'file' || payload.dataType === 'pandas.DataFrame') {
    return null
  }
  const sourceText = payload.jsonUploadFile
    ? await payload.jsonUploadFile.text()
    : payload.jsonText
  const trimmed = sourceText.trim()
  if (!trimmed) {
    return null
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return 'Value must be valid JSON for the selected type.'
  }
  switch (payload.dataType) {
    case 'bool':
      return typeof parsed === 'boolean' ? null : 'Value must be a JSON boolean.'
    case 'int':
      return typeof parsed === 'number' && Number.isInteger(parsed) ? null : 'Value must be a JSON integer.'
    case 'float':
      return typeof parsed === 'number' && Number.isFinite(parsed) ? null : 'Value must be a JSON number.'
    case 'str':
      return typeof parsed === 'string' ? null : 'Value must be a JSON string.'
    case 'list':
      return Array.isArray(parsed) ? null : 'Value must be a JSON array.'
    case 'dict':
      return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
        ? null
        : 'Value must be a JSON object.'
    default:
      return null
  }
}

type CreateFileDialogProps = {
  suggestedTitle: string
  existingIds: string[]
  onClose: () => void
  mode?: 'create' | 'edit'
  fixedNodeId?: string
  initialArtifactName?: string
  uploadDisabledMessage?: string | null
  onCreate: (payload: { nodeId: string; title: string; file: File | null; artifactName: string }) => Promise<void>
}

export function CreateFileDialog({ suggestedTitle, existingIds, onClose, mode = 'create', fixedNodeId, initialArtifactName = 'file', uploadDisabledMessage = null, onCreate }: CreateFileDialogProps) {
  const [title, setTitle] = useState(suggestedTitle)
  const [nodeId, setNodeId] = useState(fixedNodeId ?? normalizeNodeId(suggestedTitle))
  const [nodeIdTouched, setNodeIdTouched] = useState(false)
  const [artifactName, setArtifactName] = useState(initialArtifactName)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setTitle(suggestedTitle)
    setNodeId(fixedNodeId ?? normalizeNodeId(suggestedTitle))
    setNodeIdTouched(false)
    setArtifactName(initialArtifactName)
    setFile(null)
    setBusy(false)
  }, [fixedNodeId, initialArtifactName, suggestedTitle])

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

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit()
  }

  return (
    <Modal title={mode === 'edit' ? 'Edit File Block' : 'Create File'} onClose={onClose}>
      <form className="form-grid" onSubmit={handleSubmit}>
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
            disabled={mode === 'edit'}
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
            disabled={mode === 'edit'}
            onChange={(event) => setArtifactName(normalizeFreeformSnakeCase(event.target.value))}
            placeholder="file"
            spellCheck={false}
          />
          {mode === 'edit'
            ? <span className="field-note">Output names for existing file blocks are fixed.</span>
            : invalidArtifactName
              ? <span className="field-note error">Output name is required.</span>
              : <span className="field-note">The uploaded file keeps its original extension.</span>}
        </label>
        <label>
          <span>Upload file</span>
          <input
            type="file"
            disabled={Boolean(uploadDisabledMessage)}
            onChange={(event) => {
              const nextFile = event.target.files?.[0] ?? null
              setFile(nextFile)
            }}
          />
          {uploadDisabledMessage
            ? <span className="field-note">{uploadDisabledMessage}</span>
            : !file
            ? <span className="field-note">{mode === 'edit' ? 'Optional. Leave blank to keep the current file state.' : 'Optional. Leave blank to create a pending file input.'}</span>
            : <span className="field-note">{mode === 'edit' ? 'The new file uploads after saving changes.' : 'The file node is created, then the file is uploaded.'}</span>}
        </label>
        <div className="dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy || invalidTitle || invalidId || duplicateId || invalidArtifactName}>{busy ? (mode === 'edit' ? 'Saving...' : 'Creating...') : (mode === 'edit' ? 'Save changes' : 'Create file')}</button>
        </div>
      </form>
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


function placeholderForType(dataType: ConstantValueType): string {
  switch (dataType) {
    case 'int':
      return '42'
    case 'float':
      return '3.14'
    case 'bool':
      return 'true'
    case 'str':
      return '"hello"'
    case 'list':
      return '[1, 2, 3]'
    case 'dict':
      return '{"key": "value"}'
    case 'file':
      return ''
    case 'pandas.DataFrame':
      return ''
  }
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
