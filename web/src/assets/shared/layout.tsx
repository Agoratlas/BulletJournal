import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'

import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Cog } from '../../components/Icons'
import type { AssetFilter, AssetSort, AssetRecord, PreparedTablePayload } from '../../lib/types'
import {
  clampPanelHeight,
  modifierFieldLabelClassName,
  optionalIntegerFromInput,
  optionalNumberFromInput,
  valuesEqual,
} from './modifiers'
import { PAGE_SIZE_OPTIONS } from './types'
import type { AssetPanelFrameVariant, AssetPanelInfo, ChartAxisOverrides, ChartTitleOverrides, DatavizAxisScale, ModifierColumn } from './types'
import { PreparedTable, formatCount } from './table'

export function AssetPanelFrame({
  asset,
  panelInfo,
  settingsTitle,
  settingsBody,
  settingsActive = false,
  headerCenter,
  sectionId,
  frameVariant = 'card',
  children,
}: {
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  settingsTitle?: string
  settingsBody?: ReactNode
  settingsActive?: boolean
  headerCenter?: ReactNode
  sectionId?: string
  frameVariant?: AssetPanelFrameVariant
  children: ReactNode
}) {
  return (
    <section id={sectionId} className={frameVariant === 'inline' ? 'asset-panel-frame-inline' : 'panel asset-panel-card'}>
      <div className={`asset-panel-header${headerCenter ? ' has-center-content' : ''}`}>
        <div className="asset-panel-heading">
          <div className="asset-panel-title-row">
            <span className={`asset-state-bubble is-${asset.state}`} aria-hidden="true" />
            <h2>{asset.title || asset.asset_name}</h2>
          </div>
          {asset.description ? <p className="asset-panel-description">{asset.description}</p> : null}
        </div>
        {headerCenter ? <div className="asset-panel-header-center">{headerCenter}</div> : null}
        <AssetPanelHeaderActions panelInfo={panelInfo} settingsTitle={settingsTitle} settingsBody={settingsBody} settingsActive={settingsActive} />
      </div>
      {children}
    </section>
  )
}

function AssetPanelHeaderActions({
  panelInfo,
  settingsTitle,
  settingsBody,
  settingsActive,
}: {
  panelInfo: AssetPanelInfo
  settingsTitle?: string
  settingsBody?: ReactNode
  settingsActive: boolean
}) {
  const [openMenu, setOpenMenu] = useState<'info' | 'settings' | null>(null)
  const actionsRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (openMenu === null || typeof document === 'undefined') {
      return
    }
    const handlePointerDown = (event: PointerEvent) => {
      if (actionsRef.current?.contains(event.target as Node)) {
        return
      }
      setOpenMenu(null)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [openMenu])

  return (
    <div ref={actionsRef} className="asset-panel-header-actions">
      <details className="asset-panel-action-menu" open={openMenu === 'info'}>
        <summary
          className="asset-panel-action-button"
          aria-label="Show panel info"
          title="Show panel info"
          onClick={(event) => {
            event.preventDefault()
            setOpenMenu((current) => current === 'info' ? null : 'info')
          }}
        >
          <em aria-hidden="true">i</em>
        </summary>
        <div className="asset-panel-action-popover asset-panel-info-popover">
          <dl>
            <div>
              <dt>Panel id</dt>
              <dd><code>{panelInfo.panelId}</code></dd>
            </div>
            <div>
              <dt>Asset</dt>
              <dd>{panelInfo.assetTitle || panelInfo.assetName}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{panelInfo.createdLabel}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{panelInfo.runtimeType}</dd>
            </div>
          </dl>
        </div>
      </details>

      {settingsBody ? (
        <details className="asset-panel-action-menu" open={openMenu === 'settings'}>
          <summary
            className={`asset-panel-action-button${settingsActive ? ' is-overridden' : ''}`}
            aria-label={settingsTitle}
            title={settingsTitle}
            onClick={(event) => {
              event.preventDefault()
              setOpenMenu((current) => current === 'settings' ? null : 'settings')
            }}
          >
            <Cog width={14} height={14} />
          </summary>
          <div className="asset-panel-action-popover asset-panel-settings-popover">
            <div className="asset-dataviz-settings-panel">
              {settingsTitle ? <div className="asset-dataviz-settings-heading">{settingsTitle}</div> : null}
              {settingsBody}
            </div>
          </div>
        </details>
      ) : null}
    </div>
  )
}

