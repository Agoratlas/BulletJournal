import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { prepareAsset } from '../lib/api'
import { formatTimestamp } from '../lib/helpers'
import type { AssetRecord, AssetSort, PreparedTablePayload } from '../lib/types'
import { SimpleMarkdown } from '../components/SimpleMarkdown'

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const

export type PersistedAssetPanelState = {
  modifier_overrides: Record<string, unknown>
  override_schema_hash: string | null
}

export function AssetPanel({
  nodeId,
  asset,
  persistedState,
  onPersistedStateChange,
}: {
  nodeId: string
  asset: AssetRecord
  persistedState?: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
}) {
  const createdLabel = asset.created_at ? formatTimestamp(asset.created_at) : 'Not produced yet'
  const runtimeType = asset.asset_type ?? asset.declared_asset_type ?? 'unknown'

  return (
    <section className="panel asset-panel-card">
      <div className="asset-panel-header">
        <div className="asset-panel-heading">
          <div className="asset-panel-topline">
            <div className="asset-panel-title-row">
              <h2>{asset.title || asset.asset_name}</h2>
              <span className={`asset-state-badge is-${asset.state}`}>{asset.state}</span>
              <span className="asset-type-badge">{runtimeType}</span>
            </div>
            <div className="asset-panel-meta-row">
              <code>{nodeId}/{asset.asset_name}</code>
              <span>{createdLabel}</span>
            </div>
          </div>
          {asset.description ? <p className="asset-panel-description">{asset.description}</p> : null}
        </div>
      </div>

      {asset.current_asset_version_id === null ? (
        <div className="asset-panel-placeholder">
          <p>This asset is declared but has not been produced yet.</p>
        </div>
      ) : asset.asset_type === 'markdown' ? (
        <MarkdownAssetPanel asset={asset} />
      ) : asset.asset_type === 'dataframe' ? (
        <DataFrameAssetPanel
          nodeId={nodeId}
          asset={asset}
          persistedState={persistedState ?? null}
          onPersistedStateChange={onPersistedStateChange}
        />
      ) : (
        <div className="asset-panel-placeholder">
          <p>Asset type <code>{runtimeType}</code> is not supported by this viewer yet.</p>
        </div>
      )}
    </section>
  )
}

function MarkdownAssetPanel({ asset }: { asset: AssetRecord }) {
  const markdownText = typeof asset.definition?.markdown_text === 'string' ? asset.definition.markdown_text : null

  if (!markdownText) {
    return (
      <div className="asset-panel-placeholder">
        <p>This Markdown asset is missing its text payload.</p>
      </div>
    )
  }

  return (
    <div className="asset-markdown-panel">
      <SimpleMarkdown text={markdownText} />
    </div>
  )
}

