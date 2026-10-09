import { useEffect, useRef, useState } from "react"
import { api } from "../api"
import { evText, safeParse, STATUSES, STATUS_DOT, type InsightTask, type Insights, type RecentEvent, type TaskEvent } from "../types"

const CARD = "rounded-lg border border-zinc-200 dark:border-zinc-700/60 bg-white dark:bg-zinc-900"
const H2 = "px-3 h-9 flex items-center font-medium text-xs text-zinc-500 dark:text-zinc-400 border-b border-zinc-200 dark:border-zinc-800"

/** 监控页: 数据零新增, 全部由 /api/insights + /api/events/recent + /api/dispatches 派生 */
export default function Monitor({ rev, onOpen }: { rev: number; onOpen: (id: string) => void }) {
  const [ins, setIns] = useState<Insights | null>(null)
  const [evs, setEvs] = useState<RecentEvent[]>([])
  const [disp, setDisp] = useState<{ task_id: string; pid: number; started: string; agent: string }[]>([])
  const [openAct, setOpenAct] = useState<string | null>(null) // 展开过程流的卡
  const [acts, setActs] = useState<{ kind: string; text: string; ts: string }[]>([])
  const [rawMode, setRawMode] = useState(false) // 摘要 ↔ 进程真实 stdout 原文
  const [raw, setRaw] = useState<string[]>([])
  const [rec, setRec] = useState<{ task_id: string; ended: string }[]>([])

  useEffect(() => {
    api.insights().then(setIns).catch(() => {})
    api.recent(100).then(r => setEvs(r.events)).catch(() => {})
    api.dispatches().then(r => setDisp(r.running)).catch(() => {})
    api.recentDispatches().then(r => setRec(r.recent)).catch(() => {})
  }, [rev])

  // 展开某卡时 2s 轮询它的 AI 过程流(摘要或原文), 折叠即停
  useEffect(() => {
    if (!openAct) return
    let stop = false
    const tick = () => (rawMode
      ? api.activityRaw(openAct).then(r => { if (!stop) setRaw(r.raw) })
      : api.activity(openAct).then(r => { if (!stop) setActs(r.activities) })
    ).catch(() => {})
    tick()
    const iv = setInterval(tick, 2000)
    return () => { stop = true; clearInterval(iv) }
  }, [openAct, rawMode])

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

      {/* 运行中的 AI + 执行者负载 */}
      <div className="flex flex-col gap-4 shrink-0">
        <div className={CARD}>
          <div className={H2}>
            运行中的 AI · {disp.length}
            <span className="ml-auto flex rounded text-[10px] border border-zinc-200 dark:border-zinc-700 overflow-hidden">
              <button onClick={() => setRawMode(false)}
                className={`px-1.5 h-5 ${!rawMode ? "bg-zinc-200 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-200" : "text-zinc-400"}`}>
                摘要
              </button>
              <button onClick={() => setRawMode(true)}
                className={`px-1.5 h-5 ${rawMode ? "bg-zinc-200 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-200" : "text-zinc-400"}`}
                title="进程真实 stdout(stream-json 原文, 黑底终端样式)">
                原文
              </button>
            </span>
          </div>
          {disp.length > 0 && (
            <ul className="p-2 pb-0 space-y-1">
              {disp.map(d => (
                <li key={d.task_id}>
                  <div className="flex items-center gap-2 text-xs text-emerald-600 dark:text-emerald-400">
                    <span className="animate-pulse" title={`pid ${d.pid} · ${d.started}`}>⟳</span>
                    <span className="shrink-0" title={d.agent === "acceptor" ? "验收 agent" : "执行 agent"}>
                      {d.agent === "acceptor" ? "✔" : "⚡"}
                    </span>
                    <button onClick={() => setOpenAct(openAct === d.task_id ? null : d.task_id)}
                      className="truncate hover:underline flex-1 text-left" title="点开看 AI 正在做什么">
                      {d.agent === "acceptor" ? "验收中" : "执行中"} · {d.task_id} {openAct === d.task_id ? "▾" : "▸"}
                    </button>
                    <button
                      onClick={() => api.stopDispatch(d.task_id).catch(() => {})}
                      className="shrink-0 h-5 px-1.5 rounded text-red-500 hover:bg-red-500/10"
                      title="强停该 AI 进程"
                    >
                      停止
                    </button>
                  </div>
                  {openAct === d.task_id && (
                    rawMode ? <Terminal lines={raw} /> : <ActivityStream acts={acts} />
                  )}
                </li>
              ))}
            </ul>
          )}
          {rec.length > 0 && (
            <ul className="p-2 pt-1 space-y-0.5 border-t border-zinc-100 dark:border-zinc-800/60 mt-1">
              <li className="text-[10px] text-zinc-400 px-1 pb-0.5">最近进程</li>
              {rec.slice(0, 4).map(d => (
                <li key={d.task_id + d.ended}>
                  <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                    <span className="tabular-nums shrink-0">{d.ended}</span>
                    <button onClick={() => setOpenAct(openAct === d.task_id ? null : d.task_id)}
                      className="truncate hover:underline flex-1 text-left" title="回看该进程过程流">
                      {d.task_id.slice(9)} {openAct === d.task_id ? "▾" : "▸"}
                    </button>
                  </div>
                  {openAct === d.task_id && (
                    rawMode ? <Terminal lines={raw} /> : <ActivityStream acts={acts} />
                  )}
                </li>
              ))}
            </ul>
          )}
          {!disp.length && !rec.length && (
            <div className="px-3 py-4 text-xs text-zinc-500 dark:text-zinc-400">
              当前无 AI 进程——卡面 <span className="text-blue-600 dark:text-blue-400">⚡派发</span> 执行 /{" "}
              <span className="text-emerald-600 dark:text-emerald-400">✔验收</span> 核验，过程流在此实时可看
            </div>
          )}
        </div>

        <div className={CARD}>
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
    </div>
  )
}

