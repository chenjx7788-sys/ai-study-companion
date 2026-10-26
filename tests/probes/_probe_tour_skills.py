"""引导「技能」步骤验收：真浏览器逐步走查（两个配置分支都走）。

不是"能弹出来"就算过 —— 每条判据都指向一个具体的坏法：
  · 步骤数/编号：新增一步后编号必须顺延，否则用户看到两个「③」
  · 几何：popper 中心 y 必须与目标导航项中心 y 重合（实测差 0.0）
    → 若 target 写错或元素不存在，element-plus 会静默居中，这条会红
  · 正文字数：防止只换了 title 而 body 是空模板
  · 走完落标记：防止改步骤后引导每次刷新都弹
"""
import json
import os
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))  # tests/probes/x.py → 工作区根
CHROME = os.path.join(BASE, "ai-study-companion/backend/data/browsers/chromium-1200/chrome-win64/chrome.exe")
URL = "http://127.0.0.1:4176/"
SHOTS = os.path.join(BASE, "_probe_shots_tour_skills_20261023")
os.makedirs(SHOTS, exist_ok=True)

fails, oks = [], []


def ok(name, cond, detail=""):
    (oks if cond else fails).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  | " + str(detail)) if detail else ""))


def wait_ready():
    _op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with _op.open(URL, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


assert wait_ready(), "preview 服务未就绪"


def center_y(bb):
    return None if not bb else bb["y"] + bb["height"] / 2.0


# 每个步骤的目标元素选择器（按 title 关键字映射，不依赖编号）
TARGET_OF = [
    ("导入材料", "[data-nav='/']"),
    ("理解", "[data-nav='/chat']"),
    ("技能库", "[data-nav='/skills']"),
    ("沉淀", "[data-nav='/review']"),
    ("帮助", ".nav-help"),
]

# ⚠️ configured=True → needSetup=false（不显示「先配置」步）→ 总数少 1
#    configured=False → needSetup=true → 总数多 1。第一版把这里写反了，4 条判据假红。
EXPECT_DONE = ["① 导入材料", "② 理解 & 提问", "③ 技能库：让 AI 按方法回答",
               "④ 沉淀 & 抗遗忘", "随时查看帮助"]
EXPECT_SETUP = ["① 先配置 AI 模型", "② 导入材料", "③ 理解 & 提问",
                "④ 技能库：让 AI 按方法回答", "⑤ 沉淀 & 抗遗忘", "随时查看帮助"]


def walk(configured, tag):
    print("\n========== configured=%s ==========" % configured)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True)
        page = b.new_page(viewport={"width": 1280, "height": 900})

        def handle(route):
            u = route.request.url
            if u.rstrip("/").endswith("/api/settings"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"configured": configured}))
            elif u.rstrip("/").endswith("/api/version"):
                route.fulfill(status=200, content_type="application/json", body='{"version":"0.1.7"}')
            else:
                route.fulfill(status=200, content_type="application/json", body="{}")

        page.route("**/api/**", handle)
        page.goto(URL, wait_until="domcontentloaded")
        page.evaluate("localStorage.removeItem('asc_onboarded')")
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".el-tour__title", timeout=12000)
        page.wait_for_timeout(500)

        n_ind = page.locator(".el-tour-indicator").count()
        expect_total = 5 if configured else 6
        ok("[%s] 步骤总数 == %d（新增 1 步前为 %d）" % (tag, expect_total, expect_total - 1),
           n_ind == expect_total, n_ind)

        # 「跳过引导」出口必须仍在（遮罩会拦点击，没有出口=界面像卡死）
        ok("[%s] 「跳过引导」按钮可见" % tag,
           page.locator(".tour-skip").is_visible(), "")

        # configured=True ⇒ needSetup=False ⇒ 不带「先配置」步（用 EXPECT_DONE）
        expect_titles = EXPECT_DONE if configured else EXPECT_SETUP
        titles, geo_ok, body_texts = [], [], {}
        for i in range(10):
            page.wait_for_timeout(420)
            if page.locator(".el-tour__title").count() == 0:
                break
            title = page.locator(".el-tour__title").inner_text().strip()
            body = page.locator(".el-tour__body").inner_text().strip()
            titles.append(title)
            body_texts[title] = body

            sel = next((s for k, s in TARGET_OF if k in title), None)
            if sel:
                tb = page.locator(sel).first.bounding_box()
                cb = page.locator(".el-tour__content").first.bounding_box()
                d = abs((center_y(cb) or -999) - (center_y(tb) or 0))
                geo_ok.append((title, round(d, 2)))
                ok("[%s] 「%s」popper 垂直对齐 %s（差 %.2fpx）" % (tag, title, sel, d), d <= 2.0,
                   "content=%s target=%s" % (cb, tb))

            if "技能" in title:
                cb = page.locator(".el-tour__content").first.bounding_box()
                inside = (cb["x"] >= 0 and cb["y"] >= 0
                          and cb["x"] + cb["width"] <= 1280 and cb["y"] + cb["height"] <= 900)
                ok("[%s] 技能步骤 popper 不溢出视口" % tag, inside, cb)
                ok("[%s] 技能步骤正文非空模板（>=30 字）" % tag, len(body) >= 30, len(body))
                ok("[%s] 技能步骤正文说明了技能是什么（含「说明」）" % tag, "说明" in body, body[:40])
                # 箭头必须落在侧栏右边缘之外，证明是贴着侧栏右侧展开
                ab = page.locator(".el-tour__arrow").first.bounding_box()
                ok("[%s] 箭头指向侧栏（x>167）" % tag, bool(ab) and ab["x"] > 167, ab)
                page.screenshot(path=os.path.join(SHOTS, "%s_skill_step.png" % tag))

            btn = page.locator(".el-tour__footer .el-button--primary").first
            btn.click()
            page.wait_for_timeout(220)

        ok("[%s] 步骤标题序列正确" % tag, titles == expect_titles, titles)
        ok("[%s] 技能步骤存在且只有 1 个" % tag,
           sum(1 for t in titles if "技能" in t) == 1, titles)
        ok("[%s] 走完全程后落标记（不会每次刷新都弹）" % tag,
           page.evaluate("localStorage.getItem('asc_onboarded')") == "1",
           page.evaluate("localStorage.getItem('asc_onboarded')"))
        b.close()
        return body_texts


