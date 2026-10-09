import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
} from "@dnd-kit/core"
import { SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy } from "@dnd-kit/sortable"
import { useState } from "react"
import { STATUSES, STATUS_DOT, type Status, type Task } from "../types"
import Card from "./Card"

const GRID = "grid grid-cols-4 gap-3"

export default function Board({
  tasks,
  done,
  onOpen,
  onMove,
  onNew,
  onCreate,
  flash,
  onDispatch,
  onReview,
}: {
  tasks: Task[]
  done: Task[]
  onOpen: (id: string) => void
  onMove: (id: string, status: string, beforeId: string | null, project?: string) => void
  onNew: () => void
  onCreate: (project: string, status: string) => void
  flash: Record<string, "new" | "upd">
  onDispatch: (id: string) => void
  onReview: (id: string) => void
}) {
  const [activeId, setActiveId] = useState<string | null>(null)
  // 折叠的泳道名持久化; 解析失败当全展开
  const [collapsed, setCollapsed] = useState<string[]>(() => {
    try {
      const v = JSON.parse(localStorage.getItem("tm_lanes") || "[]")
      return Array.isArray(v) ? v.filter(x => typeof x === "string") : []
    } catch {
      return []
    }
  })
  const toggleLane = (p: string) =>
    setCollapsed(v => {
      const n = v.includes(p) ? v.filter(x => x !== p) : [...v, p]
      localStorage.setItem("tm_lanes", JSON.stringify(n))
      return n
    })
  // 碰撞候选排除被拖卡自身, 否则空格时它离指针"最近"导致拖不动
  const collision: CollisionDetection = args =>
    closestCorners({ ...args, droppableContainers: args.droppableContainers.filter(c => c.id !== args.active.id) })
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const all = [...tasks, ...done]
  const active = all.find(t => t.id === activeId)

  // 泳道: 按 project 分组, 活跃度(最新 updated)排序, 未分组永远垫底; 空道自然消失
  const laneOf = (t: Task) => t.project || ""
  const names = [...new Set(all.map(laneOf))]
  const fresh = (p: string) => Math.max(...all.filter(t => laneOf(t) === p).map(t => Date.parse(t.updated) || 0))
  const lanes = [...names.filter(p => p !== "").sort((a, b) => fresh(b) - fresh(a)), ...names.filter(p => p === "")]

  function cell(p: string, s: Status): Task[] {
    const inLane = (t: Task) => laneOf(t) === p
    return s === "完成" ? all.filter(t => t.status === s && inLane(t)) : tasks.filter(t => t.status === s && inLane(t))
  }

  function handleEnd(e: DragEndEvent) {
    setActiveId(null)
    const { active: a, over } = e
    if (!over) return
    const id = String(a.id)
    const overId = String(over.id)
    if (overId === id) return // 原地
    let toStatus: Status | undefined
    let toProject: string | undefined
    let beforeId: string | null = null
    if (overId.includes("|")) {
      // 泳道格子本体 = 该格列尾; id 形如 "project|status"
      const i = overId.indexOf("|")
      toProject = overId.slice(0, i)
      toStatus = STATUSES.find(s => s === overId.slice(i + 1))
    } else {
      const target = all.find(x => x.id === overId)
      if (!target) return
      toStatus = target.status
      toProject = laneOf(target)
      beforeId = overId // 插在被覆盖卡前面
    }
    if (!toStatus) return
    const src = all.find(x => x.id === id)
    const projChanged = !!src && toProject !== undefined && laneOf(src) !== toProject
    if (projChanged) {
      const from = laneOf(src!) || "未分组"
      const to = toProject || "未分组"
      if (!window.confirm(`把「${src!.title}」从「${from}」移到「${to}」？\n(将更新所属项目)`)) return
    }
    onMove(id, toStatus, beforeId, projChanged ? toProject : undefined)
  }

  if (!all.length)
    return (
      <div className="flex-1 grid place-items-center p-8">
        <button
          onClick={onNew}
          className="px-8 py-6 rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 text-sm text-zinc-500 hover:text-blue-500 hover:border-blue-500/60 transition-colors"
        >
          + 新建第一张任务卡
        </button>
      </div>
    )

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={collision}
      onDragStart={e => setActiveId(String(e.active.id))}
      onDragEnd={handleEnd}
      onDragCancel={() => setActiveId(null)}
    >
      <div className="flex-1 flex gap-4 overflow-hidden">
        <div className="flex-1 overflow-auto px-4 pb-4">{/* 顶部零内衬: sticky 表头吸附点=容器顶, 不留透缝 */}
          <div className="min-w-[60rem] flex flex-col gap-2.5">
            {/* 状态列表头: 与泳道同网格模板(无边框无内衬) → 列必对齐; 吸顶时整条实底盖住滚动内容 */}
            <div className="sticky top-0 z-10 -mx-4 px-4 pt-1 pb-1.5 bg-zinc-50 dark:bg-zinc-950">
              <div className={GRID}>
                {STATUSES.map(s => (
                  <div key={s} className="flex items-center gap-2 px-1.5 h-7">
                    <span className={`size-2 rounded-full ${STATUS_DOT[s]}`} />
                    <span className="font-medium text-sm">{s}</span>
                    <span className="text-xs text-zinc-500 dark:text-zinc-400">{all.filter(t => t.status === s).length}</span>
                  </div>
                ))}
              </div>
            </div>
            {lanes.map(p => {
              const cells = STATUSES.map(s => cell(p, s))
              const total = cells.reduce((n, c) => n + c.length, 0)
              const stars = cells.reduce((n, c) => n + c.filter(t => t.starred).length, 0)
              const fold = collapsed.includes(p)
              return (
                <section key={p || "__none"}>
                  {/* 泳道头: 通栏色条区分项目, 网格不带盒子 → 与表头同宽对齐 */}
                  <header className="flex items-center gap-2 h-8 px-2.5 rounded-md bg-zinc-200/50 dark:bg-zinc-800/50">
                    <button
                      onClick={() => toggleLane(p)}
                      className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 text-xs w-4 shrink-0"
                      title={fold ? "展开泳道" : "折叠泳道"}
                    >
                      {fold ? "▶" : "▼"}
                    </button>
                    {p ? (
                      <button
                        onClick={() => toggleLane(p)}
                        title="折叠/展开泳道(单项目模式用顶部下拉)"
                        className="font-medium text-sm hover:text-blue-600 dark:hover:text-blue-400 transition-colors truncate max-w-72"
                      >
                        {p}
                      </button>
                    ) : (
                      <span className="font-medium text-sm text-zinc-500 dark:text-zinc-400">未分组</span>
                    )}
                    <span className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">{total}</span>
                    {stars > 0 && <span className="text-xs text-amber-500">★{stars}</span>}
                    {fold && (
                      <span className="text-xs text-zinc-400 truncate">
                        {STATUSES.map((s, i) => `${s} ${cells[i].length}`).join(" · ")}
                      </span>
                    )}
                  </header>
                  {!fold && (
                    <div className={`${GRID} mt-1.5`}>
                      {cells.map((list, i) => (
                        <Cell
                          key={STATUSES[i]}
                          droppableId={`${p}|${STATUSES[i]}`}
                          tasks={list}
                          onOpen={onOpen}
                          flash={flash}
                          onDispatch={onDispatch}
                          onReview={onReview}
                          onCreate={onCreate}
                        />
                      ))}
                    </div>
                  )}
                </section>
              )
            })}
          </div>
        </div>
        <TreeColumn all={all} onOpen={onOpen} />
      </div>
      <DragOverlay>{active && <Card task={active} overlay />}</DragOverlay>
    </DndContext>
  )
}

