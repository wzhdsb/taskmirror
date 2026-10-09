export type Status = "待执行" | "执行中" | "待验收" | "完成"
export const STATUSES: Status[] = ["待执行", "执行中", "待验收", "完成"]
export const STATUS_ORD: Record<Status, number> = { 待执行: 0, 执行中: 1, 待验收: 2, 完成: 3 }
export const PRIORITIES = ["low", "normal", "high", "urgent"] as const

export interface Task {
  id: string
  title: string
  status: Status
  owner: string
  priority: string
  labels: string[]
  color: string | null
  due: string | null
  body: string
  position: number
  created: string
  updated: string
  /** 短代号 MMDD-N(口头/会话引用用) */
  code: string
  /** 正文 checkbox 任务节点现算(无节点为 0, 前端不渲染) */
  stepsDone: number
  stepsTotal: number
  /** 关联依赖(单向声明), 后五个字段由服务端反查现算 */
  related: string[]
  depends: string[]
  relatesBack: string[]
  dependedBy: string[]
  waiting: string[]
}

export interface TaskEvent {
  id: number
  task_id: string
  ts: string
  actor: string
  event: string
  detail: Record<string, unknown>
}

export interface Detail {
  task: Task
  body: string
  events: TaskEvent[]
}

export interface BoardData {
  tasks: Task[]
  done: Task[]
  rev: number
}

export interface Load {
  owner: string
  active: number
  review: number
}

export interface InsightTask {
  id: string
  title: string
  owner?: string
  hours?: number
  due?: string
  status?: string
}

/** adviser 规则层输出(GET /api/insights) */
export interface Insights {
  generated: string
  counts: Record<string, number>
  stage_avg: Record<string, number>
  load: Load[]
  stalled: InsightTask[]
  review_overdue: InsightTask[]
  due_soon: InsightTask[]
  overdue: InsightTask[]
  suggestions: string[]
}

/** GET /api/events/recent 行; detail 为 JSON 字符串 */
export interface RecentEvent extends Omit<TaskEvent, "detail"> {
  title: string | null
  detail: string
}

/** 事件 → 日志行文案(与后端 mirror.py 的 _event_line 同语义) */
export function evText(e: TaskEvent): string {
  const d = e.detail || {}
  switch (e.event) {
    case "created": return `建卡`
    case "take": return `认领(owner=${d.owner})`
    case "status": return `状态 ${d.from}→${d.to}`
    case "owner": return `owner 置为 ${d.to || "空"}`
    case "title": return `标题改为「${d.to}」`
    case "body": return `更新正文`
    case "labels": return `标签 → ${(d.to as string[] || []).join(",")}`
    case "priority": return `优先级 → ${d.to}`
    case "due": return `截止 → ${d.to || "无"}`
    case "color": return `颜色 → ${d.to || "无"}`
    case "log": return String(d.text || "")
    case "relate": {
      const arrow = d.removed ? "−" : "→"
      return `关联 ${d.kind} ${arrow} ${d.target}`
    }
    case "restore": return `恢复到待执行`
    case "imported": return `从旧系统导入`
    case "dispatched": return `⚡ 派发 AI 进程 ${d.pid}`
    case "dispatch_exit": return `AI 进程退出 (rc=${d.rc})`
    case "dispatch_stopped": return `⛔ AI 进程被停止, 卡已打回`
    case "dispatch_lost": return `AI 进程失联(服务重启), 请检查`
    case "deleted": return `删除卡片`
    default: return e.event
  }
}

/** 状态色: 列头圆点/卡片左条/drawer pills 共用 */
export const STATUS_DOT: Record<Status, string> = {
  待执行: "bg-zinc-400",
  执行中: "bg-blue-500",
  待验收: "bg-amber-500",
  完成: "bg-emerald-500",
}

/** 卡片左侧 3px 色条 / drawer 选中 pill 实底 (inline style 用) */
export const STATUS_BAR: Record<Status, string> = {
  待执行: "#a1a1aa",
  执行中: "#3b82f6",
  待验收: "#f59e0b",
  完成: "#10b981",
}

/** 标签可选色 */
export const COLORS = ["#3b82f6", "#f59e0b", "#ef4444", "#10b981", "#8b5cf6", "#ec4899"]

export function safeParse(s: string): Record<string, unknown> {
  try {
    return JSON.parse(s || "{}")
  } catch {
    return {}
  }
}
