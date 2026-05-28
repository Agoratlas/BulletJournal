import { useEffect, useMemo, useRef, useState } from 'react'

import { ChevronDown, ChevronLeft, ChevronRight } from '../../components/Icons'
import type { AssetRecord } from '../../lib/types'
import { AssetPanel } from '../AssetPanel'
import {
  AssetPanelFrame,
  PanelSettingsSection,
} from '../shared/layout'
import {
  buildModifierOverridesRecord,
  stableValueKey,
} from '../shared/modifiers'
import type {
  AssetPanelFrameVariant,
  AssetPanelInfo,
  AssetPanelPrepareTarget,
  PersistedAssetPanelState,
} from '../shared/types'

type CollectionDisplayMode = 'all' | 'single'

type CollectionChild = {
  name: string
  title: string
  description: string | null
  assetType: string
  definition: Record<string, unknown>
  modifierSchema: Array<Record<string, unknown>>
  defaultModifiers: Record<string, unknown>
  overrideSchemaHash: string | null
}

type CollectionState = {
  displayMode: CollectionDisplayMode
  selectedChildName: string | null
}

type CollectionChildPanelState = {
  modifier_overrides: Record<string, unknown>
  override_schema_hash: string | null
  panel_height: number | null
}

type CollectionAssetPanelProps = {
  asset: AssetRecord
  panelInfo: AssetPanelInfo
  viewerMode?: 'notebook' | 'dashboard'
  persistedState: PersistedAssetPanelState | null
  onPersistedStateChange?: (state: PersistedAssetPanelState) => void
  onReadyStateChange?: (ready: boolean) => void
  sectionId?: string
  frameVariant?: AssetPanelFrameVariant
}

