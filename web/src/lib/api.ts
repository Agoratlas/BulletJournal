import type {
  GraphPatchResponse,
  GraphPatchOperation,
  ProjectSnapshot,
  SessionRecord,
} from './types'

declare global {
  interface Window {
    __BULLETJOURNAL_BASE_PATH__?: string
  }
}

export function appBasePath(): string {
  const value = window.__BULLETJOURNAL_BASE_PATH__ || ''
  if (!value || value === '/') {
    return ''
  }
  return value.endsWith('/') ? value.slice(0, -1) : value
}

export function appUrl(path: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${appBasePath()}${normalizedPath}`
}

export function executionLogDownloadUrl(nodeId: string, stream: 'stdout' | 'stderr'): string {
  return appUrl(`/api/v1/nodes/${encodeURIComponent(nodeId)}/execution-logs/${stream}/download`)
}

export function notebookDownloadUrl(nodeId: string): string {
  return appUrl(`/api/v1/nodes/${encodeURIComponent(nodeId)}/notebook/download`)
}

export async function downloadNotebookSource(nodeId: string): Promise<string> {
  const response = await fetch(notebookDownloadUrl(nodeId))
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `HTTP ${response.status}`)
  }
  return response.text()
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(appUrl(url), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) {
    const text = await response.text()
    let detail = text
    try {
      const parsed = JSON.parse(text)
      detail = formatErrorDetail(parsed.detail ?? parsed)
    } catch {
      // keep text
    }
    throw new Error(detail || `HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') {
    return detail
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') {
          return item
        }
        if (item && typeof item === 'object') {
          const message = 'msg' in item && typeof item.msg === 'string' ? item.msg : JSON.stringify(item)
          const location = Array.isArray((item as { loc?: unknown }).loc)
            ? (item as { loc: unknown[] }).loc.join('.')
            : null
          return location ? `${location}: ${message}` : message
        }
        return String(item)
      })
      .join('\n')
  }
  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail)
  }
  return String(detail)
}

export async function currentProject(): Promise<ProjectSnapshot> {
  return request('/api/v1/project/snapshot')
}

export async function getSnapshot(): Promise<ProjectSnapshot> {
  return request('/api/v1/project/snapshot')
}

export async function patchGraph(graphVersion: number, operations: GraphPatchOperation[]): Promise<GraphPatchResponse> {
  return request('/api/v1/graph', {
    method: 'PATCH',
    body: JSON.stringify({ graph_version: graphVersion, operations }),
  })
}

export async function dismissNotice(issueId: string) {
  return request<Record<string, unknown>>(`/api/v1/notices/${issueId}/dismiss`, {
    method: 'POST',
  })
}

export async function runNode(nodeId: string, mode: string, action: string | null = null) {
  return request<Record<string, unknown>>(`/api/v1/nodes/${nodeId}/run`, {
    method: 'POST',
    body: JSON.stringify({ mode, action }),
  })
}

export async function runAll() {
  return request<Record<string, unknown>>('/api/v1/runs/run-all', {
    method: 'POST',
    body: JSON.stringify({ mode: 'run_stale' }),
  })
}

export async function cancelRun(runId: string) {
  return request<Record<string, unknown>>(`/api/v1/runs/${runId}/cancel`, {
    method: 'POST',
  })
}

export async function createCheckpoint() {
  return request<Record<string, unknown>>('/api/v1/checkpoints', {
    method: 'POST',
  })
}

export async function restoreCheckpoint(checkpointId: string) {
  return request<Record<string, unknown>>(`/api/v1/checkpoints/${checkpointId}/restore`, {
    method: 'POST',
  })
}

export async function uploadFile(nodeId: string, file: File) {
  const response = await fetch(appUrl(`/api/v1/file-inputs/${nodeId}/upload`), {
    method: 'POST',
    headers: {
      'X-Filename': file.name,
      'Content-Type': file.type || 'application/octet-stream',
    },
    body: await file.arrayBuffer(),
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text)
  }
  return response.json() as Promise<Record<string, unknown>>
}

export async function setArtifactState(nodeId: string, artifactName: string, state: 'ready' | 'stale') {
  return request<Record<string, unknown>>(`/api/v1/artifacts/${encodeURIComponent(nodeId)}/${encodeURIComponent(artifactName)}/state`, {
    method: 'POST',
    body: JSON.stringify({ state }),
  })
}

export async function setNodeOutputsState(nodeId: string, state: 'ready' | 'stale', onlyCurrentState: 'ready' | 'stale' | 'pending' | null = null) {
  return request<Record<string, unknown>>(`/api/v1/nodes/${encodeURIComponent(nodeId)}/outputs/state`, {
    method: 'POST',
    body: JSON.stringify({ state, only_current_state: onlyCurrentState }),
  })
}

export async function listSessions(): Promise<SessionRecord[]> {
  return request('/api/v1/sessions')
}

export async function stopSession(sessionId: string) {
  return request<Record<string, unknown>>(`/api/v1/sessions/${sessionId}/stop`, {
    method: 'POST',
  })
}
