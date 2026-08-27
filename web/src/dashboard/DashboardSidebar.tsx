import { useEffect, useRef, useState, type DragEvent, type MouseEvent } from 'react'

import type { AssetRecord, DashboardPanelRecord } from '../lib/types'

type DropIndicator = {
  panelId: string
  position: 'before' | 'after'
}

export function DashboardSidebar({
  panels,
  assetsByLogicalId,
  panelHref,
  onPanelNavigate,
  onPanelsChange,
}: {
  panels: DashboardPanelRecord[]
  assetsByLogicalId: Map<string, AssetRecord>
  panelHref: (panelId: string) => string
  onPanelNavigate?: (event: MouseEvent<HTMLAnchorElement>, panelId: string) => void
  onPanelsChange: (updater: (panels: DashboardPanelRecord[]) => DashboardPanelRecord[]) => void
}) {
  const orderedPanels = normalizeDashboardPanels(panels)
  const visibleCount = orderedPanels.filter((panel) => panel.visible).length
  const [draggedPanelId, setDraggedPanelId] = useState<string | null>(null)
  const [hiddenDragSourcePanelId, setHiddenDragSourcePanelId] = useState<string | null>(null)
  const [dropIndicator, setDropIndicator] = useState<DropIndicator | null>(null)
  const draggedPanelIdRef = useRef<string | null>(null)
  const dragSourceHideFrameRef = useRef<number | null>(null)
  const dragCleanupFrameRef = useRef<number | null>(null)

  useEffect(() => () => {
    if (dragSourceHideFrameRef.current !== null) window.cancelAnimationFrame(dragSourceHideFrameRef.current)
    if (dragCleanupFrameRef.current !== null) window.cancelAnimationFrame(dragCleanupFrameRef.current)
  }, [])

  function resetDragState() {
    if (dragSourceHideFrameRef.current !== null) {
      window.cancelAnimationFrame(dragSourceHideFrameRef.current)
      dragSourceHideFrameRef.current = null
    }
    if (dragCleanupFrameRef.current !== null) {
      window.cancelAnimationFrame(dragCleanupFrameRef.current)
      dragCleanupFrameRef.current = null
    }
    draggedPanelIdRef.current = null
    setDraggedPanelId(null)
    setHiddenDragSourcePanelId(null)
    setDropIndicator(null)
  }

  function handlePanelDrop(targetPanelId: string, position: 'before' | 'after', droppedPanelId: string | null) {
    const activeDraggedPanelId = droppedPanelId ?? draggedPanelIdRef.current ?? draggedPanelId
    if (!activeDraggedPanelId || activeDraggedPanelId === targetPanelId) {
      resetDragState()
      return
    }
    onPanelsChange((current) => moveDashboardPanel(current, activeDraggedPanelId, targetPanelId, position))
    resetDragState()
  }

  function handlePanelDragOver(event: DragEvent<HTMLDivElement>, panelId: string) {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    const bounds = event.currentTarget.getBoundingClientRect()
    const position = event.clientY < bounds.top + bounds.height / 2 ? 'before' : 'after'
    setDropIndicator((current) => current?.panelId === panelId && current.position === position ? current : { panelId, position })
  }

  return (
    <aside className="panel dashboard-sidebar">
      <div className="dashboard-sidebar-head"><div><strong>Panels</strong><p>{visibleCount} visible of {orderedPanels.length}</p></div></div>
      {orderedPanels.length ? (
        <div
          className={`dashboard-sidebar-list${draggedPanelId ? ' is-reordering' : ''}`}
          onDragOver={(event) => {
            if (!draggedPanelIdRef.current && !draggedPanelId) return
            event.preventDefault()
            event.dataTransfer.dropEffect = 'move'
          }}
          onDrop={(event) => {
            event.preventDefault()
            event.stopPropagation()
            if (!dropIndicator) {
              resetDragState()
              return
            }
            handlePanelDrop(dropIndicator.panelId, dropIndicator.position, event.dataTransfer.getData('application/x-bulletjournal-dashboard-panel') || event.dataTransfer.getData('text/plain') || null)
          }}
        >
          {orderedPanels.map((panel) => {
            const asset = assetsByLogicalId.get(`${panel.node_id}/${panel.asset_name}`) ?? null
            const assetState = asset?.state ?? 'pending'
            const label = asset?.title || panel.asset_name
            return (
              <div
                key={panel.panel_id}
                className={['dashboard-sidebar-row', panel.visible ? '' : 'is-hidden', hiddenDragSourcePanelId === panel.panel_id ? 'is-drag-source-hidden' : '', draggedPanelId && dropIndicator?.panelId === panel.panel_id && dropIndicator.position === 'before' ? 'has-gap-before' : '', draggedPanelId && dropIndicator?.panelId === panel.panel_id && dropIndicator.position === 'after' ? 'has-gap-after' : ''].filter(Boolean).join(' ')}
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.effectAllowed = 'move'
                  event.dataTransfer.setData('application/x-bulletjournal-dashboard-panel', panel.panel_id)
                  event.dataTransfer.setData('text/plain', panel.panel_id)
                  draggedPanelIdRef.current = panel.panel_id
                  setDraggedPanelId(panel.panel_id)
                  setHiddenDragSourcePanelId(null)
                  setDropIndicator(null)
                  dragSourceHideFrameRef.current = window.requestAnimationFrame(() => setHiddenDragSourcePanelId(panel.panel_id))
                }}
                onDragEnd={() => { dragCleanupFrameRef.current = window.requestAnimationFrame(resetDragState) }}
                onDragOver={(event) => handlePanelDragOver(event, panel.panel_id)}
                onDrop={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  handlePanelDrop(panel.panel_id, dropIndicator?.panelId === panel.panel_id ? dropIndicator.position : 'before', event.dataTransfer.getData('application/x-bulletjournal-dashboard-panel') || event.dataTransfer.getData('text/plain') || null)
                }}
                aria-label={`Reorder ${label}`}
              >
                <div className="dashboard-sidebar-handle" aria-hidden="true" title="Drag to reorder"><DragHandleIcon /></div>
                <span className={`dashboard-sidebar-state-bubble is-${assetState}`} aria-hidden="true" />
                <a className="dashboard-sidebar-link" href={panelHref(panel.panel_id)} onClick={(event) => onPanelNavigate?.(event, panel.panel_id)}><span className="dashboard-sidebar-link-label">{label}</span></a>
                <button type="button" className="dashboard-sidebar-visibility" onClick={(event) => {
                  onPanelsChange((current) => current.map((entry) => entry.panel_id === panel.panel_id ? { ...entry, visible: !entry.visible } : entry))
                  event.currentTarget.blur()
                }} aria-label={panel.visible ? `Hide ${label}` : `Show ${label}`} title={panel.visible ? 'Hide panel' : 'Show panel'}><EyeOffIcon /></button>
              </div>
            )
          })}
        </div>
      ) : <p className="dashboard-sidebar-empty">This dashboard does not define any panels yet.</p>}
    </aside>
  )
}

