# 泳道改版 DOM 几何断言: 列对齐/占位文字/完成截断/高度对比(一次性验证脚本)
import json
from playwright.sync_api import sync_playwright

JS = """() => {
  const r = {}
  r.placeholder = document.body.innerText.includes('拖卡到这里')
  const xs = el => [...el.children].map(c => Math.round(c.getBoundingClientRect().x))
  r.headerX = xs(document.querySelector('.sticky .grid'))
  const lanes = [...document.querySelectorAll('section .grid')]
  r.laneXs = lanes.slice(0, 3).map(xs)
  const heights = [...document.querySelectorAll('section .grid')].map((g, i) => {
    const c = g.children[3] // 完成列
    return { lane: i, cards: c.querySelectorAll('[data-id]').length,
             more: c.innerText.includes('还有') ? 1 : 0,
             h: Math.round(c.getBoundingClientRect().height) }
  })
  r.done = heights
  r.lanes = [...document.querySelectorAll('section header')].map(h => h.innerText.split('\\n')[0].slice(0, 30))
  r.boardH = document.querySelector('.grid').closest('.flex.flex-col').scrollHeight
  return r
}"""

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 900})
    pg.goto("http://127.0.0.1:8801/", wait_until="networkidle")
    pg.wait_for_timeout(900)
    all_view = pg.evaluate(JS)
    print("== 全部项目 ==")
    print(json.dumps(all_view, ensure_ascii=False, indent=1))

    pg.select_option("header select >> nth=0", "taskmirror")
    pg.wait_for_timeout(600)
    tm_view = pg.evaluate(JS)
    print("== 单选 taskmirror ==")
    print(json.dumps(tm_view, ensure_ascii=False, indent=1))
    b.close()
