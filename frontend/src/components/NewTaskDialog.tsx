import { useEffect, useState } from "react"
import { api } from "../api"

export default function NewTaskDialog({
  onClose,
  onCreated,
  toast,
}: {
  onClose: () => void
  onCreated: () => void
  toast: (m: string) => void
}) {
  const [title, setTitle] = useState("")
  const [body, setBody] = useState("")
  const [labels, setLabels] = useState("")
  const [project, setProject] = useState("")
  const [due, setDue] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  async function submit() {
    if (!title.trim() || busy) return
    setBusy(true)
    try {
      await api.create({
        title,
        body,
        labels: labels.split(",").map(s => s.trim()).filter(Boolean),
        project: project.trim(),
        due: due || "",
      })
      onCreated()
      onClose()
    } catch (e) {
      toast((e as Error).message)
      setBusy(false)
    }
  }

  const input = "h-8 px-2.5 rounded-md bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 outline-none focus:border-blue-500 text-xs"

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh]">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative w-[34rem] max-w-[92vw] rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shadow-2xl p-4 dialog-in">
        <h3 className="font-semibold mb-3">新建任务</h3>
        <input
          autoFocus
          placeholder="标题（回车直接创建）"
          value={title}
          onChange={e => setTitle(e.target.value)}
          onKeyDown={e => e.key === "Enter" && submit()}
          className={input + " w-full"}
        />
        <textarea
          placeholder="正文 Markdown…（留空=快速卡「## 需求」，agent 接手时再整理；或直接四段式：① 任务 / ② 已知事实 / ③ 交付物 / ④ 注意）"
          value={body}
          onChange={e => setBody(e.target.value)}
          rows={6}
          className={input + " w-full mt-2.5 font-mono resize-y"}
        />
        <div className="flex gap-2 mt-2.5">
          <input placeholder="标签,逗号分隔" value={labels} onChange={e => setLabels(e.target.value)} className={input + " flex-1"} />
          <input placeholder="项目(泳道,可空)" value={project} onChange={e => setProject(e.target.value)} className={input + " w-32"} />
          <input type="date" value={due} onChange={e => setDue(e.target.value)} className={input} />
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="h-8 px-3.5 rounded-md text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800">
            取消
          </button>
          <button
            onClick={submit}
            disabled={!title.trim() || busy}
            className="h-8 px-3.5 rounded-md bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-xs font-medium"
          >
            创建
          </button>
        </div>
      </div>
    </div>
  )
}