export function ResizableDatavizContent({
  height,
  onHeightChange,
  children,
}: {
  height: number
  onHeightChange?: (height: number) => void
  children: (height: number) => ReactNode
}) {
  const [draftHeight, setDraftHeight] = useState(height)
  const draftHeightRef = useRef(height)
  const dragCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    draftHeightRef.current = height
    setDraftHeight(height)
  }, [height])

  useEffect(() => () => {
    dragCleanupRef.current?.()
  }, [])

  function handlePointerDown(event: ReactPointerEvent<HTMLButtonElement>) {
    if (!onHeightChange || event.button !== 0) {
      return
    }
    event.preventDefault()
    dragCleanupRef.current?.()
    const startY = event.clientY
    const startHeight = draftHeightRef.current
    const handleWindowPointerMove = (moveEvent: PointerEvent) => {
      const nextHeight = clampPanelHeight(startHeight + moveEvent.clientY - startY)
      draftHeightRef.current = nextHeight
      setDraftHeight(nextHeight)
    }
    const handleWindowPointerUp = () => {
      const nextHeight = draftHeightRef.current
      dragCleanupRef.current?.()
      if (nextHeight !== startHeight) {
        onHeightChange(nextHeight)
      }
    }
    const cleanup = () => {
      window.removeEventListener('pointermove', handleWindowPointerMove)
      window.removeEventListener('pointerup', handleWindowPointerUp)
      dragCleanupRef.current = null
    }
    dragCleanupRef.current = cleanup
    window.addEventListener('pointermove', handleWindowPointerMove)
    window.addEventListener('pointerup', handleWindowPointerUp)
  }

  return (
    <>
      {children(draftHeight)}
      {onHeightChange ? (
        <button
          type="button"
          className="asset-dataviz-resize-handle"
          aria-label="Resize visualization height"
          title="Drag to resize chart height"
          onPointerDown={handlePointerDown}
        >
          <span className="asset-dataviz-resize-grip" aria-hidden="true" />
        </button>
      ) : null}
    </>
  )
}

export function PanelSettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="asset-dataviz-settings-section">
      <h3>{title}</h3>
      <div className="asset-dataviz-settings-fields">{children}</div>
    </section>
  )
}

export function DeferredModifierInput({
  displayValue,
  isValid,
  fallbackValue,
  onValidChange,
  onCommit,
  inputMode,
  ariaLabel,
  placeholder,
}: {
  displayValue: string
  isValid: (value: string) => boolean
  fallbackValue: string
  onValidChange: (next: string) => void
  onCommit: (next: string) => void
  inputMode?: 'text' | 'decimal' | 'numeric'
  ariaLabel?: string
  placeholder?: string
}) {
  const [draftValue, setDraftValue] = useState(displayValue)
  const [isEditing, setIsEditing] = useState(false)

  useEffect(() => {
    if (!isEditing) {
      setDraftValue(displayValue)
    }
  }, [displayValue, isEditing])

  function commitValue(nextValue: string) {
    onCommit(isValid(nextValue) ? nextValue : fallbackValue)
  }

  return (
    <input
      value={isEditing ? draftValue : displayValue}
      inputMode={inputMode}
      aria-label={ariaLabel}
      placeholder={placeholder}
      onFocus={() => setIsEditing(true)}
      onChange={(event) => {
        const nextValue = event.target.value
        setIsEditing(true)
        setDraftValue(nextValue)
        if (isValid(nextValue)) {
          onValidChange(nextValue)
        }
      }}
      onBlur={() => {
        setIsEditing(false)
        commitValue(draftValue)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault()
          commitValue(draftValue)
          ;(event.currentTarget as HTMLInputElement).blur()
        }
        if (event.key === 'Escape') {
          event.preventDefault()
          setDraftValue(displayValue)
          ;(event.currentTarget as HTMLInputElement).blur()
        }
      }}
    />
  )
}

