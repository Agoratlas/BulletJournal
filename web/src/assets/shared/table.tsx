import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

import { ChevronDown, ChevronUp, ChevronsUpDown, Funnel } from '../../components/Icons'
import type { AssetFilter, AssetFilterKind, AssetSort, PreparedTablePayload } from '../../lib/types'
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
  }, [activeFilter, column])

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

export function PreparedTable({
  table,
  columns,
  activeSort,
  activeFilters,
  disabled,
  onToggleSort,
  onApplyFilter,
  onRemoveFilter,
}: {
  table: PreparedTablePayload
  columns: ModifierColumn[]
  activeSort: AssetSort | null
  activeFilters: AssetFilter[]
  disabled: boolean
  onToggleSort: (column: string) => void
  onApplyFilter: (filter: AssetFilter) => void
  onRemoveFilter: (columnId: string) => void
}) {
  const [openFilterColumnId, setOpenFilterColumnId] = useState<string | null>(null)
  const [columnWidthOverrides, setColumnWidthOverrides] = useState<Record<string, number>>({})
  const [availableWidth, setAvailableWidth] = useState(0)
  const [overflow, setOverflow] = useState({ left: false, right: false })
  const openFilterCellRef = useRef<HTMLDivElement | null>(null)
  const tableWrapRef = useRef<HTMLDivElement | null>(null)
  const resizeCleanupRef = useRef<(() => void) | null>(null)
  const resolvedColumnWidths = useMemo(
    () => resolveTableColumnWidths(table, columnWidthOverrides, availableWidth),
    [availableWidth, columnWidthOverrides, table.columns, table.rows],
  )
  const tableWidth = Object.values(resolvedColumnWidths).reduce((total, width) => total + width, 0)

  useEffect(() => {
    if (!openFilterColumnId) {
      return
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (openFilterCellRef.current && !openFilterCellRef.current.contains(event.target as globalThis.Node)) {
        setOpenFilterColumnId(null)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenFilterColumnId(null)
      }
    }
    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [openFilterColumnId])

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
              const isFilterOpen = openFilterColumnId === column.id
              return (
                <th key={column.id}>
                  <div className="asset-table-header-cell" ref={isFilterOpen ? openFilterCellRef : undefined}>
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
                        {filterColumn.filterKinds.length ? (
                          <button
                            type="button"
                            className={`asset-table-header-action asset-table-filter-toggle${activeFilter ? ' has-active-filter' : ''}`}
                            onClick={() => setOpenFilterColumnId((current) => current === column.id ? null : column.id)}
                            disabled={disabled}
                            aria-label={`${activeFilter ? 'Edit' : 'Add'} filter for ${column.title}`}
                            title={activeFilter ? formatFilterSummary(activeFilter, columns) : `Filter ${column.title}`}
                          >
                            <Funnel width={16} height={16} />
                          </button>
                        ) : null}
                      </div>
                    </div>
                    {isFilterOpen && filterColumn.filterKinds.length ? (
                      <DataFrameHeaderFilterMenu
                        column={filterColumn}
                        activeFilter={activeFilter}
                        disabled={disabled}
                        onApplyFilter={onApplyFilter}
                        onRemoveFilter={onRemoveFilter}
                        onClose={() => setOpenFilterColumnId(null)}
                      />
                    ) : null}
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
                <td key={column.id}>{renderCellValue(row[column.id])}</td>
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
    </div>
  )
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