/** 终端原文视图: 进程真实 stdout(stream-json 尾行), 黑底等宽, 自动滚底 */
function Terminal({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight })
  if (!lines.length)
    return <div className="ml-5 py-1 text-[11px] text-zinc-400">尚无输出…</div>
  return (
    <div ref={ref} className="ml-5 my-1 p-2 rounded-md bg-zinc-950 border border-zinc-800 text-zinc-300 font-mono text-[11px] leading-relaxed max-h-64 overflow-y-auto whitespace-pre-wrap break-all select-text">
      {lines.join("\n")}
    </div>
  )
}

/** AI 过程流(借鉴 zcode ToolCallBlock: 一行摘要, 人读优先, 最新在上) */
function ActivityStream({ acts }: { acts: { kind: string; text: string; ts: string }[] }) {
  if (!acts.length)
    return <div className="ml-5 py-1 text-[11px] text-zinc-400">启动中，尚无输出…</div>
  return (
    <ul className="ml-5 my-1 p-1.5 rounded-md bg-zinc-50 dark:bg-zinc-800/60 max-h-56 overflow-y-auto space-y-0.5">
      {[...acts].reverse().map((a, i) => (
        <li key={i} className="flex gap-1.5 text-[11px] leading-relaxed">
          <span className="text-zinc-400 dark:text-zinc-500 tabular-nums shrink-0">{a.ts?.slice(0, 8)}</span>
          <span className="shrink-0" title={a.kind}>
            {a.kind === "tool" ? "🔧" : a.kind === "done" ? "✅" : "💬"}
          </span>
          <span className={`break-all ${a.kind === "tool" ? "text-blue-600 dark:text-blue-400"
            : a.kind === "done" ? "text-emerald-600 dark:text-emerald-400 font-medium" : "text-zinc-600 dark:text-zinc-300"}`}>
            {a.text}
          </span>
        </li>
      ))}
    </ul>
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