export function AxisOverridesSection({
  title,
  overrides,
  defaultOverrides,
  defaultLabel,
  onChange,
  allowLogScale = true,
}: {
  title: string
  overrides: ChartAxisOverrides
  defaultOverrides: ChartAxisOverrides
  defaultLabel: string
  onChange: (next: ChartAxisOverrides) => void
  allowLogScale?: boolean
}) {
  return (
    <PanelSettingsSection title={title}>
      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.labelSize, defaultOverrides.labelSize))}>Label size</span>
        <DeferredModifierInput
          displayValue={overrides.labelSize}
          inputMode="decimal"
          placeholder="Default"
          isValid={(value) => optionalNumberFromInput(value) !== undefined}
          fallbackValue={defaultOverrides.labelSize}
          onValidChange={(nextValue) => onChange({ ...overrides, labelSize: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, labelSize: nextValue })}
        />
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.label, defaultOverrides.label))}>Label</span>
        <DeferredModifierInput
          displayValue={overrides.label}
          isValid={(value) => value.trim() !== ''}
          fallbackValue={defaultOverrides.label}
          onValidChange={(nextValue) => onChange({ ...overrides, label: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, label: nextValue })}
        />
      </label>

      <label className="asset-dataviz-checkbox-field">
        <input
          type="checkbox"
          checked={overrides.hideLabel}
          onChange={(event) => onChange({ ...overrides, hideLabel: event.target.checked })}
        />
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.hideLabel, defaultOverrides.hideLabel))}>Hide label</span>
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.tickCount, defaultOverrides.tickCount))}>Tick count</span>
        <DeferredModifierInput
          displayValue={overrides.tickCount}
          inputMode="numeric"
          placeholder="Auto"
          isValid={(value) => optionalIntegerFromInput(value) !== undefined}
          fallbackValue={defaultOverrides.tickCount}
          onValidChange={(nextValue) => onChange({ ...overrides, tickCount: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, tickCount: nextValue })}
        />
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.tickSize, defaultOverrides.tickSize))}>Tick size</span>
        <DeferredModifierInput
          displayValue={overrides.tickSize}
          inputMode="decimal"
          placeholder="Auto"
          isValid={(value) => optionalNumberFromInput(value) !== undefined}
          fallbackValue={defaultOverrides.tickSize}
          onValidChange={(nextValue) => onChange({ ...overrides, tickSize: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, tickSize: nextValue })}
        />
      </label>

      <label className="asset-dataviz-checkbox-field">
        <input
          type="checkbox"
          checked={overrides.showGridLines}
          onChange={(event) => onChange({ ...overrides, showGridLines: event.target.checked })}
        />
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.showGridLines, defaultOverrides.showGridLines))}>Show grid lines</span>
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.scale, defaultOverrides.scale))}>Scale</span>
        <select
          value={overrides.scale}
          onChange={(event) => onChange({ ...overrides, scale: event.target.value as DatavizAxisScale })}
        >
          <option value="lin">Lin</option>
          {allowLogScale ? <option value="log">Log</option> : null}
        </select>
      </label>
    </PanelSettingsSection>
  )
}

