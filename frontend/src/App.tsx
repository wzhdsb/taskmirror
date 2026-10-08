import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { api, sse } from "./api"
import Board from "./components/Board"
import CardDrawer from "./components/CardDrawer"
import Monitor from "./components/Monitor"
import NewTaskDialog from "./components/NewTaskDialog"
import { evText, safeParse, STATUS_ORD, type BoardData, type Task } from "./types"

interface Filter {
  q: string
  owner: string
  label: string
}

export default function App() {
  const [board, setBoard] = useState<BoardData>({ tasks: [], done: [], rev: 0 })
  const [filter, setFilter] = useState<Filter>({ q: "", owner: "", label: "" })
  const [openId, setOpenId] = useState<string | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [tab, setTab] = useState<"board" | "monitor">("board")
  const [toastMsg, setToastMsg] = useState("")
  const [dark, setDark] = useState(() => localStorage.getItem("tm_theme") !== "light")
  const [owner, setOwner] = useState(() => localStorage.getItem("tm_owner") || "me")
  const [rev, setRev] = useState(0)
  const toastTimer = useRef<number>(0)
  const [flash, setFlash] = useState<Record<string, "new" | "upd">>({})
  const [acts, setActs] = useState<{ key: number; text: string }[]>([])
  const seen = useRef<Map<string, string>>(new Map()) // id -> updated 首见值, diff 出远端变更
  const lastEv = useRef(-1) // 已见最大事件 id, 首次加载静默
  const actKey = useRef(0)
  const flashTimer = useRef(0)

  const toast = useCallback((m: string) => {
    setToastMsg(m)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToastMsg(""), 2600)
  }, [])

  const pushAct = useCallback((text: string) => {
    const key = ++actKey.current
    setActs(a => [...a.slice(-3), { key, text }])
    window.setTimeout(() => setActs(a => a.filter(x => x.key !== key)), 4200)
  }, [])

  const refresh = useCallback(() => {
    Promise.all([api.board(), api.recent(30)]).then(([b, r]) => {
      setBoard(b)
      setRev(x => x + 1)
      // 远端变更可见: 新卡绿描边+滑入, 更新卡蓝描边渐隐
      const prev = seen.current
      const f: Record<string, "new" | "upd"> = {}
      if (prev.size)
        for (const t of b.tasks) {
          const was = prev.get(t.id)
          if (was === undefined) f[t.id] = "new"
          else if (was !== t.updated) f[t.id] = "upd"
        }
      prev.clear()
      for (const t of b.tasks) prev.set(t.id, t.updated)
      if (Object.keys(f).length) {
        setFlash(f)
        window.clearTimeout(flashTimer.current)
        flashTimer.current = window.setTimeout(() => setFlash({}), 2400)
      }
      // 活动通知: 新事件右上角浮现("xx 认领了「yy」")
      const fresh = (lastEv.current >= 0 ? r.events.filter(e => e.id > lastEv.current) : [])
        .sort((a, b2) => a.id - b2.id)
      for (const e of fresh.slice(-3)) {
        const d = safeParse(e.detail)
        const title = e.title || (d.title as string | undefined) || ""
        pushAct(`${e.actor} ${evText({ ...e, detail: d } as never)}${title ? ` 「${title}」` : ""}`)
      }
      if (fresh.length > 3) pushAct(`…另有 ${fresh.length - 3} 条新动态`)
      lastEv.current = Math.max(lastEv.current, ...r.events.map(e => e.id), 0)
    }).catch(e => toast(e.message))
  }, [toast, pushAct])

  useEffect(() => {
    refresh()
    const off = sse(refresh) // EventSource 自带断线重连
    const onVis = () => {
      if (document.visibilityState === "visible") refresh()
    }
    document.addEventListener("visibilitychange", onVis)
    return () => {
      off()
      document.removeEventListener("visibilitychange", onVis)
    }
  }, [refresh])

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark)
    localStorage.setItem("tm_theme", dark ? "dark" : "light")
  }, [dark])

  const setOwnerSaved = (o: string) => {
    const v = o.trim().slice(0, 40)
    if (v) {
      setOwner(v)
      localStorage.setItem("tm_owner", v)
    }
  }

  const vis = useMemo(() => {
    const q = filter.q.trim().toLowerCase()
    return (t: Task) =>
      (!q || `${t.title} ${t.id} ${t.owner} ${t.labels.join(" ")}`.toLowerCase().includes(q)) &&
      (!filter.owner || t.owner === filter.owner) &&
      (!filter.label || t.labels.includes(filter.label))
  }, [filter])

  const tasks = board.tasks.filter(vis)
  const done = board.done.filter(vis)
  const owners = [...new Set(board.tasks.map(t => t.owner).filter(Boolean))]
  const labels = [...new Set(board.tasks.flatMap(t => t.labels))]

  // 乐观移动, 失败回滚(重拉)
  const onMove = useCallback((id: string, status: string, beforeId: string | null) => {
    setBoard(b => {
      const all = [...b.tasks]
      const i = all.findIndex(t => t.id === id)
      if (i < 0) return b
      const [t] = all.splice(i, 1)
      const nt = { ...t, status: status as Task["status"] }
      const j = beforeId ? all.findIndex(x => x.id === beforeId) : -1
      if (j >= 0) all.splice(j, 0, nt)
      else {
        // 列尾 = 目标状态第一张之前 / 全体末尾
        let k = all.length
        for (let x = 0; x < all.length; x++)
          if (STATUS_ORD[all[x].status] > STATUS_ORD[nt.status]) {
            k = x
            break
          }
        all.splice(k, 0, nt)
      }
      return { ...b, tasks: all }
    })
    api.move(id, status, beforeId).catch(e => {
      toast(e.message)
      refresh()
    })
  }, [toast, refresh])

  const onDispatch = useCallback((id: string) => {
    api.dispatch(id)
      .then(r => toast(`已派发 AI（进程 ${r.pid}），认领后卡片会自动更新`))
      .catch(e => toast(e.message))
  }, [toast])

  const sel = "h-7 px-2 rounded-md bg-zinc-100 dark:bg-zinc-800 border border-transparent focus:border-blue-500 outline-none text-xs max-w-28"

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <header className="flex items-center gap-2 px-4 h-12 border-b border-zinc-200 dark:border-zinc-700/60 bg-white/80 dark:bg-zinc-900/80 backdrop-blur shrink-0">
        <span className="font-semibold tracking-tight select-none">TaskMirror</span>
        <nav className="flex items-center gap-0.5 ml-2">
          {(["board", "monitor"] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`h-7 px-2.5 rounded-md text-xs transition-colors ${
                tab === t
                  ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900 font-medium"
                  : "text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              }`}
            >
              {t === "board" ? "看板" : "监控"}
            </button>
          ))}
        </nav>
        <input
          placeholder="搜索任务…"
          value={filter.q}
          onChange={e => setFilter(f => ({ ...f, q: e.target.value }))}
          className="w-60 h-7 px-2.5 rounded-md bg-zinc-100 dark:bg-zinc-800 border border-transparent focus:border-blue-500 outline-none placeholder:text-zinc-500 dark:placeholder:text-zinc-400"
        />
        {owners.length > 0 && (
          <select className={sel} value={filter.owner} onChange={e => setFilter(f => ({ ...f, owner: e.target.value }))}>
            <option value="">全部执行者</option>
            {owners.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        )}
        {labels.length > 0 && (
          <select className={sel} value={filter.label} onChange={e => setFilter(f => ({ ...f, label: e.target.value }))}>
            <option value="">全部标签</option>
            {labels.map(l => <option key={l} value={l}>{l}</option>)}
          </select>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <OwnerBadge owner={owner} onSave={setOwnerSaved} />
          <button
            onClick={() => setDark(d => !d)}
            title="切换主题"
            className="h-7 w-7 rounded-md hover:bg-zinc-100 dark:hover:bg-zinc-800 text-xs"
          >
            {dark ? "☀️" : "🌙"}
          </button>
          <button
            onClick={() => setShowNew(true)}
            className="h-7 px-3 rounded-md bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium"
          >
            + 新建任务
          </button>
        </div>
      </header>

      {tab === "board" ? (
        <Board tasks={tasks} done={done} onOpen={setOpenId} onMove={onMove} onNew={() => setShowNew(true)}
          flash={flash} onDispatch={onDispatch} />
      ) : (
        <Monitor rev={rev} onOpen={setOpenId} />
      )}

      {openId && (
        <CardDrawer id={openId} rev={rev} owner={owner} onClose={() => setOpenId(null)} onChanged={refresh} toast={toast} />
      )}
      {showNew && <NewTaskDialog onClose={() => setShowNew(false)} onCreated={refresh} toast={toast} />}
      {toastMsg && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-[60] px-3.5 h-8 flex items-center rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 text-xs shadow-lg">
          {toastMsg}
        </div>
      )}
      {acts.length > 0 && (
        <div className="fixed top-14 right-4 z-[60] flex flex-col items-end gap-1.5 pointer-events-none">
          {acts.map(a => (
            <div key={a.key} className="tm-toast max-w-96 truncate px-3 h-8 flex items-center rounded-md bg-zinc-900/90 dark:bg-zinc-100/95 text-white dark:text-zinc-900 text-xs shadow-lg">
              {a.text}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function OwnerBadge({ owner, onSave }: { owner: string; onSave: (s: string) => void }) {
  const [edit, setEdit] = useState(false)
  const [v, setV] = useState(owner)
  useEffect(() => setV(owner), [owner])
  const ok = () => {
    onSave(v)
    setEdit(false)
  }
  return edit ? (
    <input
      autoFocus
      value={v}
      onChange={e => setV(e.target.value)}
      onBlur={ok}
      onKeyDown={e => e.key === "Enter" && ok()}
      className="w-20 h-7 px-2 rounded-md bg-zinc-100 dark:bg-zinc-800 border border-blue-500 outline-none text-xs"
    />
  ) : (
    <button
      onClick={() => setEdit(true)}
      title="点击修改本机执行者名（认领/日志署名用）"
      className="h-7 px-2.5 rounded-md text-xs text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
    >
      执行者 <b className="text-zinc-800 dark:text-zinc-200">{owner}</b>
    </button>
  )
}
