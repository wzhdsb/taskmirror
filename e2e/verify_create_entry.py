# 验收: 新建入口唯一化——格子背景点击不弹新建框, 虚线「＋新建」卡点击才弹(带项目·列徽章)
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 900})
    pg.goto("http://127.0.0.1:8801/", wait_until="networkidle")
    pg.wait_for_timeout(900)
    # 1) 点空格子背景(直接 dispatch 到格子元素): 不应弹窗
    pg.evaluate("document.querySelectorAll('section .grid')[0].children[2].dispatchEvent(new MouseEvent('click', {bubbles: true}))")
    pg.wait_for_timeout(300)
    bg = pg.locator('input[placeholder="标题（回车直接创建）"]').count()
    # 2) 点虚线卡: 应弹窗且带徽章
    pg.locator("section").filter(has_text="taskmirror").first.locator("button:has-text('＋ 新建')").first.click()
    pg.wait_for_timeout(300)
    dash = pg.locator('input[placeholder="标题（回车直接创建）"]').count()
    chip = pg.locator("h3 span").first.text_content() if dash else ""
    pg.keyboard.press("Escape")
    print(f"背景点击弹窗: {bg} (期望0)  虚线卡点击弹窗: {dash} (期望1)  徽章: {chip}")
    b.close()
    assert bg == 0 and dash == 1, "FAIL"