/** 泳道格: 淡色块当列底, 独立 droppable(id=project|status), 星标置顶; 超约5张格内滚动; 点空白处=在该项目该列新建 */
function Cell({
  droppableId,
  tasks,
  onOpen,
  flash,
  onDispatch,
  onReview,
  onCreate,
}: {
  droppableId: string
  tasks: Task[]
  onOpen: (id: string) => void
  flash: Record<string, "new" | "upd">
  onDispatch: (id: string) => void
  onReview: (id: string) => void
  onCreate: (project: string, status: string) => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: droppableId })
  const sorted = [...tasks].sort((a, b) => (b.starred || 0) - (a.starred || 0))
  const cut = droppableId.indexOf("|")
  const project = droppableId.slice(0, cut)
  const status = droppableId.slice(cut + 1)
  return (
    <div
      ref={setNodeRef}
      onClick={e => {
        if (e.target === e.currentTarget) onCreate(project, status)
      }}
      className={`flex flex-col gap-1.5 min-h-20 max-h-[21rem] overflow-y-auto p-1.5 rounded-md transition-colors [scrollbar-width:thin] ${
        isOver ? "bg-blue-500/10 ring-1 ring-blue-500/50" : "bg-zinc-100/70 dark:bg-zinc-900/40"
      }`}
    >
      <SortableContext items={sorted.map(t => t.id)} strategy={verticalListSortingStrategy}>
        {sorted.map(t => (
          <Card key={t.id} task={t} onOpen={onOpen} flash={flash[t.id]} onDispatch={onDispatch} onReview={onReview} />
        ))}
      </SortableContext>
      <button
        onClick={e => {
          e.stopPropagation()
          onCreate(project, status)
        }}
        className="h-8 shrink-0 rounded-md border border-dashed border-zinc-300 dark:border-zinc-700 text-xs text-zinc-400 hover:text-blue-500 hover:border-blue-500/60 transition-colors"
      >
        ＋ 新建
      </button>
    </div>
  )
}

