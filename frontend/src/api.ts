import type { BoardData, Detail, Insights, RecentEvent, Task } from "./types"

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `${r.status} ${r.statusText}`)
  return r.json()
}

export const api = {
  board: () => req<BoardData>("GET", "/api/board"),
  task: (id: string) => req<Detail>("GET", `/api/tasks/${encodeURIComponent(id)}`),
  create: (t: Partial<Task> & { title: string }) => req<Detail>("POST", "/api/tasks", t),
  patch: (id: string, f: Partial<Task>) => req<Detail>("PATCH", `/api/tasks/${encodeURIComponent(id)}`, f),
  move: (id: string, status: string, before_id: string | null) =>
    req<Detail>("POST", `/api/tasks/${encodeURIComponent(id)}/move`, { status, before_id }),
  log: (id: string, text: string, actor: string) =>
    req<Detail>("POST", `/api/tasks/${encodeURIComponent(id)}/log`, { text, actor }),
  restore: (id: string) => req<Detail>("POST", `/api/tasks/${encodeURIComponent(id)}/restore`),
  remove: (id: string) => req<{ ok: boolean }>("DELETE", `/api/tasks/${encodeURIComponent(id)}`),
  insights: () => req<Insights>("GET", "/api/insights"),
  recent: (limit = 100) => req<{ events: RecentEvent[] }>("GET", `/api/events/recent?limit=${limit}`),
  dispatch: (id: string) => req<{ ok: boolean; pid: number }>("POST", `/api/tasks/${encodeURIComponent(id)}/dispatch`),
  dispatches: () => req<{ running: { task_id: string; pid: number; started: string }[] }>("GET", "/api/dispatches"),
  stopDispatch: (id: string) => req<{ ok: boolean }>("POST", `/api/dispatches/${encodeURIComponent(id)}/stop`),
  activity: (id: string) =>
    req<{ activities: { kind: string; text: string; ts: string }[]; running: boolean }>(
      "GET", `/api/dispatches/${encodeURIComponent(id)}/activity`),
}

/** SSE rev 广播 → onChange; 返回清理函数。EventSource 自带断线重连。 */
export function sse(onChange: () => void): () => void {
  const es = new EventSource("/api/events")
  es.onmessage = onChange
  return () => es.close()
}
