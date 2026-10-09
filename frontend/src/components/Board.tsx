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

export default function Board({
  tasks,
  done,
  onOpen,
  onMove,
  onNew,
  flash,
  onDispatch,
}: {
  tasks: Task[]
  done: Task[]
  onOpen: (id: string) => void
  onMove: (id: string, status: string, beforeId: string | null) => void
  onNew: () => void
  flash: Record<string, "new" | "upd">
  onDispatch: (id: string) => void
}) {
  const [activeId, setActiveId] = useState<string | null>(null)
  // 碰撞候选排除被拖卡自身, 否则空列时它离指针"最近"导致拖不动
  const collision: CollisionDetection = args =>
    closestCorners({ ...args, droppableContainers: args.droppableContainers.filter(c => c.id !== args.active.id) })
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const all = [...tasks, ...done]
  const active = all.find(t => t.id === activeId)

  function handleEnd(e: DragEndEvent) {
    setActiveId(null)
    const { active: a, over } = e
    if (!over) return
    const id = String(a.id)
    const overId = String(over.id)
    if (overId === id) return // 原地
    const toStatus = STATUSES.find(s => s === overId) ?? all.find(x => x.id === overId)?.status
    if (!toStatus) return
    if (overId === toStatus) onMove(id, toStatus, null) // 放到列体 = 列尾
    else onMove(id, toStatus, overId) // 插在被覆盖卡前面
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={collision}
      onDragStart={e => setActiveId(String(e.active.id))}
      onDragEnd={handleEnd}
      onDragCancel={() => setActiveId(null)}
    >
      <div className="flex-1 flex gap-4 p-4 overflow-x-auto">
        {STATUSES.map(s => (
          <Column
            key={s}
            status={s}
            tasks={s === "完成" ? [...tasks.filter(t => t.status === s), ...done] : tasks.filter(t => t.status === s)}
            onOpen={onOpen}
            onNew={s === "待执行" ? onNew : undefined}
            flash={flash}
            onDispatch={onDispatch}
          />
        ))}
        <TreeColumn all={all} onOpen={onOpen} />
      </div>
      <DragOverlay>{active && <Card task={active} overlay />}</DragOverlay>
    </DndContext>
  )
}

/** 任务树列: 有关联边(related/depends 及反查)的卡按连通分量聚合, 点击开抽屉看层级。 */
function TreeColumn({ all, onOpen }: { all: Task[]; onOpen: (id: string) => void }) {
  const [open, setOpen] = useState<Task[] | null>(null)
  const groups = treeGroups(all)
  return (
    <>
      <section className="flex flex-col flex-1 min-w-[14rem] max-w-[20rem] shrink-0 rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900/60">
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
              <p className="text-xs font-medium line-clamp-1">{g[0].title}</p>
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

function Column({
  status,
  tasks,
  onOpen,
  onNew,
  flash,
  onDispatch,
}: {
  status: Status
  tasks: Task[]
  onOpen: (id: string) => void
  onNew?: () => void
  flash: Record<string, "new" | "upd">
  onDispatch: (id: string) => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: status })
  return (
    <section
      ref={setNodeRef}
      className={`flex flex-col flex-1 min-w-[17rem] max-w-[24rem] shrink-0 rounded-lg border transition-colors ${
        isOver
          ? "border-blue-500/60 bg-blue-50/50 dark:bg-blue-950/20"
          : "border-zinc-200 dark:border-zinc-700/60 bg-zinc-100 dark:bg-zinc-900"
      }`}
    >
      <header className="flex items-center gap-2 px-3 h-9 shrink-0">
        <span className={`size-2 rounded-full ${STATUS_DOT[status]}`} />
        <span className="font-medium">{status}</span>
        <span className="text-xs text-zinc-500 dark:text-zinc-400">{tasks.length}</span>
      </header>
      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2 min-h-24">
        <SortableContext items={tasks.map(t => t.id)} strategy={verticalListSortingStrategy}>
          {tasks.map(t => (
            <Card key={t.id} task={t} onOpen={onOpen} flash={flash[t.id]} onDispatch={onDispatch} />
          ))}
        </SortableContext>
        {!tasks.length && onNew ? (
          <div className="flex-1 grid place-items-center">
            <button
              onClick={onNew}
              className="w-full py-6 rounded-md border border-dashed border-zinc-300 dark:border-zinc-700 text-xs text-zinc-500 hover:text-blue-500 hover:border-blue-500/60 transition-colors"
            >
              + 新建第一张任务卡
            </button>
          </div>
        ) : (
          !tasks.length && (
            <div className="flex-1 grid place-items-center text-xs text-zinc-400 dark:text-zinc-400 select-none">拖卡到这里</div>
          )
        )}
      </div>
    </section>
  )
}
