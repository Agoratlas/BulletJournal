import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'

import { ChevronDown, ChevronUp, ChevronsUpDown, Funnel, Palette } from '../../components/Icons'
import type { AssetFilter, AssetFilterKind, AssetHighlight, AssetSort, PreparedTablePayload } from '../../lib/types'
import {
  buildFilterFromInputs,
  dataTypeCategory,
  filterDraftFromColumn,
  filterKindLabel,
  filterKindsForDataType,
  formatFilterSummary,
  rangeFilterPlaceholder,
  valueFilterPlaceholder,
} from './modifiers'
import type { ModifierColumn } from './types'

function DataFrameHeaderFilterMenu({
  column,
  activeFilter,
  disabled,
  onApplyFilter,
  onRemoveFilter,
  onClose,
}: {
  column: ModifierColumn
  activeFilter: AssetFilter | null
  disabled: boolean
  onApplyFilter: (filter: AssetFilter) => void
  onRemoveFilter: (columnId: string) => void
  onClose: () => void
}) {
  const initialDraft = filterDraftFromColumn(column, activeFilter)
  const [selectedKind, setSelectedKind] = useState<AssetFilterKind>(initialDraft.kind)
  const [rangeLower, setRangeLower] = useState(initialDraft.rangeLower)
  const [rangeUpper, setRangeUpper] = useState(initialDraft.rangeUpper)
  const [valueInput, setValueInput] = useState(initialDraft.valueInput)
  const [includeNull, setIncludeNull] = useState(initialDraft.includeNull)
  const [regexPattern, setRegexPattern] = useState(initialDraft.regexPattern)
  const [regexCaseSensitive, setRegexCaseSensitive] = useState(initialDraft.regexCaseSensitive)
  const [editorError, setEditorError] = useState<string | null>(null)
  const category = dataTypeCategory(column.dataType)

  useEffect(() => {
    const nextDraft = filterDraftFromColumn(column, activeFilter)
    setSelectedKind(nextDraft.kind)
    setRangeLower(nextDraft.rangeLower)
    setRangeUpper(nextDraft.rangeUpper)
    setValueInput(nextDraft.valueInput)
    setIncludeNull(nextDraft.includeNull)
    setRegexPattern(nextDraft.regexPattern)
    setRegexCaseSensitive(nextDraft.regexCaseSensitive)
    setEditorError(null)
  }, [activeFilter, column.dataType, column.id])

  function applyCurrentFilter() {
    try {
      const filter = buildFilterFromInputs({
        column,
        kind: selectedKind,
        rangeLower,
        rangeUpper,
        valueInput,
        includeNull,
        regexPattern,
        regexCaseSensitive,
      })
      setEditorError(null)
      onApplyFilter(filter)
      onClose()
    } catch (error) {
      setEditorError(error instanceof Error ? error.message : 'Could not apply this filter.')
    }
  }

  return (
    <div className="asset-table-filter-menu" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      const target = event.target
      if (
        event.key === 'Enter'
        && (target instanceof HTMLInputElement || target instanceof HTMLSelectElement)
      ) {
        event.preventDefault()
        applyCurrentFilter()
      }
    }}>
      <select
        value={selectedKind}
        onChange={(event) => {
          setSelectedKind(event.target.value as AssetFilterKind)
          setEditorError(null)
        }}
        disabled={disabled}
        aria-label={`Filter type for ${column.title}`}
      >
        {column.filterKinds.map((kind) => (
          <option key={kind} value={kind}>{filterKindLabel(kind)}</option>
        ))}
      </select>

      {selectedKind === 'range' ? (
        <div className="asset-table-filter-range-fields">
          <input
            value={rangeLower}
            onChange={(event) => setRangeLower(event.target.value)}
            disabled={disabled}
            placeholder={rangeFilterPlaceholder(column.dataType, 'lower')}
            aria-label={`Lower bound for ${column.title}`}
            inputMode={category === 'numeric' ? 'decimal' : undefined}
          />
          <span aria-hidden="true">-</span>
          <input
            value={rangeUpper}
            onChange={(event) => setRangeUpper(event.target.value)}
            disabled={disabled}
            placeholder={rangeFilterPlaceholder(column.dataType, 'upper')}
            aria-label={`Upper bound for ${column.title}`}
            inputMode={category === 'numeric' ? 'decimal' : undefined}
          />
        </div>
      ) : null}

      {selectedKind === 'value' ? (
        <>
          {category === 'bool' ? (
            <select
              value={valueInput}
              onChange={(event) => setValueInput(event.target.value)}
              disabled={disabled}
              aria-label={`Value filter for ${column.title}`}
            >
              <option value="">Select value</option>
              <option value="true">True</option>
              <option value="false">False</option>
            </select>
          ) : (
            <input
              value={valueInput}
              onChange={(event) => setValueInput(event.target.value)}
              disabled={disabled}
              placeholder={valueFilterPlaceholder(column.dataType)}
              aria-label={`Value filter for ${column.title}`}
              inputMode={category === 'numeric' ? 'decimal' : undefined}
            />
          )}
          <label className="asset-table-filter-checkbox">
            <input
              type="checkbox"
              checked={includeNull}
              onChange={(event) => setIncludeNull(event.target.checked)}
              disabled={disabled}
            />
            <span>Include empty</span>
          </label>
        </>
      ) : null}

      {selectedKind === 'regex' ? (
        <>
          <input
            value={regexPattern}
            onChange={(event) => setRegexPattern(event.target.value)}
            disabled={disabled}
            placeholder="Pattern"
            aria-label={`Regex filter for ${column.title}`}
          />
          <label className="asset-table-filter-checkbox">
            <input
              type="checkbox"
              checked={regexCaseSensitive}
              onChange={(event) => setRegexCaseSensitive(event.target.checked)}
              disabled={disabled}
            />
            <span>Case-sensitive</span>
          </label>
        </>
      ) : null}

      {editorError ? <p className="asset-table-filter-error">{editorError}</p> : null}

      <div className="asset-table-filter-menu-actions">
        {activeFilter ? (
          <button
            type="button"
            className="ghost asset-inline-action asset-table-filter-clear"
            onClick={() => {
              onRemoveFilter(column.id)
              onClose()
            }}
            disabled={disabled}
          >
            Clear
          </button>
        ) : <span />}
        <button type="button" className="secondary asset-table-filter-apply" onClick={applyCurrentFilter} disabled={disabled}>
          Apply
        </button>
      </div>
    </div>
  )
}