export function TitleOverridesSection({
  title = 'Title',
  overrides,
  defaultOverrides,
  defaultText,
  onChange,
}: {
  title?: string
  overrides: ChartTitleOverrides
  defaultOverrides: ChartTitleOverrides
  defaultText: string
  onChange: (next: ChartTitleOverrides) => void
}) {
  const resolvedText = overrides.text.trim() || defaultText
  return (
    <PanelSettingsSection title={title}>
      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.size, defaultOverrides.size))}>Size</span>
        <DeferredModifierInput
          displayValue={overrides.size}
          inputMode="decimal"
          placeholder="Default"
          isValid={(value) => optionalNumberFromInput(value) !== undefined}
          fallbackValue={defaultOverrides.size}
          onValidChange={(nextValue) => onChange({ ...overrides, size: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, size: nextValue })}
        />
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(resolvedText, defaultOverrides.text.trim() || defaultText))}>Text</span>
        <DeferredModifierInput
          displayValue={resolvedText}
          isValid={(value) => value.trim() !== ''}
          fallbackValue={defaultOverrides.text}
          onValidChange={(nextValue) => onChange({ ...overrides, text: nextValue })}
          onCommit={(nextValue) => onChange({ ...overrides, text: nextValue })}
        />
      </label>

      <label className="asset-dataviz-checkbox-field">
        <input
          type="checkbox"
          checked={overrides.hideTitle}
          onChange={(event) => onChange({ ...overrides, hideTitle: event.target.checked })}
        />
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.hideTitle, defaultOverrides.hideTitle))}>Hide title</span>
      </label>

      <label className="asset-dataviz-field">
        <span className={modifierFieldLabelClassName(!valuesEqual(overrides.position, defaultOverrides.position))}>Position</span>
        <select
          value={overrides.position}
          onChange={(event) => onChange({ ...overrides, position: event.target.value as 'top' | 'bottom' })}
        >
          <option value="top">Top</option>
          <option value="bottom">Bottom</option>
        </select>
      </label>
    </PanelSettingsSection>
  )
}

export function OverrideIncompatibleNotice({
  onReset,
}: {
  onReset?: () => void
}) {
  return (
    <div className="asset-panel-inline-notice error">
      <p>Saved panel overrides are no longer compatible with the current asset schema.</p>
      {onReset ? (
        <button type="button" className="secondary asset-inline-action" onClick={onReset}>
          Reset panel overrides
        </button>
      ) : null}
    </div>
  )
}

export function PrepareErrorsNotice({ errors }: { errors: Array<{ code: string; message: string }> }) {
  if (!errors.length) {
    return null
  }
  return (
    <div className="asset-panel-inline-notice">
      {errors.map((error) => (
        <p key={error.code}>{error.message}</p>
      ))}
    </div>
  )
}

export function LoadingPlaceholder({ message }: { message: string }) {
  return <div className="asset-panel-placeholder"><p>{message}</p></div>
}

export function ErrorPlaceholder({ message }: { message: string }) {
  return <div className="asset-panel-placeholder error"><p>{message}</p></div>
}

