import { useEffect, useState } from "react"
import { api } from "../api"
import { evText, STATUSES, STATUS_DOT, type InsightTask, type Insights, type RecentEvent, type TaskEvent } from "../types"

const CARD = "rounded-lg border border-zinc-200 dark:border-zinc-700/60 bg-white dark:bg-zinc-900"
const H2 = "px-3 h-9 flex items-center font-medium text-xs text-zinc-500 dark:text-zinc-400 border-b border-zinc-200 dark:border-zinc-800"

/** 监控页: 数据零新增, 全部由 /api/insights + /api/events/recent 派生 */
export default function Monitor({ rev, onOpen }: { rev: number; onOpen: (id: string) => void }) {
  const [ins, setIns] = useState<Insights | null>(null)
  const [evs, setEvs] = useState<RecentEvent[]>([])

  useEffect(() => {
    api.insights().then(setIns).catch(() => {})
    api.recent(100).then(r => setEvs(r.events)).catch(() => {})
  }, [rev])

  const counts = ins?.counts || {}
  return (
    <div className="flex-1 grid grid-cols-1 lg:grid-cols-3 gap-4 p-4 overflow-y-auto">
      <div className="lg:col-span-2 flex flex-col gap-4 min-w-0">
        {/* 概览 + 建议 */}
        <div className={`${CARD} shrink-0`}>
          <div className={H2}>概览 · 生成于 {ins?.generated?.slice(11) || "…"}</div>
          <div className="p-3 flex flex-wrap gap-6">
            {STATUSES.slice(0, 3).map(s => (
              <div key={s} className="flex items-center gap-2">
                <span className={`size-2 rounded-full ${STATUS_DOT[s]}`} />
                <span className="text-2xl font-semibold tabular-nums">{counts[s] ?? 0}</span>
                <span className="text-xs text-zinc-500 dark:text-zinc-400">
                  {s}
                  {ins?.stage_avg?.[s] != null && <span className="text-zinc-400 dark:text-zinc-500"> · 均 {ins.stage_avg[s]}h</span>}
                </span>
              </div>
            ))}
          </div>
          {ins && ins.suggestions.length > 0 && (
            <ul className="px-3 pb-3 space-y-1.5">
              {ins.suggestions.map((s, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400">
                  <span>▸</span>
                  {s}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 停滞 / 滞留 / 逾期 */}
        <div className={`${CARD} shrink-0`}>
          <div className={H2}>需要关注</div>
          <NeedList ins={ins} onOpen={onOpen} />
        </div>

        {/* 事件流 */}
        <div className={`${CARD} flex-1 flex flex-col min-h-64`}>
          <div className={H2}>事件流 · 最近 {evs.length}</div>
          <ul className="flex-1 overflow-y-auto p-2 space-y-0.5 text-xs">
            {evs.map(e => (
              <li key={e.id} className="flex gap-2 px-1.5 py-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800/60">
                <span className="text-zinc-400 dark:text-zinc-500 tabular-nums shrink-0">{e.ts.slice(5, 16)}</span>
                <span className="text-blue-600 dark:text-blue-400 shrink-0 max-w-24 truncate">{e.actor}</span>
                <span className="truncate text-zinc-500 dark:text-zinc-400 max-w-40">{e.title || e.task_id}</span>
                <span className="text-zinc-700 dark:text-zinc-300 truncate">
                  {evText({ ...e, detail: safeParse(e.detail) } as TaskEvent)}
                </span>
              </li>
            ))}
            {!evs.length && <li className="text-center text-zinc-500 py-8">暂无事件</li>}
          </ul>
        </div>
      </div>

      {/* 执行者负载 */}
      <div className={`${CARD} h-fit shrink-0`}>
        <div className={H2}>执行者负载</div>
        <ul className="p-3 space-y-2.5">
          {ins?.load.map(d => {
            const total = d.active + d.review
            const pct = total ? (d.active / total) * 100 : 0
            return (
              <li key={d.owner}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="font-medium">{d.owner}</span>
                  <span className="text-zinc-500 dark:text-zinc-400">
                    在手 {d.active} · 待验收 {d.review}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden flex">
                  <div className="bg-blue-500" style={{ width: `${pct}%` }} />
                  <div className="bg-amber-500" style={{ width: `${100 - pct}%` }} />
                </div>
              </li>
            )
          })}
          {ins && !ins.load.length && <li className="text-xs text-zinc-500 py-4 text-center">暂无在办</li>}
        </ul>
      </div>
    </div>
  )
}

function NeedList({ ins, onOpen }: { ins: Insights | null; onOpen: (id: string) => void }) {
  const groups: { key: keyof Insights; label: string; cls: string }[] = [
    { key: "review_overdue", label: "验收滞留", cls: "text-amber-600 dark:text-amber-400" },
    { key: "stalled", label: "停滞", cls: "text-red-500" },
    { key: "overdue", label: "已逾期", cls: "text-red-500" },
    { key: "due_soon", label: "临期", cls: "text-amber-500" },
  ]
  const rows = groups.flatMap(g =>
    ((ins?.[g.key] as InsightTask[] | undefined) || []).map(t => ({ ...t, label: g.label, cls: g.cls })),
  )
  if (ins && !rows.length) return <div className="py-8 text-center text-xs text-zinc-500">✓ 全部健康</div>
  return (
    <ul className="p-2 space-y-0.5 text-xs">
      {rows.map(t => (
        <li key={`${t.label}-${t.id}`}>
          <button onClick={() => onOpen(t.id)} className="w-full flex gap-2 px-1.5 py-1 rounded text-left hover:bg-zinc-100 dark:hover:bg-zinc-800/60">
            <span className={`shrink-0 ${t.cls}`}>[{t.label}]</span>
            <span className="truncate flex-1">{t.title}</span>
            <span className="text-zinc-500 dark:text-zinc-400 shrink-0 tabular-nums">
              {t.hours != null ? `${t.hours}h` : t.due}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}

function safeParse(s: string): Record<string, unknown> {
  try {
    return JSON.parse(s || "{}")
  } catch {
    return {}
  }
}
