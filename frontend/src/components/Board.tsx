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
}: {
  tasks: Task[]
  done: Task[]
  onOpen: (id: string) => void
  onMove: (id: string, status: string, beforeId: string | null) => void
  onNew: () => void
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
          />
        ))}
      </div>
      <DragOverlay>{active && <Card task={active} overlay />}</DragOverlay>
    </DndContext>
  )
}

function Column({
  status,
  tasks,
  onOpen,
  onNew,
}: {
  status: Status
  tasks: Task[]
  onOpen: (id: string) => void
  onNew?: () => void
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
            <Card key={t.id} task={t} onOpen={onOpen} />
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