export function normalizeDashboardPanels(panels: DashboardPanelRecord[]): DashboardPanelRecord[] {
  return panels.slice().sort((left, right) => left.position - right.position || left.panel_id.localeCompare(right.panel_id)).map((panel, index) => ({ ...panel, position: index }))
}

function moveDashboardPanel(panels: DashboardPanelRecord[], draggedPanelId: string, targetPanelId: string, position: 'before' | 'after'): DashboardPanelRecord[] {
  const ordered = normalizeDashboardPanels(panels)
  const draggedIndex = ordered.findIndex((panel) => panel.panel_id === draggedPanelId)
  const targetIndex = ordered.findIndex((panel) => panel.panel_id === targetPanelId)
  if (draggedIndex === -1 || targetIndex === -1 || draggedIndex === targetIndex) return ordered
  const next = ordered.slice()
  const [draggedPanel] = next.splice(draggedIndex, 1)
  const adjustedTargetIndex = draggedIndex < targetIndex ? targetIndex - 1 : targetIndex
  next.splice(position === 'after' ? adjustedTargetIndex + 1 : adjustedTargetIndex, 0, draggedPanel)
  return next.map((panel, index) => ({ ...panel, position: index }))
}

function DragHandleIcon() {
  return <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="5" cy="3.5" r="1.1" fill="currentColor" /><circle cx="11" cy="3.5" r="1.1" fill="currentColor" /><circle cx="5" cy="8" r="1.1" fill="currentColor" /><circle cx="11" cy="8" r="1.1" fill="currentColor" /><circle cx="5" cy="12.5" r="1.1" fill="currentColor" /><circle cx="11" cy="12.5" r="1.1" fill="currentColor" /></svg>
}

function EyeOffIcon() {
  return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2.6 10c1.7-3 4.5-4.8 7.4-4.8 2 0 3.9.8 5.4 2.2 1 .9 1.8 1.9 2.6 3-.8 1.1-1.6 2.1-2.6 3-1.5 1.4-3.4 2.2-5.4 2.2-2.9 0-5.7-1.8-7.4-4.8Z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /><circle cx="10" cy="10" r="2.5" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M3 17 17 3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
}