function DataFrameHeaderHighlightMenu({
  column,
  activeHighlights,
  disabled,
  onApplyHighlights,
  onClose,
}: {
  column: ModifierColumn
  activeHighlights: AssetHighlight[]
  disabled: boolean
  onApplyHighlights: (columnId: string, highlights: AssetHighlight[]) => void
  onClose: () => void
}) {
  const initial = activeHighlights[0]
  const draft = filterDraftFromColumn(column, initial)
  const [kind, setKind] = useState<AssetFilterKind>(draft.kind)
  const [rangeLower, setRangeLower] = useState(draft.rangeLower)
  const [rangeUpper, setRangeUpper] = useState(draft.rangeUpper)
  const [valueInput, setValueInput] = useState(draft.valueInput)
  const [includeNull, setIncludeNull] = useState(draft.includeNull)
  const [regexPattern, setRegexPattern] = useState(draft.regexPattern)
  const [regexCaseSensitive, setRegexCaseSensitive] = useState(draft.regexCaseSensitive)
  const [color, setColor] = useState(initial?.highlight_color ?? '#f0bd20')
  const [scope, setScope] = useState<'cell' | 'row'>(initial?.highlight_scope ?? 'cell')
  const [error, setError] = useState<string | null>(null)
  const category = dataTypeCategory(column.dataType)
  function apply() {
    try {
      const filter = buildFilterFromInputs({ column, kind, rangeLower, rangeUpper, valueInput, includeNull, regexPattern, regexCaseSensitive })
      onApplyHighlights(column.id, [{ ...filter, highlight_color: color, highlight_scope: scope }])
      onClose()
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : 'Could not apply this highlight.')
    }
  }
  return (
    <div className="asset-table-filter-menu" onClick={(event) => event.stopPropagation()}>
      <select value={kind} onChange={(event) => setKind(event.target.value as AssetFilterKind)} disabled={disabled} aria-label={`Highlight type for ${column.title}`}>
        {column.filterKinds.map((entry) => <option key={entry} value={entry}>{filterKindLabel(entry)}</option>)}
      </select>
      {kind === 'range' ? <div className="asset-table-filter-range-fields"><input value={rangeLower} onChange={(event) => setRangeLower(event.target.value)} placeholder={rangeFilterPlaceholder(column.dataType, 'lower')} inputMode={category === 'numeric' ? 'decimal' : undefined} /><span>-</span><input value={rangeUpper} onChange={(event) => setRangeUpper(event.target.value)} placeholder={rangeFilterPlaceholder(column.dataType, 'upper')} inputMode={category === 'numeric' ? 'decimal' : undefined} /></div> : null}
      {kind === 'value' ? <><input value={valueInput} onChange={(event) => setValueInput(event.target.value)} placeholder={valueFilterPlaceholder(column.dataType)} inputMode={category === 'numeric' ? 'decimal' : undefined} /><label className="asset-table-filter-checkbox"><input type="checkbox" checked={includeNull} onChange={(event) => setIncludeNull(event.target.checked)} /><span>Include empty</span></label></> : null}
      {kind === 'regex' ? <><input value={regexPattern} onChange={(event) => setRegexPattern(event.target.value)} placeholder="Pattern" /><label className="asset-table-filter-checkbox"><input type="checkbox" checked={regexCaseSensitive} onChange={(event) => setRegexCaseSensitive(event.target.checked)} /><span>Case-sensitive</span></label></> : null}
      <div className="asset-table-highlight-style">
        <input type="color" value={color} onChange={(event) => setColor(event.target.value)} disabled={disabled} aria-label={`Highlight color for ${column.title}`} />
        <select value={scope} onChange={(event) => setScope(event.target.value as 'cell' | 'row')} disabled={disabled} aria-label={`Highlight scope for ${column.title}`}>
          <option value="cell">Cell only</option>
          <option value="row">Entire row</option>
        </select>
      </div>
      {error ? <p className="asset-table-filter-error">{error}</p> : null}
      <div className="asset-table-filter-menu-actions"><button type="button" className="ghost asset-inline-action asset-table-filter-clear" disabled={disabled || !activeHighlights.length} onClick={() => { onApplyHighlights(column.id, []); onClose() }}>Clear</button><button type="button" className="secondary asset-table-filter-apply" onClick={apply} disabled={disabled}>Apply</button></div>
    </div>
  )
}

