import type {
  GraphPatchResponse,
  GraphPatchOperation,
  ProjectOpenResponse,
  ProjectSnapshot,
  SessionRecord,
} from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
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

export async function openProject(path: string): Promise<ProjectOpenResponse> {
  return request('/api/v1/projects/open', {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}

export async function initProject(path: string, title?: string): Promise<ProjectOpenResponse> {
  return request('/api/v1/projects/init', {
    method: 'POST',
    body: JSON.stringify({ path, title: title || null }),
  })
}

export async function currentProject(): Promise<ProjectSnapshot> {
  return request('/api/v1/projects/current')
}

export async function getSnapshot(projectId: string): Promise<ProjectSnapshot> {
  return request(`/api/v1/projects/${projectId}/snapshot`)
}

export async function patchGraph(projectId: string, graphVersion: number, operations: GraphPatchOperation[]): Promise<GraphPatchResponse> {
  return request(`/api/v1/projects/${projectId}/graph`, {
    method: 'PATCH',
    body: JSON.stringify({ graph_version: graphVersion, operations }),
  })
}

export async function dismissNotice(projectId: string, issueId: string) {
  return request<Record<string, unknown>>(`/api/v1/projects/${projectId}/notices/${issueId}/dismiss`, {
    method: 'POST',
  })
}

export async function runNode(projectId: string, nodeId: string, mode: string, action: string | null = null) {
  return request<Record<string, unknown>>(`/api/v1/projects/${projectId}/nodes/${nodeId}/run`, {
    method: 'POST',
    body: JSON.stringify({ mode, action }),
  })
}

export async function runAll(projectId: string) {
  return request<Record<string, unknown>>(`/api/v1/projects/${projectId}/runs/run-all`, {
    method: 'POST',
    body: JSON.stringify({ mode: 'run_stale' }),
  })
}

export async function cancelRun(projectId: string, runId: string) {
  return request<Record<string, unknown>>(`/api/v1/projects/${projectId}/runs/${runId}/cancel`, {
    method: 'POST',
  })
}

export async function createCheckpoint(projectId: string) {
  return request<Record<string, unknown>>(`/api/v1/projects/${projectId}/checkpoints`, {
    method: 'POST',
  })
}

export async function restoreCheckpoint(projectId: string, checkpointId: string) {
  return request<Record<string, unknown>>(`/api/v1/projects/${projectId}/checkpoints/${checkpointId}/restore`, {
    method: 'POST',
  })
}

export async function uploadFile(projectId: string, nodeId: string, file: File) {
  const response = await fetch(`/api/v1/projects/${projectId}/file-inputs/${nodeId}/upload`, {
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

export async function listSessions(projectId: string): Promise<SessionRecord[]> {
  return request(`/api/v1/projects/${projectId}/sessions`)
}

export async function stopSession(projectId: string, sessionId: string) {
  return request<Record<string, unknown>>(`/api/v1/projects/${projectId}/sessions/${sessionId}/stop`, {
    method: 'POST',
  })
}

export function makeEditSessionLoadingUrl(projectId: string, sessionId: string): string {
  const params = new URLSearchParams({ project_id: projectId, session_id: sessionId })
  return `/?${params.toString()}`
}
