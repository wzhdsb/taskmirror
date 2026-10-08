"""TaskMirror e2e (Playwright sync, weibo_monitor env)。
自起 8802 服务(TASKBOARD_HOME=临时目录, 不碰真实数据) → 全流程冒烟 → 截图 e2e/shot.png。
用法: python e2e/e2e.py  (需先 python dev.py build)
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8802"
PY = os.environ.get("TM_E2E_PY", "D:/Anaconda/python.exe")  # 服务端用 base env(装了依赖); e2e 本身用 weibo_monitor 的 playwright

home = tempfile.mkdtemp(prefix="tm-e2e-")
server = subprocess.Popen(
    [PY, "-m", "app.main"], cwd=str(ROOT / "backend"),
    env={**os.environ, "PYTHONUTF8": "1", "TASKBOARD_PORT": "8802", "TASKBOARD_HOME": home},
)
try:
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen(BASE + "/api/meta", timeout=1)
            break
        except OSError:
            time.sleep(0.2)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 860})
        pg.goto(BASE)

        # 四列就位
        for col in ("待执行", "执行中", "待验收", "完成"):
            expect(pg.get_by_text(col, exact=True)).to_be_visible()

        # 新建卡
        pg.on("dialog", lambda d: d.accept())
        pg.click("text=+ 新建任务")
        pg.get_by_placeholder("标题（回车直接创建）").fill("e2e 冒烟卡")
        pg.locator("textarea").fill("## ① 任务\n\n走一遍全流程")
        pg.get_by_placeholder("标签,逗号分隔").fill("e2e")
        pg.get_by_placeholder("标题（回车直接创建）").press("Enter")  # 标题框 Enter 直接创建
        card = pg.locator("text=e2e 冒烟卡").first
        expect(card).to_be_visible()

        # 打开抽屉 → 认领
        card.click()
        expect(pg.get_by_text("进展日志")).to_be_visible()
        pg.click("text=认领")
        expect(pg.locator("text=@me").first).to_be_visible()

        # 加日志
        pg.fill("input[placeholder*='追加日志']", "e2e 第一步")
        pg.keyboard.press("Enter")
        expect(pg.get_by_text("e2e 第一步").first).to_be_visible()

        # 编辑正文
        pg.click("text=编辑")
        pg.fill("textarea", "## ① 任务\n\n正文被 e2e 改过")
        pg.click("text=保存")
        expect(pg.get_by_text("正文被 e2e 改过")).to_be_visible()
        pg.keyboard.press("Escape")  # Esc 关抽屉

        # 跨列移动走状态 pills(与拖拽同一条 /move 代码路径)
        pg.locator("text=e2e 冒烟卡").first.click()
        pg.locator("button", has_text="执行中").first.click()
        pg.keyboard.press("Escape")
        expect(pg.locator("section", has=pg.get_by_text("执行中", exact=True)).locator("text=e2e 冒烟卡")).to_be_visible()

        # SSE 远端更新: 绕过 UI 直接打 API, 页面应自动出现新日志
        pg.locator("text=e2e 冒烟卡").first.click()
        cid = pg.get_by_text("· 建").first.inner_text().split(" ·")[0].strip()
        pg.evaluate(
            """id => fetch(`/api/tasks/${encodeURIComponent(id)}/log`, {method:'POST',
               headers:{'Content-Type':'application/json'}, body: JSON.stringify({text:'SSE 远端日志', actor:'外部'})})""",
            cid,
        )
        expect(pg.get_by_text("SSE 远端日志").first).to_be_visible(timeout=8000)

        # 状态 pills: 完成 → 恢复 → 删除(confirm 由 dialog handler 自动接受)
        pg.click("button:has-text('完成')")
        expect(pg.get_by_text("恢复")).to_be_visible()
        pg.click("text=恢复")
        pg.click("button:has-text('完成')")
        pg.click("text=恢复")
        pg.click("text=删除")
        time.sleep(0.5)
        assert "e2e 冒烟卡" not in pg.content()

        # 暗色切换 + 重建两张卡后截图
        pg.click("text=+ 新建任务")
        pg.get_by_placeholder("标题（回车直接创建）").fill("TaskMirror 产品化重构")
        pg.fill("textarea", "## ① 任务\n\nSQLite 真源 + React 看板 + MCP 调度闭环")
        pg.get_by_placeholder("标签,逗号分隔").fill("重构,调度")
        pg.get_by_placeholder("标题（回车直接创建）").press("Enter")

        # 监控页: 概览/负载/建议/事件流 (先认领新卡让负载有数据)
        pg.locator("text=TaskMirror 产品化重构").first.click()
        pg.click("text=认领")
        pg.keyboard.press("Escape")
        pg.click("text=监控")
        expect(pg.get_by_text("执行者负载")).to_be_visible()
        expect(pg.get_by_text("概览")).to_be_visible()
        expect(pg.locator("li", has_text="状态 待执行→执行中").first).to_be_visible()
        expect(pg.locator("li", has_text="在手 1").first).to_be_visible()
        pg.screenshot(path=str(ROOT / "e2e" / "monitor-dark.png"))
        pg.click("text=看板")

        pg.click("text=☀️")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(ROOT / "e2e" / "board-light.png"))
        pg.click("text=🌙")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(ROOT / "e2e" / "board-dark.png"), full_page=False)
        browser.close()
    print("E2E OK")
finally:
    server.terminate()