export function PreparedTable({
  table,
  columns,
  activeSort,
  activeFilters,
  activeHighlights,
  disabled,
  onToggleSort,
  onApplyFilter,
  onRemoveFilter,
  onApplyHighlights,
}: {
  table: PreparedTablePayload
  columns: ModifierColumn[]
  activeSort: AssetSort | null
  activeFilters: AssetFilter[]
  activeHighlights: AssetHighlight[]
  disabled: boolean
  onToggleSort: (column: string) => void
  onApplyFilter: (filter: AssetFilter) => void
  onRemoveFilter: (columnId: string) => void
  onApplyHighlights?: (columnId: string, highlights: AssetHighlight[]) => void
}) {
  const [openFilterColumnId, setOpenFilterColumnId] = useState<string | null>(null)
  const [openHighlightColumnId, setOpenHighlightColumnId] = useState<string | null>(null)
  const [columnWidthOverrides, setColumnWidthOverrides] = useState<Record<string, number>>({})
  const [availableWidth, setAvailableWidth] = useState(0)
  const [overflow, setOverflow] = useState({ left: false, right: false })
  const [openMenuPosition, setOpenMenuPosition] = useState<{ left: number; top: number } | null>(null)
  const openMenuAnchorRef = useRef<HTMLDivElement | null>(null)
  const openMenuRef = useRef<HTMLDivElement | null>(null)
  const tableWrapRef = useRef<HTMLDivElement | null>(null)
  const resizeCleanupRef = useRef<(() => void) | null>(null)
  const resolvedColumnWidths = useMemo(
    () => resolveTableColumnWidths(table, columnWidthOverrides, availableWidth),
    [availableWidth, columnWidthOverrides, table.columns, table.rows],
  )
  const tableWidth = Object.values(resolvedColumnWidths).reduce((total, width) => total + width, 0)

  useEffect(() => {
    if (!openFilterColumnId && !openHighlightColumnId) {
      return
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (
        !openMenuAnchorRef.current?.contains(event.target as globalThis.Node)
        && !openMenuRef.current?.contains(event.target as globalThis.Node)
      ) {
        setOpenFilterColumnId(null)
        setOpenHighlightColumnId(null)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenFilterColumnId(null)
        setOpenHighlightColumnId(null)
      }
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [openFilterColumnId, openHighlightColumnId])

  useLayoutEffect(() => {
    if (!openFilterColumnId && !openHighlightColumnId) {
      setOpenMenuPosition(null)
      return
    }
    const updatePosition = () => {
      const anchor = openMenuAnchorRef.current
      if (!anchor) {
        return
      }
      const rect = anchor.getBoundingClientRect()
      setOpenMenuPosition({
        left: Math.min(rect.left, window.innerWidth - 272),
        top: rect.bottom + 6,
      })
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [openFilterColumnId, openHighlightColumnId])

  useEffect(() => {
    if (openFilterColumnId && !table.columns.some((column) => column.id === openFilterColumnId)) {
      setOpenFilterColumnId(null)
    }
  }, [openFilterColumnId, table.columns])

  useEffect(() => () => {
    resizeCleanupRef.current?.()
  }, [])

  useLayoutEffect(() => {
    const tableWrap = tableWrapRef.current
    if (!tableWrap) {
      return
    }
    const updateAvailableWidth = () => {
      setAvailableWidth(tableWrap.clientWidth)
    }
    updateAvailableWidth()
    const observer = new ResizeObserver(updateAvailableWidth)
    observer.observe(tableWrap)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const tableWrap = tableWrapRef.current
    if (!tableWrap) {
      return
    }
    const updateOverflow = () => {
      const maxScrollLeft = tableWrap.scrollWidth - tableWrap.clientWidth
      setOverflow({
        left: tableWrap.scrollLeft > 0,
        right: maxScrollLeft > 0 && tableWrap.scrollLeft < maxScrollLeft,
      })
    }
    const observer = new ResizeObserver(updateOverflow)
    observer.observe(tableWrap)
    tableWrap.addEventListener('scroll', updateOverflow, { passive: true })
    updateOverflow()
    return () => {
      observer.disconnect()
      tableWrap.removeEventListener('scroll', updateOverflow)
    }
  }, [resolvedColumnWidths, table.columns, table.rows])

  function handleColumnResizeStart(event: ReactPointerEvent<HTMLButtonElement>, columnId: string) {
    if (event.button !== 0) {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    resizeCleanupRef.current?.()
    const startWidth = resolvedColumnWidths[columnId] ?? DEFAULT_COLUMN_WIDTH
    const startX = event.clientX
    const handlePointerMove = (moveEvent: PointerEvent) => {
      setColumnWidthOverrides((current) => ({
        ...current,
        [columnId]: Math.max(MIN_COLUMN_WIDTH, Math.round(startWidth + moveEvent.clientX - startX)),
      }))
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', cleanup)
      resizeCleanupRef.current = null
    }
    resizeCleanupRef.current = cleanup
    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', cleanup)
  }

  return (
    <div className={`asset-table-overflow-shell${overflow.left ? ' has-left-overflow' : ''}${overflow.right ? ' has-right-overflow' : ''}`}>
      <span className="asset-table-overflow-shadow is-left" aria-hidden="true" />
      <span className="asset-table-overflow-shadow is-right" aria-hidden="true" />
      <div ref={tableWrapRef} className="table-wrap asset-table-wrap">
        <table className="preview-table asset-table" style={{ width: `${tableWidth}px` }}>
          <colgroup>
            {table.columns.map((column) => (
              <col key={column.id} style={{ width: `${resolvedColumnWidths[column.id] ?? DEFAULT_COLUMN_WIDTH}px` }} />
            ))}
          </colgroup>
        <thead>
          <tr>
            {table.columns.map((column) => {
              const filterColumn = columns.find((entry) => entry.id === column.id) ?? {
                id: column.id,
                title: column.title,
                dataType: column.data_type,
                filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
              }
              const isActive = activeSort?.column === column.id
              const activeFilter = activeFilters.find((entry) => entry.column === column.id) ?? null
              const columnHighlights = activeHighlights.filter((entry) => entry.column === column.id)
              return (
                <th key={column.id}>
                  <div className="asset-table-header-cell">
                    <div className="asset-table-header-main">
                      <div className="asset-table-header-label">
                        <span className="asset-table-column-title" title={column.title}>{column.title}</span>
                        <span className="asset-table-column-type">{column.data_type}</span>
                      </div>
                      <div className="asset-table-header-actions">
                        {column.sortable ? (
                          <button
                            type="button"
                            className={`asset-table-header-action asset-table-sort-toggle${isActive ? ' is-active-sort' : ''}`}
                            onClick={() => onToggleSort(column.id)}
                            disabled={disabled}
                            aria-label={`${isActive ? 'Change' : 'Sort'} ${column.title}`}
                            title={isActive ? `Sorted ${activeSort?.direction === 'asc' ? 'ascending' : 'descending'}` : `Sort ${column.title}`}
                          >
                            {isActive ? (
                              activeSort?.direction === 'asc'
                                ? <ChevronUp width={16} height={16} />
                                : <ChevronDown width={16} height={16} />
                            ) : (
                              <ChevronsUpDown width={16} height={16} />
                            )}
                          </button>
                        ) : null}
                        {filterColumn.filterKinds.length && onApplyHighlights ? <button type="button" className={`asset-table-header-action asset-table-highlight-toggle${columnHighlights.length ? ' has-active-highlight' : ''}`} onClick={(event) => {
                          openMenuAnchorRef.current = event.currentTarget.closest<HTMLDivElement>('.asset-table-header-cell')
                          setOpenFilterColumnId(null)
                          setOpenHighlightColumnId((current) => current === column.id ? null : column.id)
                        }} disabled={disabled} aria-label={`${columnHighlights.length ? 'Edit' : 'Add'} highlight for ${column.title}`} title={`Highlight ${column.title}`}><Palette width={16} height={16} /></button> : null}
                        {filterColumn.filterKinds.length ? (
                          <button
                            type="button"
                            className={`asset-table-header-action asset-table-filter-toggle${activeFilter ? ' has-active-filter' : ''}`}
                            onClick={(event) => {
                              openMenuAnchorRef.current = event.currentTarget.closest<HTMLDivElement>('.asset-table-header-cell')
                              setOpenHighlightColumnId(null)
                              setOpenFilterColumnId((current) => current === column.id ? null : column.id)
                            }}
                            disabled={disabled}
                            aria-label={`${activeFilter ? 'Edit' : 'Add'} filter for ${column.title}`}
                            title={activeFilter ? formatFilterSummary(activeFilter, columns) : `Filter ${column.title}`}
                          >
                            <Funnel width={16} height={16} />
                          </button>
                        ) : null}
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="asset-table-column-resize-handle"
                    aria-label={`Resize ${column.title} column`}
                    title={`Resize ${column.title} column`}
                    onPointerDown={(event) => handleColumnResizeStart(event, column.id)}
                  />
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {table.rows.length ? table.rows.map((row, index) => (
            <tr key={index}>
              {table.columns.map((column) => (
                <td key={column.id} style={cellHighlightStyle(table, index, column.id)}>{renderCellValue(row[column.id])}</td>
              ))}
            </tr>
          )) : (
            <tr>
              <td colSpan={Math.max(table.columns.length, 1)} className="asset-table-empty-cell">No rows on this page.</td>
            </tr>
          )}
        </tbody>
        </table>
      </div>
      {openMenuPosition && typeof document !== 'undefined' ? createPortal(
        <div ref={openMenuRef} className="asset-table-filter-menu-portal" style={openMenuPosition}>
          {openFilterColumnId ? (() => {
            const column = table.columns.find((entry) => entry.id === openFilterColumnId)
            if (!column) {
              return null
            }
            const filterColumn = columns.find((entry) => entry.id === column.id) ?? {
              id: column.id,
              title: column.title,
              dataType: column.data_type,
              filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
            }
            return <DataFrameHeaderFilterMenu column={filterColumn} activeFilter={activeFilters.find((entry) => entry.column === column.id) ?? null} disabled={disabled} onApplyFilter={onApplyFilter} onRemoveFilter={onRemoveFilter} onClose={() => setOpenFilterColumnId(null)} />
          })() : null}
          {openHighlightColumnId && onApplyHighlights ? (() => {
            const column = table.columns.find((entry) => entry.id === openHighlightColumnId)
            if (!column) {
              return null
            }
            const filterColumn = columns.find((entry) => entry.id === column.id) ?? {
              id: column.id,
              title: column.title,
              dataType: column.data_type,
              filterKinds: column.filter_kinds ?? filterKindsForDataType(column.data_type),
            }
            return <DataFrameHeaderHighlightMenu column={filterColumn} activeHighlights={activeHighlights.filter((entry) => entry.column === column.id)} disabled={disabled} onApplyHighlights={onApplyHighlights} onClose={() => setOpenHighlightColumnId(null)} />
          })() : null}
        </div>,
        document.body,
      ) : null}
    </div>
  )
}

function cellHighlightStyle(table: PreparedTablePayload, row: number, column: string) {
  const highlight = table.cell_highlights?.find((entry) => entry.row === row && entry.column === column)
  return highlight ? { backgroundColor: `color-mix(in srgb, ${highlight.color} 40%, transparent)` } : undefined
}

const DEFAULT_COLUMN_WIDTH = 100
const MIN_COLUMN_WIDTH = 48
const MAX_AUTOMATIC_COLUMN_WIDTH = 220
const APPROXIMATE_CHARACTER_WIDTH = 7
const CELL_HORIZONTAL_PADDING = 16

function resolveTableColumnWidths(
  table: PreparedTablePayload,
  overrides: Record<string, number>,
  availableWidth: number,
): Record<string, number> {
  if (!table.columns.length) {
    return {}
  }
  const preferredWidths = Object.fromEntries(table.columns.map((column) => {
    const headerLength = Math.max(column.title.length, column.data_type.length)
    const contentLength = table.rows.reduce(
      (longest, row) => Math.max(longest, formatCellValue(row[column.id]).length),
      0,
    )
    return [
      column.id,
      Math.min(
        MAX_AUTOMATIC_COLUMN_WIDTH,
        Math.max(DEFAULT_COLUMN_WIDTH, (Math.max(headerLength, contentLength) * APPROXIMATE_CHARACTER_WIDTH) + CELL_HORIZONTAL_PADDING),
      ),
    ]
  }))
  const widths = Object.fromEntries(table.columns.map((column) => [
    column.id,
    overrides[column.id] ?? preferredWidths[column.id] ?? DEFAULT_COLUMN_WIDTH,
  ]))
  const automaticColumns = table.columns.filter((column) => overrides[column.id] === undefined)
  const currentTotal = Object.values(widths).reduce((total, width) => total + width, 0)
  let remainingExtra = Math.max(0, Math.floor(availableWidth - currentTotal))
  for (const [index, column] of automaticColumns.entries()) {
    if (remainingExtra <= 0) {
      break
    }
    const columnsRemaining = automaticColumns.length - index
    const extra = Math.ceil(remainingExtra / columnsRemaining)
    widths[column.id] = (preferredWidths[column.id] ?? DEFAULT_COLUMN_WIDTH) + extra
    remainingExtra -= extra
  }
  return widths
}

function renderCellValue(value: unknown) {
  const text = formatCellValue(value)
  if (isHttpUrl(text)) {
    return <a className="asset-table-cell-link" href={text} target="_blank" rel="noreferrer">{text}</a>
  }
  return text
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.href === value
  } catch {
    return false
  }
}

function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return JSON.stringify(value)
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat('en-US').format(value).replace(/,/g, ' ')
}
