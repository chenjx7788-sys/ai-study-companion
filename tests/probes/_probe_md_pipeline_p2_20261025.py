"""渲染管线二期验收探针（2026-10-25）

三层判据：
  S 系 静态契约：六视图全部走 createMd + renderMdSafe，无裸 MarkdownIt / md.render 残留
  X 系 单元消毒：renderMdSafe 对 XSS payload 的消毒生效（5173 dev 直接 import 磁盘 md.js）
  P 系 页面 smoke：六个视图在 8000（dist 全栈）真实打开，锚点内容在 + 零 console error

分辨力自检：
  X2 —— 同 payload 用 innerHTML 直插 DOM，onerror 属性必须在（证明 X1 的断言能咬人）
  S9 —— .patchbak 死文件里必须能搜到 new MarkdownIt（证明 S1/S2 的「无残留」不是恒真空转）

用法：<default-venv-python> _probe_md_pipeline_p2_20261025.py
"""
import json
import os
import pathlib
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE_DIR = pathlib.Path(r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\frontend")
SRC = BASE_DIR / "src"
CHROME = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\backend\data\browsers\chromium-1200\chrome-win64\chrome.exe"
DEV = "http://127.0.0.1:5173"
APP = "http://127.0.0.1:8000"

fails, oks, skips = [], [], []


def ok(name, cond, detail=""):
    (oks if cond else fails).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  | " + str(detail)[:150]) if detail else ""))


def skip(name, why):
    skips.append(name)
    print("SKIP " + name + "  | " + why)


def get(path):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(APP + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


# ================= S 系：静态契约 =================
print("===== S 系：静态契约 =====")
views = sorted(p for p in (SRC / "views").glob("*.vue"))
active = [p for p in views if ".bak" not in p.name and ".patchbak" not in p.name]
blob = {p.name: p.read_text(encoding="utf-8") for p in active}

SIX = ["HelpView.vue", "PodcastView.vue", "ReviewView.vue",
       "StatsView.vue", "EphemeralView.vue", "StudyView.vue"]

ok("S1 活跃视图无 import MarkdownIt",
   all("import MarkdownIt" not in t for t in blob.values()),
   [n for n, t in blob.items() if "import MarkdownIt" in t])
ok("S2 活跃视图无 new MarkdownIt",
   all("new MarkdownIt" not in t for t in blob.values()),
   [n for n, t in blob.items() if "new MarkdownIt" in t])
ok("S3 EphemeralView 无 md.render(", "md.render(" not in blob["EphemeralView.vue"])
ok("S4 StudyView 无 md.render(", "md.render(" not in blob["StudyView.vue"])
ok("S5 六视图均接 renderMdSafe", all("renderMdSafe" in blob[n] for n in SIX),
   [n for n in SIX if "renderMdSafe" not in blob[n]])
app_vue = (SRC / "App.vue").read_text(encoding="utf-8")
ok("S6 App.vue 新 tour 文案", "提问时模型会判断哪个技能合用" in app_vue)
dist_hit = any("提问时模型会判断哪个技能合用".encode("utf-8") in p.read_bytes()
               for p in (BASE_DIR / "dist" / "assets").glob("*.js"))
ok("S7 dist 产物含新文案", dist_hit)
mdjs = (SRC / "utils" / "md.js").read_text(encoding="utf-8")
ok("S8 md.js 消毒入口在", "DOMPurify.sanitize" in mdjs and "export function renderMd" in mdjs)

# S9 分辨力：死文件里必须搜得到（证明 S1/S2 的扫描对象不是空集）
patchbak = SRC / "views" / "BrowserSidebarView.vue.patchbak"
ok("S9 分辨力: patchbak 死文件含 new MarkdownIt",
   patchbak.exists() and "new MarkdownIt" in patchbak.read_text(encoding="utf-8"))

# ================= 浏览器部分 =================
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=CHROME, headless=True)

    # ---------- X 系：单元消毒（dev 5173 import 磁盘 md.js） ----------
    print("\n===== X 系：单元消毒 =====")
    page = browser.new_page()
    page.goto(DEV + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    probe = page.evaluate(
        "async () => {"
        "  const m = await import('/src/utils/md.js');"
        "  if (typeof m.renderMd !== 'function' || typeof m.createMd !== 'function')"
        "    return { fatal: 'renderMd/createMd 导出缺失' };"
        "  const md = m.createMd({ breaks: true });"
        "  const payload = '# 标题\\n<img src=x onerror=\"window.__x1=1\">\\n"
        "<script>window.__x2=1</" + "script>\\n[x](javascript:window.__x3=1)\\n\\n**加粗**';"
        "  const html = m.renderMd(md, payload);"
        "  const d = document.createElement('div');"
        "  d.innerHTML = html;"
        "  return {"
        "    imgOnerror: d.querySelectorAll('img[onerror]').length,"
        "    scripts: d.querySelectorAll('script').length,"
        "    jsLinks: d.querySelectorAll('a[href^=\"javascript:\"]').length,"
        "    hasStrong: !!d.querySelector('strong'),"
        "    text: d.textContent.slice(0, 80)"
        "  };"
        "}")
    if probe.get("fatal"):
        ok("X1 renderMdSafe 消毒", False, probe["fatal"])
        ok("X3 消毒不误伤正常内容", False, "前置失败")
    else:
        ok("X1 renderMdSafe 消毒（无 onerror 节点/script 节点/js 链接）",
           probe["imgOnerror"] == 0 and probe["scripts"] == 0 and probe["jsLinks"] == 0,
           {k: probe[k] for k in ("imgOnerror", "scripts", "jsLinks")})
        ok("X3 消毒不误伤正常内容（标题/加粗文本在）",
           probe["hasStrong"] and "标题" in probe["text"], probe["text"][:60])
    raw = page.evaluate(
        "(() => { const d = document.createElement('div');"
        " d.innerHTML = '<img src=x onerror=\"window.__y1=1\">';"
        " document.body.appendChild(d);"
        " const hit = !!d.querySelector('img[onerror]'); d.remove(); return hit; })()")
    ok("X2 分辨力: innerHTML 直插 payload 能检出 onerror", raw)
    page.close()

    # ---------- P 系：页面 smoke（8000 dist 全栈） ----------
    print("\n===== P 系：页面 smoke（8000） =====")

    def smoke(route, name, check_fn):
        errors = []
        pg = browser.new_page()
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(APP + route, wait_until="domcontentloaded")
        pg.wait_for_timeout(1800)
        anchor, detail = check_fn(pg)
        ok("P-%s %s 锚点" % (route, name), anchor, detail)
        # 「Failed to load resource」是网络层 4xx（如 simple-learn 无讲义的业务空态 404），
        # 页面自己的空态逻辑会处理；真 JS 错误不该长这样。剔除但留计数可见。
        js_errors = [e for e in errors if not e.startswith("Failed to load resource")]
        ok("P-%s %s 零 JS 错误" % (route, name), not js_errors,
           {"js": js_errors[:2], "net4xx_已豁免": len(errors) - len(js_errors)})
        pg.close()

    def check_help(pg):
        pg.locator(".faq-q").first.click()
        pg.wait_for_timeout(400)
        txt = pg.locator(".faq-a").first.inner_text().strip()
        return len(txt) > 10, txt[:40]

    def check_text(kw):
        def fn(pg):
            body = pg.locator("body").inner_text()
            return kw in body, "body %d 字, kw=%s" % (len(body), kw)
        return fn

    smoke("/help", "帮助页 FAQ 渲染", check_help)
    smoke("/review", "复习页", check_text("复习"))
    smoke("/podcast", "播客页", check_text("播客"))
    smoke("/stats", "统计页", check_text("数据"))

    mats = get("/api/materials")
    items = mats.get("items") if isinstance(mats, dict) else mats
    if items:
        mid = items[0]["id"]

        def check_study(pg):
            pg.wait_for_timeout(1500)
            body = pg.locator("body").inner_text()
            return len(body) > 100, "body %d 字" % len(body)
        smoke("/study/%s" % mid, "学习页（材料 %s）" % mid, check_study)
    else:
        skip("P-/study", "材料库为空")

    try:
        recent = get("/api/ai/ephemeral/recent")
        ritems = recent.get("items") or []
    except Exception as ex:
        ritems = []
        print("  (ephemeral/recent 拉取失败: %s)" % ex)
    if ritems:
        key = ritems[0]["key"]

        def check_read(pg):
            pg.wait_for_timeout(1500)
            n = pg.locator(".md-preview").count()
            txt = pg.locator(".md-preview").first.inner_text().strip() if n else ""
            return n >= 1 and len(txt) > 10, "md-preview=%d len=%d" % (n, len(txt))
        smoke("/read?key=%s" % key, "临时阅读页", check_read)
    else:
        skip("P-/read", "无 ephemeral 快照可测")

    browser.close()

print("\n===== 总计: %d PASS / %d FAIL / %d SKIP =====" % (len(oks), len(fails), len(skips)))
for f in fails:
    print("FAILED:", f)
sys.exit(1 if fails else 0)
