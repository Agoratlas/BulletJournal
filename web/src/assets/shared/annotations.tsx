import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { NotePen, Star } from '../../components/Icons'
import { FAVORITES_DASHBOARD_ID, listNodeAssets, patchAssetAnnotation } from '../../lib/api'
import type { AssetRecord } from '../../lib/types'

function useAssetAnnotation(asset: AssetRecord) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['node-assets', asset.node_id],
    queryFn: () => listNodeAssets(asset.node_id),
    enabled: asset.annotation !== undefined,
    refetchOnMount: false,
    select: (assets) => assets.find((entry) => entry.asset_name === asset.asset_name)?.annotation,
  })
  const mutation = useMutation({
    mutationFn: (patch: { is_favorite?: boolean; note?: string }) => patchAssetAnnotation(asset.node_id, asset.asset_name, patch),
    onSuccess: (updated, patch) => {
      if (!updated.annotation) return
      const saved = updated.annotation
      queryClient.setQueryData<AssetRecord[]>(['node-assets', asset.node_id], (assets) => assets?.map((entry) => (
        entry.asset_name === asset.asset_name ? {
          ...entry,
          annotation: {
            is_favorite: patch.is_favorite !== undefined ? saved.is_favorite : (entry.annotation?.is_favorite ?? false),
            note: patch.note !== undefined ? saved.note : (entry.annotation?.note ?? ''),
          },
        } : entry
      )))
      if (patch.is_favorite !== undefined) {
        void queryClient.invalidateQueries({ queryKey: ['dashboard', FAVORITES_DASHBOARD_ID], exact: true })
      }
    },
  })
  return { annotation: query.data ?? asset.annotation, mutation }
}

export function openAssetPanelNote(panelId: string): boolean {
  const panel = document.querySelector<HTMLElement>(`[data-asset-panel-id="${CSS.escape(panelId)}"]`)
  const button = panel?.querySelector<HTMLButtonElement>('[data-asset-note-button]')
  if (!panel || !button || button.disabled) return false
  if (button.getAttribute('aria-expanded') !== 'true') button.click()
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' })
  return true
}

export function AssetFavoriteButton({ asset, sidebar = false }: { asset: AssetRecord; sidebar?: boolean }) {
  const { annotation, mutation } = useAssetAnnotation(asset)
  if (!annotation) return null
  const label = annotation.is_favorite ? 'Remove from favorites' : 'Mark as favorite'
  return (
    <span className="asset-annotation-action">
      <button
        type="button"
        className={`${sidebar ? 'dashboard-sidebar-action dashboard-sidebar-favorite' : 'asset-panel-action-button asset-favorite-button'}${annotation.is_favorite ? ' is-favorite is-active' : ''}`}
        aria-label={label}
        aria-pressed={annotation.is_favorite}
        title={label}
        disabled={mutation.isPending}
        onClick={() => mutation.mutate({ is_favorite: !annotation.is_favorite })}
      ><Star width={14} height={14} fill={annotation.is_favorite ? 'currentColor' : 'none'} /></button>
      {mutation.error ? <span className="asset-panel-action-popover error" role="alert">{mutation.error.message}</span> : null}
    </span>
  )
}

export function AssetNoteButton({ asset, onOpen }: { asset: AssetRecord; onOpen: () => void }) {
  const { annotation, mutation } = useAssetAnnotation(asset)
  const [isOpen, setIsOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const draftRef = useRef(draft)
  const openRef = useRef(isOpen)
  draftRef.current = draft
  openRef.current = isOpen

  function save() {
    if (!openRef.current) return
    openRef.current = false
    setIsOpen(false)
    if (draftRef.current !== annotation?.note) mutation.mutate({ note: draftRef.current })
  }

  useEffect(() => {
    if (!isOpen) return
    textareaRef.current?.focus({ preventScroll: true })
    const outsideClick = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) save()
    }
    document.addEventListener('pointerdown', outsideClick)
    return () => document.removeEventListener('pointerdown', outsideClick)
  }, [isOpen, annotation?.note])

  if (!annotation) return null
  return (
    <div ref={containerRef} className="asset-panel-action-menu">
      <button
        type="button"
        data-asset-note-button
        className={`asset-panel-action-button asset-note-button${annotation.note ? ' has-note' : ''}`}
        aria-label={annotation.note ? 'Edit asset note' : 'Add asset note'}
        aria-expanded={isOpen}
        title={annotation.note ? 'Edit asset note' : 'Add asset note'}
        disabled={mutation.isPending}
        onClick={() => {
          if (isOpen) save()
          else {
            mutation.reset()
            setDraft(annotation.note)
            openRef.current = true
            setIsOpen(true)
            onOpen()
          }
        }}
      ><NotePen width={14} height={14} /></button>
      {isOpen ? (
        <form className="asset-panel-action-popover asset-note-popover" onSubmit={(event) => { event.preventDefault(); save() }}>
          <label className="asset-note-field">
            <span>Asset note</span>
            <textarea ref={textareaRef} rows={6} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => {
              if (event.key === 'Escape') { event.preventDefault(); setIsOpen(false) }
            }} />
          </label>
          <div className="asset-note-actions">
            <button type="button" className="secondary" onClick={() => setIsOpen(false)}>Cancel</button>
            <button type="submit">Save</button>
          </div>
        </form>
      ) : null}
      {mutation.error ? (
        <div className="asset-panel-action-popover error" role="alert">
          <p>{mutation.error.message}</p>
          <button type="button" className="secondary" onClick={() => { mutation.reset(); setIsOpen(true) }}>Edit note</button>
        </div>
      ) : null}
    </div>
  )
}