export function CollectionAssetPanel({
  asset,
  panelInfo,
  viewerMode = 'notebook',
  persistedState,
  onPersistedStateChange,
  onReadyStateChange,
  sectionId,
  frameVariant,
}: CollectionAssetPanelProps) {
  const children = useMemo(() => collectionChildrenFromAsset(asset), [asset])
  const persistedOverrideKey = useMemo(
    () => stableValueKey(persistedState?.modifier_overrides ?? {}),
    [persistedState?.modifier_overrides],
  )
  const persistedChildPanels = useMemo(
    () => collectionChildPanelStatesFromValue((persistedState?.modifier_overrides as Record<string, unknown> | undefined)?.child_panels),
    [persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const initialState = useMemo(
    () => collectionStateFromModifiers(asset, children, persistedState?.modifier_overrides ?? {}),
    [asset, children, persistedOverrideKey, persistedState?.modifier_overrides],
  )
  const externalStateKey = useMemo(() => stableValueKey(initialState), [initialState])
  const [displayMode, setDisplayMode] = useState<CollectionDisplayMode>(initialState.displayMode)
  const [selectedChildName, setSelectedChildName] = useState<string | null>(initialState.selectedChildName)
  const [displayedChildName, setDisplayedChildName] = useState<string | null>(initialState.selectedChildName)
  const [readyChildNames, setReadyChildNames] = useState<Record<string, true>>({})
  const isApplyingPersistedStateRef = useRef(false)
  const lastAppliedExternalStateRef = useRef({
    assetVersionId: asset.current_asset_version_id,
    externalStateKey,
  })
  const childNamesKey = useMemo(() => children.map((child) => child.name).join('\u0000'), [children])
  const localStateKey = stableValueKey({ displayMode, selectedChildName })
  const activeChild = children.find((child) => child.name === selectedChildName) ?? children[0] ?? null
  const displayedChild = children.find((child) => child.name === displayedChildName) ?? activeChild
  const shouldPreloadActiveChild = displayMode === 'single'
    && activeChild !== null
    && displayedChild !== null
    && activeChild.name !== displayedChild.name
  const isActiveChildReady = activeChild !== null && readyChildNames[activeChild.name] === true
  const defaultState = collectionDefaultState(asset, children)
  const hasSettingsOverrides = Object.keys(
    buildModifierOverridesRecord(
      {
        display_mode: displayMode,
        selected_child: activeChild?.name ?? null,
      },
      {
        display_mode: defaultState.displayMode,
        selected_child: defaultState.selectedChildName,
      },
    ),
  ).length > 0

  useEffect(() => {
    const lastAppliedExternalState = lastAppliedExternalStateRef.current
    const externalStateChanged = lastAppliedExternalState.assetVersionId !== asset.current_asset_version_id
      || lastAppliedExternalState.externalStateKey !== externalStateKey
    if (!externalStateChanged) {
      return
    }
    lastAppliedExternalStateRef.current = {
      assetVersionId: asset.current_asset_version_id,
      externalStateKey,
    }
    if (localStateKey === externalStateKey) {
      return
    }
    isApplyingPersistedStateRef.current = true
    setDisplayMode(initialState.displayMode)
    setSelectedChildName(initialState.selectedChildName)
  }, [asset.current_asset_version_id, externalStateKey, initialState.displayMode, initialState.selectedChildName, localStateKey])

  useEffect(() => {
    if (localStateKey === externalStateKey) {
      isApplyingPersistedStateRef.current = false
    }
  }, [externalStateKey, localStateKey])

  useEffect(() => {
    setReadyChildNames({})
    setDisplayedChildName(initialState.selectedChildName)
  }, [asset.current_asset_version_id, childNamesKey])

  useEffect(() => {
    if (displayMode !== 'single') {
      setDisplayedChildName(activeChild?.name ?? null)
      return
    }
    if (!activeChild) {
      setDisplayedChildName(null)
      return
    }
    if (displayedChild?.name === activeChild.name) {
      return
    }
    if (isActiveChildReady || displayedChild === null) {
      setDisplayedChildName(activeChild.name)
    }
  }, [activeChild, displayMode, displayedChild, isActiveChildReady])

  useEffect(() => {
    const isReady = displayMode !== 'single'
      || !displayedChild
      || readyChildNames[displayedChild.name] === true
    onReadyStateChange?.(isReady)
  }, [displayMode, displayedChild, onReadyStateChange, readyChildNames])

  useEffect(() => {
    if (isApplyingPersistedStateRef.current) {
      return
    }
    const nextState = buildCollectionPersistedState(
      asset,
      defaultState,
      displayMode,
      activeChild?.name ?? null,
      persistedChildPanels,
    )
    if (
      persistedState
      && persistedState.override_schema_hash === nextState.override_schema_hash
      && stableValueKey(persistedState.modifier_overrides) === stableValueKey(nextState.modifier_overrides)
    ) {
      return
    }
    onPersistedStateChange?.(nextState)
  }, [
    activeChild?.name,
    asset,
    defaultState,
    displayMode,
    onPersistedStateChange,
    persistedChildPanels,
    persistedState,
  ])

  function persistChildPanels(nextChildPanels: Record<string, CollectionChildPanelState>) {
    const nextState = buildCollectionPersistedState(
      asset,
      defaultState,
      displayMode,
      activeChild?.name ?? null,
      nextChildPanels,
    )
    onPersistedStateChange?.(nextState)
  }

  function handleResetView() {
    setDisplayMode(defaultState.displayMode)
    setSelectedChildName(defaultState.selectedChildName)
    onPersistedStateChange?.(buildCollectionPersistedState(
      asset,
      defaultState,
      defaultState.displayMode,
      defaultState.selectedChildName,
      persistedChildPanels,
    ))
  }

  const settingsBody = children.length ? (
    <>
      <div className="asset-dataviz-settings-actions">
        <button
          type="button"
          className="secondary asset-dataviz-settings-reset"
          onClick={handleResetView}
          disabled={!hasSettingsOverrides}
        >
          Reset view
        </button>
      </div>
      <PanelSettingsSection title="Collection view">
        <label className="asset-dataviz-field">
          <span className="asset-modifier-label">Display mode</span>
          <select
            value={displayMode}
            onChange={(event) => setDisplayMode(event.target.value === 'single' ? 'single' : 'all')}
          >
            <option value="all">All children</option>
            <option value="single">Single child</option>
          </select>
        </label>
        {children.length > 1 ? (
          <label className="asset-dataviz-field">
            <span className="asset-modifier-label">Selected child</span>
            <select
              value={activeChild?.name ?? ''}
              onChange={(event) => setSelectedChildName(event.target.value || (children[0]?.name ?? null))}
            >
              {children.map((child) => (
                <option key={child.name} value={child.name}>{child.title}</option>
              ))}
            </select>
          </label>
        ) : null}
      </PanelSettingsSection>
    </>
  ) : undefined

  if (!children.length) {
    return (
      <AssetPanelFrame asset={asset} panelInfo={panelInfo} sectionId={sectionId} frameVariant={frameVariant}>
        <div className="asset-panel-placeholder">
          <p>This collection asset does not include any child definitions.</p>
        </div>
      </AssetPanelFrame>
    )
  }

  const renderedChildren = displayMode === 'single'
    ? [
        ...(displayedChild ? [{ child: displayedChild, isPreloading: false }] : []),
        ...(shouldPreloadActiveChild && activeChild ? [{ child: activeChild, isPreloading: true }] : []),
      ]
    : children.map((child) => ({ child, isPreloading: false }))
  const headerCenter = displayMode === 'single' && activeChild && children.length > 1 ? (
    <div className="asset-collection-header-control" role="group" aria-label="Collection child navigation">
      <button
        type="button"
        className="asset-collection-stepper"
        onClick={() => setSelectedChildName(previousCollectionChild(children, activeChild.name)?.name ?? activeChild.name)}
        disabled={children[0]?.name === activeChild.name}
        aria-label="Show previous child"
        title="Previous child"
      >
        <ChevronLeft width={16} height={16} />
      </button>
      <div className="asset-collection-header-select-shell">
        <select
          value={activeChild.name}
          onChange={(event) => setSelectedChildName(event.target.value || activeChild.name)}
          aria-label="Select collection child"
          title="Select child"
        >
          {children.map((child) => (
            <option key={child.name} value={child.name}>{child.title}</option>
          ))}
        </select>
        <ChevronDown className="asset-collection-header-select-icon" width={14} height={14} />
      </div>
      <button
        type="button"
        className="asset-collection-stepper"
        onClick={() => setSelectedChildName(nextCollectionChild(children, activeChild.name)?.name ?? activeChild.name)}
        disabled={children[children.length - 1]?.name === activeChild.name}
        aria-label="Show next child"
        title="Next child"
      >
        <ChevronRight width={16} height={16} />
      </button>
    </div>
  ) : undefined

  function handleChildReadyStateChange(childName: string, ready: boolean) {
    if (!ready) {
      return
    }
    setReadyChildNames((current) => current[childName] ? current : { ...current, [childName]: true })
  }

  return (
      <AssetPanelFrame
        asset={asset}
        panelInfo={panelInfo}
        settingsTitle="Collection view"
        settingsBody={settingsBody}
        settingsActive={hasSettingsOverrides}
        headerCenter={headerCenter}
        sectionId={sectionId}
        frameVariant={frameVariant}
      >
      <div className="asset-collection-panel">
        <div className={`asset-collection-list${displayMode === 'single' ? ' is-single' : ''}`}>
          {renderedChildren.map(({ child, isPreloading }) => {
            const childPanelState = persistedChildPanels[child.name] ?? null
            return (
              <div
                key={isPreloading ? `${child.name}/preload` : child.name}
                className={`asset-collection-item${isPreloading ? ' is-preloading' : ''}`}
                aria-hidden={isPreloading}
              >
                <AssetPanel
                  panelId={`${panelInfo.panelId}/${child.name}`}
                  nodeId={asset.node_id}
                  asset={collectionChildAssetRecord(asset, child)}
                  prepareTarget={collectionChildPrepareTarget(asset, child)}
                  viewerMode={viewerMode}
                  frameVariant="inline"
                  onReadyStateChange={(ready) => handleChildReadyStateChange(child.name, ready)}
                  persistedState={childPanelState ? {
                    modifier_overrides: childPanelState.modifier_overrides,
                    override_schema_hash: childPanelState.override_schema_hash,
                  } : null}
                  onPersistedStateChange={(state) => {
                    persistChildPanels({
                      ...persistedChildPanels,
                      [child.name]: normalizeCollectionChildPanelState({
                        ...(childPanelState ?? {}),
                        modifier_overrides: state.modifier_overrides,
                        override_schema_hash: state.override_schema_hash,
                      }),
                    })
                  }}
                  panelHeight={childPanelState?.panel_height ?? null}
                  onPanelHeightChange={(height) => {
                    persistChildPanels({
                      ...persistedChildPanels,
                      [child.name]: normalizeCollectionChildPanelState({
                        ...childPanelState,
                        modifier_overrides: childPanelState?.modifier_overrides ?? {},
                        override_schema_hash: childPanelState?.override_schema_hash ?? child.overrideSchemaHash,
                        panel_height: height,
                      }),
                    })
                  }}
                />
              </div>
            )
          })}
        </div>
      </div>
    </AssetPanelFrame>
  )
}

function collectionChildrenFromAsset(asset: AssetRecord): CollectionChild[] {
  const definitions = Array.isArray(asset.definition?.children) ? asset.definition.children : []
  return definitions.flatMap((definition, index) => {
    if (!definition || typeof definition !== 'object') {
      return []
    }
    const record = definition as Record<string, unknown>
    const name = typeof record.name === 'string' && record.name.trim()
      ? record.name.trim()
      : `asset_${index + 1}`
    const assetType = typeof record.asset_type === 'string' && record.asset_type.trim()
      ? record.asset_type.trim()
      : 'unknown'
    return [{
      name,
      title: typeof record.title === 'string' && record.title.trim() ? record.title.trim() : `Asset ${index + 1}`,
      description: typeof record.description === 'string' && record.description.trim() ? record.description.trim() : null,
      assetType,
      definition: record,
      modifierSchema: Array.isArray(record.modifier_schema)
        ? record.modifier_schema.filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === 'object')
        : [],
      defaultModifiers: isRecord(record.default_modifiers)
        ? record.default_modifiers
        : isRecord(record.modifier_defaults)
          ? record.modifier_defaults
          : {},
      overrideSchemaHash: typeof record.override_schema_hash === 'string' ? record.override_schema_hash : null,
    }]
  })
}

function collectionDefaultState(asset: AssetRecord, children: CollectionChild[]): CollectionState {
  return {
    displayMode: asset.definition?.display_mode_default === 'single' ? 'single' : 'all',
    selectedChildName: children[0]?.name ?? null,
  }
}

function collectionStateFromModifiers(
  asset: AssetRecord,
  children: CollectionChild[],
  modifierOverrides: Record<string, unknown>,
): CollectionState {
  const defaults = collectionDefaultState(asset, children)
  const selectedChildName = typeof modifierOverrides.selected_child === 'string'
    && children.some((child) => child.name === modifierOverrides.selected_child)
    ? modifierOverrides.selected_child
    : defaults.selectedChildName
  return {
    displayMode: modifierOverrides.display_mode === 'single' ? 'single' : defaults.displayMode,
    selectedChildName,
  }
}

function buildCollectionPersistedState(
  asset: AssetRecord,
  defaults: CollectionState,
  displayMode: CollectionDisplayMode,
  selectedChildName: string | null,
  childPanels: Record<string, CollectionChildPanelState>,
): PersistedAssetPanelState {
  const modifierOverrides = buildModifierOverridesRecord(
    {
      display_mode: displayMode,
      selected_child: selectedChildName,
    },
    {
      display_mode: defaults.displayMode,
      selected_child: defaults.selectedChildName,
    },
  )
  const serializedChildPanels = serializeCollectionChildPanelStates(childPanels)
  if (Object.keys(serializedChildPanels).length) {
    modifierOverrides.child_panels = serializedChildPanels
  }
  return {
    modifier_overrides: modifierOverrides,
    override_schema_hash: asset.override_schema_hash,
  }
}

function collectionChildPanelStatesFromValue(value: unknown): Record<string, CollectionChildPanelState> {
  if (!isRecord(value)) {
    return {}
  }
  const entries: Array<[string, CollectionChildPanelState]> = []
  for (const [childName, childState] of Object.entries(value)) {
    if (!isRecord(childState)) {
      continue
    }
    entries.push([childName, normalizeCollectionChildPanelState(childState)])
  }
  return Object.fromEntries(entries)
}

function serializeCollectionChildPanelStates(value: Record<string, CollectionChildPanelState>): Record<string, unknown> {
  const entries: Array<[string, Record<string, unknown>]> = []
  for (const [childName, childState] of Object.entries(value)) {
    const normalized = normalizeCollectionChildPanelState(childState)
    if (
      normalized.override_schema_hash === null
      && normalized.panel_height === null
      && Object.keys(normalized.modifier_overrides).length === 0
    ) {
      continue
    }
    const nextState: Record<string, unknown> = {
      modifier_overrides: normalized.modifier_overrides,
    }
    if (normalized.override_schema_hash !== null) {
      nextState.override_schema_hash = normalized.override_schema_hash
    }
    if (normalized.panel_height !== null) {
      nextState.panel_height = normalized.panel_height
    }
    entries.push([childName, nextState])
  }
  return Object.fromEntries(entries)
}

function normalizeCollectionChildPanelState(value: Partial<CollectionChildPanelState> | null | undefined): CollectionChildPanelState {
  return {
    modifier_overrides: isRecord(value?.modifier_overrides) ? value.modifier_overrides : {},
    override_schema_hash: typeof value?.override_schema_hash === 'string' ? value.override_schema_hash : null,
    panel_height: typeof value?.panel_height === 'number' ? value.panel_height : null,
  }
}

function collectionChildAssetRecord(asset: AssetRecord, child: CollectionChild): AssetRecord {
  return {
    ...asset,
    asset_name: child.name,
    title: child.title,
    description: child.description,
    declared_asset_type: child.assetType,
    asset_type: child.assetType,
    interactive: child.definition.interactive === true,
    definition: child.definition,
    modifier_schema: child.modifierSchema,
    default_modifiers: child.defaultModifiers,
    override_schema_hash: child.overrideSchemaHash,
    objects: [],
  }
}

function collectionChildPrepareTarget(asset: AssetRecord, child: CollectionChild): AssetPanelPrepareTarget {
  return {
    nodeId: asset.node_id,
    assetName: asset.asset_name,
    panelContext: { collection_child_name: child.name },
  }
}

function previousCollectionChild(children: CollectionChild[], childName: string): CollectionChild | null {
  const index = children.findIndex((child) => child.name === childName)
  if (index <= 0) {
    return null
  }
  return children[index - 1] ?? null
}

function nextCollectionChild(children: CollectionChild[], childName: string): CollectionChild | null {
  const index = children.findIndex((child) => child.name === childName)
  if (index === -1 || index >= children.length - 1) {
    return null
  }
  return children[index + 1] ?? null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}