/** 任务树列: 有关联边(related/depends 及反查)的卡按连通分量聚合, 点击开抽屉看层级。 */
function TreeColumn({ all, onOpen }: { all: Task[]; onOpen: (id: string) => void }) {
  const [open, setOpen] = useState<Task[] | null>(null)
  const groups = treeGroups(all)
  return (
    <>
      <section className="flex flex-col w-64 shrink-0 rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900/60">
        <header className="flex items-center gap-2 px-3 h-9 shrink-0">
          <span className="text-zinc-400">⧉</span>
          <span className="font-medium text-zinc-600 dark:text-zinc-300">任务树</span>
          <span className="text-xs text-zinc-500 dark:text-zinc-400">{groups.length}</span>
        </header>
        <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2 min-h-24">
          {groups.map(g => (
            <button
              key={g[0].id}
              onClick={() => setOpen(g)}
              className="rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 p-2 text-left hover:shadow-md transition-shadow"
            >
              <p className="text-xs font-medium line-clamp-1">
                {g[0].code && <span className="mr-1 px-1 rounded bg-zinc-100 dark:bg-zinc-700/70 text-zinc-400 text-[10px] font-mono">{g[0].code}</span>}
                {g[0].title}
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1 items-center text-[11px]">
                {g.map(m => (
                  <span key={m.id} className={`size-1.5 rounded-full ${STATUS_DOT[m.status]}`} title={`${m.title} (${m.status})`} />
                ))}
                <span className="text-zinc-400">{g.length} 张</span>
              </div>
            </button>
          ))}
          {!groups.length && (
            <div className="flex-1 grid place-items-center text-xs text-zinc-400 select-none text-center px-2">
              暂无关联任务
              <br />
              卡详情里添加 related/depends
            </div>
          )}
        </div>
      </section>
      {open && <TreeDrawer group={open} onClose={() => setOpen(null)} onOpen={onOpen} />}
    </>
  )
}

/** 连通分量: related+depends 边(含反查, 即无向) union-find; 单卡分量不进树列。 */
function treeGroups(all: Task[]): Task[][] {
  const idx = new Map(all.map((t, i) => [t.id, i]))
  const parent = all.map((_, i) => i)
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])))
  const union = (a: string, b: string) => {
    const i = idx.get(a), j = idx.get(b)
    if (i == null || j == null) return
    parent[find(i)] = find(j)
  }
  for (const t of all) {
    for (const r of [...t.related, ...t.relatesBack]) union(t.id, r)
    for (const d of [...t.depends, ...t.dependedBy]) union(t.id, d)
  }
  const groups = new Map<number, Task[]>()
  all.forEach(t => {
    const r = find(idx.get(t.id)!)
    groups.set(r, [...(groups.get(r) ?? []), t])
  })
  return [...groups.values()].filter(g => g.length > 1)
}

/** 树抽屉: depends 前置在上/后继缩进, related 根级并列; DFS 防环。 */
function TreeDrawer({ group, onClose, onOpen }: {
  group: Task[]
  onClose: () => void
  onOpen: (id: string) => void
}) {
  const byId = new Map(group.map(t => [t.id, t]))
  const ids = new Set(group.map(t => t.id))
  const seen = new Set<string>()
  const rows: { t: Task; depth: number }[] = []
  const visit = (id: string, depth: number, chain: Set<string>) => {
    if (seen.has(id) || chain.has(id)) return // seen=已摆过; chain=环防御
    const t = byId.get(id)
    if (!t) return
    seen.add(id)
    rows.push({ t, depth })
    for (const succ of t.dependedBy) {
      if (ids.has(succ)) visit(succ, depth + 1, new Set([...chain, id]))
    }
  }
  // 根 = 组内无未消化 depends 的卡(或只剩环), related 关系的两端都从根级出发
  const roots = group.filter(t => !t.depends.some(d => ids.has(d) && byId.get(d)!.status !== "完成"))
  const starters = roots.length ? roots : group
  for (const r of starters) if (!seen.has(r.id)) visit(r.id, 0, new Set())
  for (const t of group) visit(t.id, 0, new Set()) // 兜底: 环/孤端也显示

  return (
    <>
      <div className="fixed inset-0 z-30" onClick={onClose} />
      <aside className="fixed right-0 top-0 bottom-0 z-40 w-[480px] max-w-[92vw] bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-700 shadow-2xl flex flex-col tm-pop">
        <header className="flex items-center gap-2 px-4 h-12 border-b border-zinc-200 dark:border-zinc-800 shrink-0">
          <span className="text-zinc-400">⧉</span>
          <span className="font-medium truncate">任务树 · {group.length} 张</span>
          <button onClick={onClose} className="ml-auto text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 px-2">✕</button>
        </header>
        <ul className="flex-1 overflow-y-auto p-3 space-y-1">
          {rows.map(({ t, depth }) => (
            <li key={t.id} style={{ paddingLeft: depth * 18 }} className={depth ? "border-l border-zinc-200 dark:border-zinc-700 ml-2" : ""}>
              <button
                onClick={() => onOpen(t.id)}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800/60 text-left"
              >
                <span className={`size-2 rounded-full shrink-0 ${STATUS_DOT[t.status]}`} />
                {t.code && <span className="px-1 rounded bg-zinc-100 dark:bg-zinc-700/70 text-zinc-400 text-[10px] font-mono shrink-0">{t.code}</span>}
                <span className="flex-1 truncate text-sm">{t.title}</span>
                {t.owner && <span className="text-[11px] text-blue-600 dark:text-blue-400 shrink-0">{t.owner}</span>}
                <span className="text-[11px] text-zinc-400 shrink-0">{t.status}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
    </>
  )
}
