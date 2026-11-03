"""陪练打磨的前端 UI 验证（Playwright 真机 · 只读，不写库）。

验的是「这些入口真的在页面上、点了真的有反应」：
P7 趋势卡图例 · P5 成绩单「重答这题」→ 重答态 · P3 重答态下的「给点提示」
⚠️ 只点「重答这题」与「取消」，**不提交**，因此不会产生任何写库请求。
"""
import sys
from playwright.sync_api import sync_playwright, expect

CHROME = r"C:\Users\陈锦祥\AppData\Local\ms-playwright\chromium-1223\chrome-win64\chrome.exe"
URL = "http://127.0.0.1:5173/drill"

PASS, FAIL = [], []


def chk(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail and not cond else ""))


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL, wait_until="domcontentloaded")

    # 列表加载完成：等到卡片或空态出现（不能带空态进等待条件，否则立刻满足）
    pg.wait_for_selector(".session-card, .el-empty", timeout=30000)
    cards = pg.query_selector_all(".session-card")
    chk("列表渲染出历史场次", len(cards) > 0, f"cards={len(cards)}")

    # ---- P7 趋势卡图例 ----
    legend = pg.query_selector(".td-legend")
    if pg.query_selector(".td-card"):
        chk("P7 趋势卡出现图例", legend is not None)
        if legend:
            txt = legend.inner_text()
            chk("P7 图例同时标注「有评分 / 未评分」", "有评分" in txt and "未评分" in txt, txt)
            hollow = pg.query_selector(".lg-swatch.hollow")
            chk("P7 空心色块存在", hollow is not None)
    else:
        chk("P7 趋势卡存在（场次 ≥3）", False, "没找到 .td-card")

    # ---- 进一个「已完成」场次 ----
    target = None
    for c in cards:
        st = c.query_selector(".sc-status")
        if st and "已完成" in st.inner_text():
            target = c
            break
    chk("找到已完成场次", target is not None)
    if target is None:
        b.close()
        sys.exit(1)
    target.click()
    pg.wait_for_selector(".rs-hero", timeout=20000)
    chk("成绩单渲染", pg.query_selector(".rs-hero") is not None)

    # ---- P5 「重答这题」入口 ----
    retry_btns = pg.query_selector_all("text=重答这题")
    chk("P5 成绩单出现「重答这题」入口", len(retry_btns) > 0, f"count={len(retry_btns)}")
    if retry_btns:
        retry_btns[0].click()
        pg.wait_for_selector(".answer-box", timeout=15000)
        chk("P5 点击后进入重答态（作答框出现）", pg.query_selector(".answer-box") is not None)
        bar = pg.query_selector(".retry-bar")
        chk("P5 重答态有顶部说明条", bar is not None)
        if bar:
            chk("P5 说明条讲清「只重算这一题」", "只重算这一题" in bar.inner_text(), bar.inner_text()[:60])
        btn_txt = pg.inner_text(".answer-actions .el-button--primary")
        chk("P5 主按钮文案变为「重新评分」", "重新评分" in btn_txt, btn_txt)

        # ---- P3 重答态的「给点提示」 ----
        hint_btn = pg.query_selector("text=给点提示")
        if hint_btn:
            hint_btn.click()
            pg.wait_for_selector(".hint-bar", timeout=5000)
            hb = pg.inner_text(".hint-bar")
            chk("P3 提示条展开且有内容", len(hb.strip()) > 6, hb[:60])
            chk("P3 提示条标明「不含答案」", "不含答案" in hb, hb[:60])
        else:
            # 该考点没有 hint 时不出现按钮 —— 这本身是设计内行为，记 INFO 不记 FAIL
            print("INFO 该考点无 hint，「给点提示」按钮按设计不出现")

        # 取消 → 回成绩单
        pg.click(".retry-bar .el-button")
        pg.wait_for_selector(".rs-hero", timeout=10000)
        chk("P5 取消后回到成绩单", pg.query_selector(".retry-bar") is None)

    pg.screenshot(path="_ui_drill_polish_result.png", full_page=False)
    chk("控制台无 JS 报错", len([e for e in errs if "favicon" not in e]) == 0, errs[:2])
    b.close()

print(f"\n==== {len(PASS)} PASS / {len(FAIL)} FAIL ====")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
