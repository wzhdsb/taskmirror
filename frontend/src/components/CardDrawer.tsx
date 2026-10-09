import { useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { api } from "../api"
import { COLORS, STATUSES, STATUS_BAR, STATUS_DOT, evText, type Detail, type Task } from "../types"

export default function CardDrawer({
  id,
  rev,
  owner,
  all,
  onOpen,
  onClose,
  onChanged,
  toast,
}: {
  id: string
  rev: number
  owner: string
  all: Task[]
  onOpen: (id: string) => void
  onClose: () => void
  onChanged: () => void
  toast: (m: string) => void
}) {
  const [d, setD] = useState<Detail | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState("")
  const [titleEdit, setTitleEdit] = useState(false)
  const [titleDraft, setTitleDraft] = useState("")
  const [labelsEdit, setLabelsEdit] = useState(false)
  const [labelsDraft, setLabelsDraft] = useState("")
  const [logText, setLogText] = useState("")
  const [relTarget, setRelTarget] = useState("")
  const [relKind, setRelKind] = useState("related")

  useEffect(() => {
    setD(null)
    setEditing(false)
    api.task(id).then(setD).catch(e => toast(e.message))
  }, [id, toast])

  // 远端更新(SSE→rev)时重拉; 正在编辑正文/标题/标签时不覆盖
  useEffect(() => {
    if (d && !editing && !titleEdit && !labelsEdit) api.task(id).then(setD).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rev])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  if (!d)
    return (
      <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose}>
        <div className="absolute right-0 top-0 h-full w-[30rem] max-w-[92vw] bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-800" />
      </div>
    )

  const t = d.task
  const save = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      setD(await api.task(id))
      onChanged()
    } catch (e) {
      toast((e as Error).message)
    }
  }
  const input = "h-7 px-2 rounded-md bg-zinc-100 dark:bg-zinc-800 border border-transparent focus:border-blue-500 outline-none text-xs"

  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="absolute right-0 top-0 h-full w-[30rem] max-w-[92vw] bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-800 shadow-2xl flex flex-col drawer-in">
        {/* 状态 pills + 标题 */}
        <div className="px-4 pt-3 pb-2.5 border-b border-zinc-200 dark:border-zinc-800 shrink-0">
          <div className="flex items-center gap-1.5">
            {STATUSES.map(s => (
              <button
                key={s}
                onClick={() => save(() => api.move(id, s, null))}
                className={`h-6 px-2.5 rounded-full text-xs border transition-colors ${
                  t.status === s
                    ? "border-transparent text-white"
                    : "border-zinc-200 dark:border-zinc-700 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                }`}
                style={t.status === s ? { background: STATUS_BAR[s] } : undefined}
              >
                {s}
              </button>
            ))}
            <button onClick={onClose} title="关闭 (Esc)" className="ml-auto h-6 w-6 rounded-md hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-400">
              ✕
            </button>
            {t.status === "完成" && (
              <span title="已归档: 点状态 pill 或拖回前四列即可重开" className="ml-1 h-6 px-2 inline-flex items-center rounded-full bg-zinc-100 dark:bg-zinc-800 text-[11px] text-zinc-500 shrink-0">
                已归档
              </span>
            )}
          </div>
          {titleEdit ? (
            <input
              autoFocus
              value={titleDraft}
              onChange={e => setTitleDraft(e.target.value)}
              onBlur={() => {
                const v = titleDraft.trim()
                setTitleEdit(false)
                if (v && v !== t.title) save(() => api.patch(id, { title: v }))
              }}
              onKeyDown={e => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
              className="mt-2 w-full text-base font-semibold bg-transparent border-b border-blue-500 outline-none"
            />
          ) : (
            <h2
              className="mt-2 text-base font-semibold leading-snug break-all rounded px-1 -mx-1 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 cursor-text"
              title="点击改标题"
              onClick={() => {
                setTitleDraft(t.title)
                setTitleEdit(true)
              }}
            >
              {t.title}
            </h2>
          )}
          <div className="mt-1 text-[11px] text-zinc-400 truncate">
            {t.id}{t.code ? ` · 代号 ${t.code}` : ""} · 建 {t.created.slice(5, 16)} · 更 {t.updated.slice(5, 16)}
          </div>
        </div>

        {/* meta 行 */}
        <div className="px-4 py-2.5 border-b border-zinc-200 dark:border-zinc-800 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs shrink-0">
          {t.owner ? (
            <span className="px-2 h-6 inline-flex items-center rounded bg-blue-500/10 text-blue-600 dark:text-blue-400">
              @{t.owner}
            </span>
          ) : (
            <button
              onClick={() =>
                save(async () => {
                  await api.patch(id, { owner })
                  if (t.status === "待执行") await api.move(id, "执行中", null) // 与 MCP take_task 同语义
                })
              }
              className="h-6 px-2 rounded-md bg-blue-600 hover:bg-blue-500 text-white"
            >
              认领
            </button>
          )}
          <button
            onClick={() => save(() => api.patch(id, { starred: t.starred ? 0 : 1 }))}
            title="星标=下一批要做的, 列内自动置顶"
            className={`h-7 px-2 rounded-md text-xs border transition-colors ${
              t.starred
                ? "border-amber-400 bg-amber-500/15 text-amber-600 dark:text-amber-400"
                : "border-zinc-200 dark:border-zinc-700 text-zinc-400 hover:text-amber-500"
            }`}
          >
            ★ {t.starred ? "已星标" : "星标"}
          </button>
          <input
            key={t.updated}
            defaultValue={t.project}
            list="tm-projects"
            placeholder="项目(泳道)"
            onBlur={e => {
              const v = e.target.value.trim()
              if (v !== t.project) save(() => api.patch(id, { project: v }))
            }}
            onKeyDown={e => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            className={input + " w-32"}
            title="所属项目=泳道分组, 清空归未分组"
          />
          <datalist id="tm-projects">
            {[...new Set(all.map(x => x.project).filter(Boolean))].map(p => (
              <option key={p} value={p} />
            ))}
          </datalist>
          <input type="date" className={input} value={t.due || ""} onChange={e => save(() => api.patch(id, { due: e.target.value || "" }))} />
          <div className="flex items-center gap-1 ml-auto">
            {COLORS.map(c => (
              <button
                key={c}
                onClick={() => save(() => api.patch(id, { color: t.color === c ? "" : c }))}
                className={`size-3.5 rounded-full hover:scale-125 transition-transform ${t.color === c ? "ring-2 ring-blue-500 ring-offset-1 dark:ring-offset-zinc-900" : ""}`}
                style={{ background: c }}
                title="标签色"
              />
            ))}
          </div>
          {labelsEdit ? (
            <input
              autoFocus
              placeholder="标签, 逗号分隔, 回车保存"
              value={labelsDraft}
              onChange={e => setLabelsDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") {
                  setLabelsEdit(false)
                  const ls = labelsDraft.split(",").map(s => s.trim()).filter(Boolean)
                  if (ls.join(",") !== t.labels.join(",")) save(() => api.patch(id, { labels: ls }))
                }
              }}
              onBlur={() => setLabelsEdit(false)}
              className={input + " w-44"}
            />
          ) : (
            <button
              onClick={() => {
                setLabelsDraft(t.labels.join(","))
                setLabelsEdit(true)
              }}
              className="flex flex-wrap items-center gap-1"
            >
              {t.labels.length ? (
                t.labels.map(l => (
                  <span key={l} className="px-1.5 h-5 inline-flex items-center rounded-full border border-zinc-200 dark:border-zinc-700 text-zinc-500">
                    {l}
                  </span>
                ))
              ) : (
                <span className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300">+ 标签</span>
              )}
            </button>
          )}
        </div>

        {/* 关联区: 单向声明的边 own 行可移除, 反查边只展示 */}
        <div className="px-4 py-2.5 border-b border-zinc-200 dark:border-zinc-800 shrink-0">
          <div className="text-xs font-medium text-zinc-400 mb-1.5">关联</div>
          <ul className="space-y-1">
            {[
              ...(t.related ?? []).map(x => ({ id: x, icon: "⧉", note: "配套", own: true, kind: "related" })),
              ...(t.depends ?? []).map(x => ({ id: x, icon: "⏳", note: "前置", own: true, kind: "depends" })),
              ...(t.relatesBack ?? []).map(x => ({ id: x, icon: "◂", note: "被配套", own: false, kind: "related" })),
              ...(t.dependedBy ?? []).map(x => ({ id: x, icon: "▾", note: "后继", own: false, kind: "depends" })),
            ].map(r => {
              const m = all.find(x => x.id === r.id)
              return (
                <li key={`${r.kind}-${r.id}`} className="flex items-center gap-2 text-xs">
                  <span>{r.icon}</span>
                  <span className={`size-1.5 rounded-full shrink-0 ${m ? STATUS_DOT[m.status] : "bg-zinc-300"}`} />
                  <button
                    onClick={() => onOpen(r.id)}
                    className="flex-1 text-left truncate text-blue-600 dark:text-blue-400 hover:underline"
                    title={`${r.id}${r.own ? "" : " (对方声明)"}`}
                  >
                    {m?.title ?? r.id}
                  </button>
                  <span className="text-zinc-400 shrink-0">{r.note}</span>
                  {r.own && t.status !== "完成" && (
                    <button
                      title="移除关联"
                      onClick={() => save(() => api.relate(id, r.id, r.kind, true))}
                      className="text-zinc-400 hover:text-red-500 px-1 shrink-0"
                    >
                      ×
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
          {t.status !== "完成" && (
            <div className="mt-1.5 flex gap-1.5">
              <input
                list="tm-rel-targets"
                value={relTarget}
                onChange={e => setRelTarget(e.target.value)}
                placeholder="目标卡 id / 标题关键词"
                className={input + " flex-1"}
              />
              <datalist id="tm-rel-targets">
                {all.filter(x => x.id !== id).map(x => (
                  <option key={x.id} value={x.id}>{x.title}</option>
                ))}
              </datalist>
              <select className={input} value={relKind} onChange={e => setRelKind(e.target.value)}>
                <option value="related">配套 ⧉</option>
                <option value="depends">前置 ⏳</option>
              </select>
              <button
                onClick={() => {
                  const target = relTarget.trim() || (all.find(x => x.title === relTarget)?.id ?? "")
                  if (target) {
                    setRelTarget("")
                    save(() => api.relate(id, target, relKind))
                  }
                }}
                className="h-7 px-2 rounded-md bg-blue-600 hover:bg-blue-500 text-white text-xs shrink-0"
              >
                添加
              </button>
            </div>
          )}
        </div>

        {/* 正文 + 日志 */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs font-medium text-zinc-400">正文</span>
            {editing ? (
              <span className="flex gap-2 text-xs">
                <button className="text-blue-600 dark:text-blue-400" onClick={() => {
                  setEditing(false)
                  if (draft !== d.body) save(() => api.patch(id, { body: draft }))
                }}>
                  保存
                </button>
                <button className="text-zinc-400" onClick={() => setEditing(false)}>取消</button>
              </span>
            ) : (
              <button className="text-xs text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300" onClick={() => {
                setDraft(d.body)
                setEditing(true)
              }}>
                编辑
              </button>
            )}
          </div>
          {editing ? (
            <textarea
              autoFocus
              value={draft}
              onChange={e => setDraft(e.target.value)}
              className="w-full h-72 font-mono text-xs p-2.5 rounded-md bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 outline-none focus:border-blue-500 resize-y"
            />
          ) : (
            <div className="md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{d.body || "*（空正文）*"}</ReactMarkdown>
            </div>
          )}

          <div className="mt-6 mb-2 text-xs font-medium text-zinc-400">进展日志</div>
          <ol className="space-y-1.5">
            {d.events.map(e => (
              <li key={e.id} className="flex gap-2 text-xs leading-relaxed">
                <span className="text-zinc-400 shrink-0 tabular-nums">{e.ts.slice(5, 16)}</span>
                <span className="shrink-0 text-blue-600 dark:text-blue-400">[{e.actor}]</span>
                <span className="text-zinc-600 dark:text-zinc-300 break-all">{evText(e)}</span>
              </li>
            ))}
          </ol>
        </div>

        {/* 底部: 追加日志 + 动作 */}
        <div className="border-t border-zinc-200 dark:border-zinc-800 p-3 space-y-2 shrink-0">
          <input
            placeholder="追加日志, Enter 提交"
            value={logText}
            onChange={e => setLogText(e.target.value)}
            onKeyDown={e => {
              const s = logText.trim()
              if (e.key === "Enter" && s) {
                setLogText("")
                save(() => api.log(id, s, owner))
              }
            }}
            className={input + " w-full h-8"}
          />
          <div className="flex gap-2 justify-end">
            {t.status === "完成" && (
              <button onClick={() => save(() => api.restore(id))} className="h-7 px-3 rounded-md text-xs border border-zinc-200 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800">
                恢复
              </button>
            )}
            <button
              onClick={() => {
                if (confirm(`删除「${t.title}」? 镜像文件会留档 trash/`)) {
                  api.remove(id).then(() => {
                    onChanged()
                    onClose()
                  }).catch(e => toast(e.message))
                }
              }}
              className="h-7 px-3 rounded-md text-xs text-red-500 hover:bg-red-500/10"
            >
              删除
            </button>
          </div>
        </div>
      </aside>
    </div>
  )
}