export function PreparedAssetTableSection({
  table,
  columns,
  activeSort,
  activeFilters,
  viewerMode = 'notebook',
  disabled,
  totalRows,
  displayedRows,
  columnCount,
  pageInput,
  pageCount,
  isRefreshing,
  canGoPrevious,
  canGoNext,
  hasTemporarySelection = false,
  onPageInputChange,
  onCommitPageInput,
  onResetPageInput,
  onPageSizeChange,
  onFirstPage,
  onPreviousPage,
  onNextPage,
  onLastPage,
  onToggleSort,
  onApplyFilter,
  onRemoveFilter,
  onClearFilters,
}: {
  table: PreparedTablePayload
  columns: ModifierColumn[]
  activeSort: AssetSort | null
  activeFilters: AssetFilter[]
  viewerMode?: 'notebook' | 'dashboard'
  disabled: boolean
  totalRows: number
  displayedRows: number
  columnCount: number
  pageInput: string
  pageCount: number
  isRefreshing: boolean
  canGoPrevious: boolean
  canGoNext: boolean
  hasTemporarySelection?: boolean
  onPageInputChange: (value: string) => void
  onCommitPageInput: () => void
  onResetPageInput: () => void
  onPageSizeChange: (size: number) => void
  onFirstPage: () => void
  onPreviousPage: () => void
  onNextPage: () => void
  onLastPage: () => void
  onToggleSort: (column: string) => void
  onApplyFilter: (filter: AssetFilter) => void
  onRemoveFilter: (columnId: string) => void
  onClearFilters?: () => void
}) {
  const hasActiveFilters = activeFilters.length > 0
  const hasActiveSort = activeSort !== null
  const showDisplayedRows = viewerMode === 'dashboard' && displayedRows !== totalRows
  const rowsLabel = viewerMode === 'dashboard' ? totalRows : displayedRows
  const showDashboardClearFilters = viewerMode === 'dashboard' && Boolean(onClearFilters) && (hasActiveFilters || hasActiveSort || hasTemporarySelection)

  return (
    <div className={`asset-dataframe-shell${isRefreshing ? ' is-refreshing' : ''}`}>
      <PreparedTable
        table={table}
        columns={columns}
        activeSort={activeSort}
        activeFilters={activeFilters}
        disabled={disabled}
        onToggleSort={onToggleSort}
        onApplyFilter={onApplyFilter}
        onRemoveFilter={onRemoveFilter}
      />
      <div className="asset-dataframe-toolbar">
        <div className="asset-dataframe-stats">
          <span>
            {formatCount(rowsLabel)} rows x {formatCount(columnCount)} cols
            {showDisplayedRows ? ` (${formatCount(displayedRows)} rows displayed)` : ''}
          </span>
          {showDashboardClearFilters ? (
            <button
              type="button"
              className="asset-dataframe-clear-filters"
              onClick={onClearFilters}
              disabled={disabled || isRefreshing}
            >
              Clear filters
            </button>
          ) : null}
        </div>
        <div className="asset-dataframe-controls">
          <select
            className="asset-page-size-select"
            value={table.page.size}
            onChange={(event) => onPageSizeChange(Number(event.target.value))}
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
              onClick={onFirstPage}
              disabled={!canGoPrevious || isRefreshing}
              aria-label="Go to first page"
              title="First page"
            >
              <ChevronsLeft width={16} height={16} />
            </button>
            <button
              type="button"
              className="secondary asset-page-nav-button"
              onClick={onPreviousPage}
              disabled={!canGoPrevious || isRefreshing}
              aria-label="Go to previous page"
              title="Previous page"
            >
              <ChevronLeft width={16} height={16} />
            </button>
            <input
              className="asset-page-input"
              value={pageInput}
              inputMode="numeric"
              aria-label="Page number"
              onChange={(event) => onPageInputChange(event.target.value.replace(/[^0-9]/g, ''))}
              onBlur={onCommitPageInput}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  onCommitPageInput()
                }
                if (event.key === 'Escape') {
                  event.preventDefault()
                  onResetPageInput()
                }
              }}
            />
            <span className="asset-page-count-label">/ {pageCount}</span>
            <button
              type="button"
              className="secondary asset-page-nav-button"
              onClick={onNextPage}
              disabled={!canGoNext || isRefreshing}
              aria-label="Go to next page"
              title="Next page"
            >
              <ChevronRight width={16} height={16} />
            </button>
            <button
              type="button"
              className="secondary asset-page-nav-button"
              onClick={onLastPage}
              disabled={!canGoNext || isRefreshing}
              aria-label="Go to last page"
              title="Last page"
            >
              <ChevronsRight width={16} height={16} />
            </button>
          </div>
        </div>
      </div>
      {isRefreshing ? (
        <div className="asset-dataframe-loading-overlay" aria-hidden="true">
          <div className="asset-dataframe-loading-spinner" />
        </div>
      ) : null}
    </div>
  )
}
