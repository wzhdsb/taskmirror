import { useSortable } from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { STATUS_BAR, type Task } from "../types"

function dueCls(t: Task): string | null {
  if (!t.due) return null
  const days = (new Date(t.due + "T23:59:59").getTime() - Date.now()) / 86400000
  if (days < 0) return "text-red-500"
  if (days <= 3) return "text-amber-500"
  return null
}

export default function Card({
  task: t,
  onOpen,
  overlay,
  flash,
  onDispatch,
  onReview,
}: {
  task: Task
  onOpen?: (id: string) => void
  overlay?: boolean
  flash?: "new" | "upd"
  onDispatch?: (id: string) => void
  onReview?: (id: string) => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: t.id })
  const due = dueCls(t)
  const bar = { borderLeftColor: STATUS_BAR[t.status] }

  const inner = (
    <div className="p-2 pl-2.5">
      <div className="flex items-start gap-1">
        {t.code && <span className="mt-0.5 px-1 rounded bg-zinc-100 dark:bg-zinc-700/70 text-zinc-400 dark:text-zinc-300 text-[10px] font-mono shrink-0" title={`短代号 ${t.code}(口头引用用)`}>{t.code}</span>}
        <p className="flex-1 line-clamp-2 font-medium leading-snug break-all">{t.title}</p>
        {!!t.starred && <span className="mt-0.5 text-amber-500 shrink-0" title="星标：下一批要做的">★</span>}
      </div>
      {t.stepsTotal > 0 && (
        <div className="mt-1 flex items-center gap-1" title={`任务节点 ${t.stepsDone}/${t.stepsTotal}`}>
          <div className="flex gap-0.5 flex-1 min-w-12">
            {Array.from({ length: t.stepsTotal }, (_, i) => (
              <span key={i} className={`h-1 flex-1 rounded-full ${i < t.stepsDone ? "bg-emerald-500" : "bg-zinc-200 dark:bg-zinc-700"}`} />
            ))}
          </div>
          <span className="text-[10px] tabular-nums text-zinc-400">{t.stepsDone}/{t.stepsTotal}</span>
        </div>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px] text-zinc-500 dark:text-zinc-300">
        {t.owner && (
          <span className="px-1.5 rounded bg-blue-500/10 text-blue-600 dark:text-blue-400 max-w-24 truncate">{t.owner}</span>
        )}
        {(t.related?.length || t.relatesBack?.length) ? (
          <span
            className="px-1 rounded bg-blue-500/10 text-blue-600 dark:text-blue-400"
            title={`关联: ${[...t.related, ...t.relatesBack].join(", ")}`}
          >
            ⧉{t.related.length + t.relatesBack.length}
          </span>
        ) : null}
        {!overlay && t.waiting?.map(w => (
          <button
            key={w}
            title={`前置未完成: ${w} — 点击查看`}
            onClick={e => { e.stopPropagation(); onOpen?.(w) }}
            onPointerDown={e => e.stopPropagation()}
            className="px-1 rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 max-w-full truncate hover:bg-amber-500/20 transition-colors"
          >
            ⏳{w.slice(9)}
          </button>
        ))}
        {t.labels.map(l => (
          <span key={l} className="px-1.5 rounded-full border border-zinc-200 dark:border-zinc-700 max-w-24 truncate">
            {l}
          </span>
        ))}
        {t.due && (
          <span className={due ?? ""} title={t.due}>
            📅 {t.due.slice(5)}
          </span>
        )}
        {t.color && <span className="size-2 rounded-full" style={{ background: t.color }} />}
        {!overlay && t.status === "待执行" && onDispatch && (
          <button
            title="派发 AI 执行这张卡"
            onClick={e => {
              e.stopPropagation()
              onDispatch(t.id)
            }}
            onPointerDown={e => e.stopPropagation()}
            className="ml-auto h-5 px-1.5 rounded text-blue-600 dark:text-blue-400 hover:bg-blue-500/10 transition-colors"
          >
            ⚡派发
          </button>
        )}
        {!overlay && t.status === "待验收" && onReview && (
          <button
            title="派 AI 验收这张卡(逐项核验交付物, 三判: 通过/打回/转人工)"
            onClick={e => {
              e.stopPropagation()
              onReview(t.id)
            }}
            onPointerDown={e => e.stopPropagation()}
            className="ml-auto h-5 px-1.5 rounded text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/10 transition-colors"
          >
            ✔验收
          </button>
        )}
      </div>
    </div>
  )

  const cls = `rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 shadow-sm hover:shadow-md cursor-grab active:cursor-grabbing touch-manipulation select-none transition-shadow border-l-[3px] ${
    t.status === "完成" ? "opacity-70" : ""
  } ${isDragging ? "opacity-40" : ""} ${overlay ? "shadow-xl rotate-2" : ""} ${
    flash === "new" ? "tm-flash-new" : flash === "upd" ? "tm-flash-upd" : ""
  }`

  if (overlay) return <div className={cls} style={bar}>{inner}</div>
  return (
    <div
      ref={setNodeRef}
      style={{ ...bar, transform: CSS.Translate.toString(transform), transition }}
      {...attributes}
      {...listeners}
      onClick={() => onOpen?.(t.id)}
      className={cls}
    >
      {inner}
    </div>
  )
}