function DataFrameAssetPanel({
  nodeId,
  asset,
  persistedState,
  onPersistedStateChange,
}: {
  nodeId: string
  asset: AssetRecord
  persistedState: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
}) {
  const initialTableState = useMemo(
    () => initialTableStateFromModifiers(asset.default_modifiers, persistedState?.modifier_overrides ?? {}),
    [asset.default_modifiers, persistedState?.modifier_overrides],
  )
  const [pageIndex, setPageIndex] = useState(initialTableState.page.index)
  const [pageSize, setPageSize] = useState(initialTableState.page.size)
  const [sort, setSort] = useState<AssetSort | null>(initialTableState.sort)
  const [pageInput, setPageInput] = useState(String(initialTableState.page.index + 1))

  useEffect(() => {
    setPageIndex(initialTableState.page.index)
    setPageSize(initialTableState.page.size)
    setSort(initialTableState.sort)
    setPageInput(String(initialTableState.page.index + 1))
  }, [asset.current_asset_version_id, initialTableState.page.index, initialTableState.page.size, initialTableState.sort?.column, initialTableState.sort?.direction])

  useEffect(() => {
    onPersistedStateChange?.({
      modifier_overrides: {
        page: { index: pageIndex, size: pageSize },
        sort: sort ? [sort] : [],
      },
      override_schema_hash: asset.override_schema_hash,
    })
  }, [asset.override_schema_hash, onPersistedStateChange, pageIndex, pageSize, sort])

  const prepareQuery = useQuery({
    queryKey: [
      'asset-prepare',
      nodeId,
      asset.asset_name,
      asset.current_asset_version_id,
      pageIndex,
      pageSize,
      sort?.column ?? null,
      sort?.direction ?? null,
    ],
    queryFn: () => prepareAsset(nodeId, asset.asset_name, {
      asset_version_id: asset.current_asset_version_id,
      modifier_overrides: {
        page: { index: pageIndex, size: pageSize },
        sort: sort ? [sort] : [],
      },
      transient_modifiers: {},
    }),
    enabled: asset.current_asset_version_id !== null,
    placeholderData: (previousData) => previousData,
    retry: false,
  })

  const response = prepareQuery.data ?? null
  const table = response?.payloads.table ?? null
  const resolvedPage = table?.page ?? { index: pageIndex, size: pageSize }
  const resolvedSort = table?.sort?.[0] ?? null
  const totalRows = table?.rows_total ?? (typeof asset.definition?.row_count === 'number' ? asset.definition.row_count : 0)
  const columnCount = table?.columns.length ?? (Array.isArray(asset.definition?.table_columns) ? asset.definition.table_columns.length : 0)
  const pageCount = Math.max(1, Math.ceil(totalRows / Math.max(resolvedPage.size, 1)))
  const canGoPrevious = resolvedPage.index > 0
  const canGoNext = resolvedPage.index + 1 < pageCount

  useEffect(() => {
    setPageInput(String(resolvedPage.index + 1))
  }, [resolvedPage.index])

  function commitPageInput() {
    const parsed = Number(pageInput.trim())
    if (!Number.isInteger(parsed)) {
      setPageInput(String(resolvedPage.index + 1))
      return
    }
    const clampedIndex = Math.min(Math.max(parsed - 1, 0), pageCount - 1)
    setPageIndex(clampedIndex)
    setPageInput(String(clampedIndex + 1))
  }

  return (
    <div className="asset-dataframe-panel">
      {response?.errors.length ? (
        <div className="asset-panel-inline-notice">
          {response.errors.map((error) => (
            <p key={error.code}>{error.message}</p>
          ))}
        </div>
      ) : null}

      {prepareQuery.isLoading && !table ? <div className="asset-panel-placeholder"><p>Preparing table view...</p></div> : null}

      {prepareQuery.isError ? (
        <div className="asset-panel-placeholder error">
          <p>{prepareQuery.error instanceof Error ? prepareQuery.error.message : 'Could not prepare the table view.'}</p>
        </div>
      ) : null}

      {table ? (
        <div className={`asset-dataframe-shell${prepareQuery.isFetching ? ' is-refreshing' : ''}`}>
          <PreparedTable
            table={table}
            activeSort={resolvedSort}
            onToggleSort={(column) => {
              setPageIndex(0)
              setSort((current) => {
                if (!current || current.column !== column) {
                  return { column, direction: 'asc' }
                }
                if (current.direction === 'asc') {
                  return { column, direction: 'desc' }
                }
                return null
              })
            }}
          />
          <div className="asset-dataframe-toolbar">
            <div className="asset-dataframe-stats">{formatCount(totalRows)} rows x {formatCount(columnCount)} cols</div>
            <div className="asset-dataframe-controls">
              <select
                className="asset-page-size-select"
                value={resolvedPage.size}
                onChange={(event) => {
                  setPageSize(Number(event.target.value))
                  setPageIndex(0)
                }}
                aria-label="Rows per page"
              >
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <option key={size} value={size}>{size} / page</option>
                ))}
              </select>
              <div className="asset-dataframe-pagination">
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(0)}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<<'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
                  disabled={!canGoPrevious || prepareQuery.isFetching}
                >
                  {'<'}
                </button>
                <input
                  className="asset-page-input"
                  value={pageInput}
                  inputMode="numeric"
                  aria-label="Page number"
                  onChange={(event) => setPageInput(event.target.value.replace(/[^0-9]/g, ''))}
                  onBlur={commitPageInput}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      commitPageInput()
                    }
                    if (event.key === 'Escape') {
                      event.preventDefault()
                      setPageInput(String(resolvedPage.index + 1))
                    }
                  }}
                />
                <span className="asset-page-count-label">/ {pageCount}</span>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex((current) => Math.min(pageCount - 1, current + 1))}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>'}
                </button>
                <button
                  type="button"
                  className="secondary asset-page-nav-button"
                  onClick={() => setPageIndex(pageCount - 1)}
                  disabled={!canGoNext || prepareQuery.isFetching}
                >
                  {'>>'}
                </button>
              </div>
            </div>
          </div>
          {prepareQuery.isFetching ? (
            <div className="asset-dataframe-loading-overlay" aria-hidden="true">
              <div className="asset-dataframe-loading-spinner" />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function initialTableStateFromModifiers(
  defaultModifiers: Record<string, unknown>,
  modifierOverrides: Record<string, unknown>,
): { page: { index: number; size: number }; sort: AssetSort | null } {
  const page = pageFromValue(modifierOverrides.page) ?? pageFromValue(defaultModifiers.page) ?? { index: 0, size: 25 }
  const sort = sortFromValue(modifierOverrides.sort) ?? sortFromValue(defaultModifiers.sort) ?? null
  return { page, sort }
}

function pageFromValue(value: unknown): { index: number; size: number } | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  const pageRecord = value as Record<string, unknown>
  const index = typeof pageRecord.index === 'number' && pageRecord.index >= 0 ? pageRecord.index : 0
  const size = typeof pageRecord.size === 'number' && PAGE_SIZE_OPTIONS.includes(pageRecord.size as 10 | 25 | 50 | 100)
    ? pageRecord.size
    : 25
  return { index, size }
}

function sortFromValue(value: unknown): AssetSort | null {
  if (!Array.isArray(value) || !value.length || !value[0] || typeof value[0] !== 'object') {
    return null
  }
  const record = value[0] as Record<string, unknown>
  if ((record.direction !== 'asc' && record.direction !== 'desc') || typeof record.column !== 'string' || !record.column) {
    return null
  }
  return { column: record.column, direction: record.direction }
}

function PreparedTable({
  table,
  activeSort,
  onToggleSort,
}: {
  table: PreparedTablePayload
  activeSort: AssetSort | null
  onToggleSort: (column: string) => void
}) {
  return (
    <div className="table-wrap asset-table-wrap">
      <table className="preview-table asset-table">
        <thead>
          <tr>
            {table.columns.map((column) => {
              const isActive = activeSort?.column === column.id
              const indicator = isActive ? (activeSort?.direction === 'asc' ? ' ↑' : ' ↓') : ''
              return (
                <th key={column.id}>
                  {column.sortable ? (
                    <button
                      type="button"
                      className={`asset-table-sort-button${isActive ? ' active' : ''}`}
                      onClick={() => onToggleSort(column.id)}
                    >
                      {column.title}
                      {indicator}
                    </button>
                  ) : column.title}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {table.rows.length ? table.rows.map((row, index) => (
            <tr key={index}>
              {table.columns.map((column) => (
                <td key={column.id}>{formatCellValue(row[column.id])}</td>
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
  )
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

function formatCount(value: number): string {
  return new Intl.NumberFormat('en-US').format(value).replace(/,/g, ' ')
}