def walk_collapsed():
    """折叠态：老用户可能上次折叠了侧栏，此时重启引导，目标元素只有图标宽度。

    这条只断言"不崩、仍能定位、不溢出"——折叠态的箭头 x 必然小于展开态，不能复用同一阈值。
    """
    print("\n========== 折叠态（asc_sidebar_collapsed=1） ==========")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True)
        page = b.new_page(viewport={"width": 1280, "height": 900})

        def handle(route):
            u = route.request.url
            if u.rstrip("/").endswith("/api/settings"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"configured": True}))
            elif u.rstrip("/").endswith("/api/version"):
                route.fulfill(status=200, content_type="application/json", body='{"version":"0.1.7"}')
            else:
                route.fulfill(status=200, content_type="application/json", body="{}")

        page.route("**/api/**", handle)
        page.goto(URL, wait_until="domcontentloaded")
        page.evaluate("localStorage.removeItem('asc_onboarded');"
                      "localStorage.setItem('asc_sidebar_collapsed','1')")
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".el-tour__title", timeout=12000)

        title = ""
        for _ in range(4):
            page.wait_for_timeout(420)
            if page.locator(".el-tour__title").count() == 0:
                break
            title = page.locator(".el-tour__title").inner_text().strip()
            if "技能" in title:
                break
            page.locator(".el-tour__footer .el-button--primary").first.click()

        ok("[折叠] 技能步骤仍能到达", "技能" in title, title)
        nb = page.locator("[data-nav='/skills']").bounding_box()
        ok("[折叠] 技能导航项可定位（宽度 > 0）", bool(nb) and nb["width"] > 0, nb)
        cb = page.locator(".el-tour__content").first.bounding_box()
        ok("[折叠] popper 不溢出视口",
           bool(cb) and cb["x"] >= 0 and cb["y"] >= 0
           and cb["x"] + cb["width"] <= 1280 and cb["y"] + cb["height"] <= 900, cb)
        page.screenshot(path=os.path.join(SHOTS, "collapsed_skill_step.png"))
        b.close()


walk(True, "已配置")
walk(False, "未配置")
walk_collapsed()

print("\n==== 结果：%d PASS / %d FAIL ====" % (len(oks), len(fails)))
for f in fails:
    print("FAILED:", f)
raise SystemExit(1 if fails else 0)
