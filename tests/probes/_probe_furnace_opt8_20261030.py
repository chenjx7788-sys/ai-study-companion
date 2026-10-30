# -*- coding: utf-8 -*-
"""炼丹炉 8 项优化点验收探针（2026-10-30）

覆盖 O4 / O8 / O10 / O11 / O12 / O13 / O14 / O15。
其中 O4/O8/O10/O11/O12/O15 是 2026-10-29 改的代码、O14 也在码里，**但从未跑过真浏览器**；
本探针把「改过」升级成「验过」，O13 是本轮新改的。

跑法（后端 8000 + dev 5173 都已在跑）：
    <项目解释器> _probe_furnace_opt8_20261030.py

纪律（都是踩过的坑）：
  1. 绝不拦 "**/api/**"：dev 5173 下会把 /src/api/index.js 一起拦掉 → 整页白屏。
     这里按 **pathname 精确匹配**（urlparse(u).path == "/api/apps"）。
  2. 每条判据配分辨力样本：O13 注入假 badge、O15 对比非草稿卡、O12 先取消再覆盖、
     O11 对比「第 1 次打开只有 2 份」、O8 先读旧值 3 场。
  3. 等待条件只等目标元素本身，绝不带空态选择器（空态会立刻满足 → 假 SKIP）。
  4. 会点「创建 / 删除 / 发送」的按钮一律先挂 mock —— 视觉抽查脚本也是会写库的脚本。
"""
import json
import sys
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"
CHROME = r"C:\Users\陈锦祥\AppData\Local\ms-playwright\chromium-1223\chrome-win64\chrome.exe"

RESULTS = []


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", str(detail)))
    print(("[PASS] " if ok else "[FAIL] ") + rid + "  " + str(detail)[:200], flush=True)


def skip(rid, why):
    RESULTS.append((rid, "SKIP", why))
    print("[SKIP] " + rid + "  " + why, flush=True)


def j(route, obj, status=200):
    route.fulfill(status=status, content_type="application/json",
                  body=json.dumps(obj, ensure_ascii=False))


def mock(pg, path, handler):
    """按 pathname 精确挂载。path 支持前缀匹配（endswith('*')）。"""
    if path.endswith("*"):
        pre = path[:-1]
        pg.route(lambda u: urllib.parse.urlparse(u).path.startswith(pre), handler)
    else:
        pg.route(lambda u: urllib.parse.urlparse(u).path == path, handler)


# ---------------------------------------------------------------- 预检
def precheck():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    ok5173 = ok8000 = False
    for _ in range(3):                       # vite 重编译期间会短暂 000，重试 3 次
        try:
            with op.open(BASE + "/", timeout=4) as r:
                ok5173 = r.status == 200
            break
        except Exception:
            import time
            time.sleep(2)
    try:
        with op.open(API + "/api/apps", timeout=6) as r:
            ok8000 = r.status == 200
    except Exception:
        pass
    if not (ok5173 and ok8000):
        print("[PRECHECK] dev5173=%s api8000=%s —— 服务没起，不计 PASS/FAIL" % (ok5173, ok8000), flush=True)
        sys.exit(2)
    print("[precheck] dev 5173 / api 8000 均在", flush=True)


def new_page(browser, errs):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    return pg


# ---------------------------------------------------------------- O4（静态）
def probe_o4():
    p = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\backend\app\routers\chat.py"
    src = open(p, "rb").read().decode("utf-8")
    n = src.count("apps_svc.material_ids(db")
    report("O4a 绑定资料在本请求只查一次", n == 1, "chat.py 里 material_ids(db 调用点=%d" % n)
    report("O4b 双保险复用同一份（_allow = set(app_mids)）",
           "_allow = set(app_mids)" in src, "找到 _allow = set(app_mids)")
    # 分辨力：确保这个字符串不是碰巧出现 —— 换一个不存在的写法计数应为 0
    report("O4c 判据分辨力（不存在的调用写法计数为 0）",
           src.count("apps_svc.material_ids_missing(db") == 0)


# ---------------------------------------------------------------- APP 列表页：O13/O15/O11/O12/O14
APPS = [
    {"id": 1, "name": "炼读师", "status": "ok", "spirit": "book", "material_count": 2,
     "session_count": 3, "persona_head": "你是炼读师", "created_at": "2026-10-29T01:00:00",
     "updated_at": "2026-10-30T01:00:00"},
    {"id": 9, "name": "掉料丹灵", "status": "draft", "spirit": "book", "material_count": 0,
     "session_count": 0, "persona_head": "你是掉料丹灵", "created_at": "2026-10-29T01:00:00",
     "updated_at": "2026-10-30T01:00:00"},
]


def probe_list_page(browser, errs):
    pg = new_page(browser, errs)
    st = {"materials": 0, "assist": 0, "create": 0}

    def apps_h(route):
        # 同一个 path 同时承载 GET 列表与 POST 创建（不要重复挂两条 route，后挂的会先命中）
        if route.request.method == "POST":
            create_h(route)
        else:
            j(route, APPS)

    def mats_h(route):
        st["materials"] += 1
        base = [{"id": 41, "title": "淬火权杖研究", "parsed_status": "success", "format": "md"},
                {"id": 42, "title": "星辉辅食手册", "parsed_status": "success", "format": "pdf"}]
        if st["materials"] >= 2:      # 第二次打开时多一份「本次会话新导入」的资料
            base.append({"id": 43, "title": "新导入的资料C", "parsed_status": "success", "format": "md"})
        j(route, base)

    def assist_h(route):
        st["assist"] += 1
        j(route, {"name": "AI 起的名字", "spirit": "book", "persona": "AI 人格文案",
                  "boundaries": "", "miss_text": "", "welcome": "", "questions": ["q1", "q2", "q3"]})

    def create_h(route):
        st["create"] += 1
        j(route, {"id": 500 + st["create"], "name": "新丹灵", "spirit": "book",
                  "status": "ok", "material_count": 1, "session_count": 0, "persona_head": ""})

    def settings_h(route):
        j(route, {"llm_models": []})

    mock(pg, "/api/apps", apps_h)
    mock(pg, "/api/materials", mats_h)
    mock(pg, "/api/apps/assist", assist_h)
    mock(pg, "/api/settings", settings_h)
    # O15c 会跳到配置页，配置页要读详情/版本/看板 —— 一并 mock，别让真后端回 404 干扰判据
    mock(pg, "/api/apps/9", lambda r: j(r, {"id": 9, "name": "掉料丹灵", "status": "draft",
                                            "spirit": "book", "persona": "你是掉料丹灵",
                                            "bounding": "", "materials": []}))
    mock(pg, "/api/apps/9/versions", lambda r: j(r, []))
    mock(pg, "/api/apps/9/stats", lambda r: j(r, {"question_count": 0, "hit_count": 0, "miss_count": 0,
                                                  "answered_count": 0, "failed_count": 0,
                                                  "hit_rate": 0, "trend": []}))

    pg.goto(BASE + "/furnace", wait_until="domcontentloaded")
    pg.wait_for_selector(".app-aside", timeout=15000)
    pg.wait_for_timeout(800)

    # ---------- O13 侧栏「新」标 ----------
    has_menu = pg.locator(".app-aside").inner_text().find("炼丹炉") >= 0
    n_badge = pg.locator(".app-aside .new-badge").count()
    report("O13a 侧栏有炼丹炉入口（前提）", has_menu, "menu含炼丹炉=%s" % has_menu)
    report("O13b 炼丹炉不再挂「新」标", n_badge == 0, ".new-badge 计数=%d" % n_badge)
    # 分辨力：注入一个假 badge，同一选择器必须能数到
    pg.evaluate("""() => { const a = document.querySelector('.app-aside');
        const s = document.createElement('span'); s.className = 'new-badge'; s.textContent = '新';
        s.id = '__fake_badge'; a.appendChild(s); }""")
    n_fake = pg.locator(".app-aside .new-badge").count()
    pg.evaluate("() => { const e = document.getElementById('__fake_badge'); if (e) e.remove(); }")
    report("O13c 判据分辨力（注入假 badge 能数到 1）", n_fake == 1, "注入后计数=%d" % n_fake)

    # ---------- O15 草稿态卡片主按钮 ----------
    pg.wait_for_selector(".ac-go", timeout=10000)
    cards = pg.locator(".ac-go")
    txts = [cards.nth(i).inner_text().strip() for i in range(cards.count())]
    report("O15a 草稿态卡片主按钮=补料后复活", "补料后复活" in txts, "按钮文案=%s" % txts)
    report("O15b 正常卡片主按钮仍是进入对话（分辨力）", "进入对话" in txts, "按钮文案=%s" % txts)
    # 点草稿态的「补料后复活」应去配置页
    idx = txts.index("补料后复活") if "补料后复活" in txts else -1
    if idx >= 0:
        cards.nth(idx).click()
        pg.wait_for_timeout(600)
        report("O15c 点它能直达配置页", "/edit" in pg.url, "url=%s" % pg.url)
        pg.go_back()
        pg.wait_for_selector(".ac-go", timeout=10000)
    else:
        skip("O15c 点它能直达配置页", "没找到草稿态卡片")

    # ---------- O11 每次开炉都重取资料 ----------
    # ⚠️ 计数必须从现在归零：O15c 已经去过配置页，那边也会 GET /api/materials，
    #    不归零就会把「上一次页面的请求」算进本次，得出 pick=3 / 请求=3 的假红。
    st["materials"] = 0
    pg.locator("button:has-text('＋ 开炉炼应用')").first.click()
    pg.wait_for_selector(".pick", timeout=10000)
    n1 = pg.locator(".pick").count()
    c1 = st["materials"]
    report("O11a 第一次开炉拉到资料", n1 == 2 and c1 == 1, "pick=%d, 请求次数=%d" % (n1, c1))
    report("O11b 此时还没有新资料（前提）",
           pg.locator(".pick:has-text('新导入的资料C')").count() == 0, "C 未出现")
    pg.locator(".wiz-x").click()
    pg.wait_for_timeout(500)

    pg.locator("button:has-text('＋ 开炉炼应用')").first.click()
    pg.wait_for_selector(".pick", timeout=10000)
    c2 = st["materials"]
    hasC = pg.locator(".pick:has-text('新导入的资料C')").count()
    report("O11c 再次开炉真的重取了（请求次数 2）", c2 == 2, "请求次数=%d" % c2)
    report("O11d 本次会话新导入的资料出现在投料区", hasC == 1, "C 卡片数=%d" % hasC)

    # ---------- O12 草稿覆盖前必须确认 ----------
    pg.locator(".pick").first.click()
    pg.locator("button:has-text('下一步：定型')").click()
    pg.wait_for_selector(".wz-guide", timeout=8000)
    name_in = pg.get_by_placeholder("例如：《政治学十五讲》领读人")
    name_in.fill("我手填的名字")
    pg.locator("button:has-text('✨ AI 起草')").first.click()
    confirmed = False
    try:
        pg.wait_for_selector(".el-message-box", timeout=5000)
        confirmed = True
    except Exception:
        confirmed = False
    report("O12a 手填后点 AI 起草会先确认", confirmed, "弹窗出现=%s" % confirmed)
    if confirmed:
        pg.click(".el-message-box__btns button:has-text('取消')")
        pg.wait_for_timeout(500)
        report("O12b 取消后不覆盖、也不发请求",
               st["assist"] == 0 and name_in.input_value() == "我手填的名字",
               "assist 请求=%d, name=%r" % (st["assist"], name_in.input_value()))
        pg.locator("button:has-text('✨ AI 起草')").first.click()
        pg.wait_for_selector(".el-message-box", timeout=5000)
        pg.click(".el-message-box__btns button:has-text('覆盖')")
        pg.wait_for_timeout(900)
        report("O12c 选「覆盖」才真的起草（请求 1 次且字段被替换）",
               st["assist"] == 1 and name_in.input_value() == "AI 起的名字",
               "assist=%d, name=%r" % (st["assist"], name_in.input_value()))
    else:
        skip("O12b 取消后不覆盖", "确认框没出现")
        skip("O12c 选覆盖才起草", "确认框没出现")

    # ---------- O14 揭炉仪式可退出（× / ESC / 点遮罩） ----------
    def walk_and_create():
        # O12 跑完向导还开着（第②步有手填内容）→ 先关掉，否则遮罩会吃掉对列表按钮的点击
        if pg.locator(".wiz-mask").count():
            pg.locator(".wiz-x").click()
            try:
                pg.wait_for_selector(".el-message-box", timeout=3000)
                pg.click(".el-message-box__btns button:has-text('关闭并暂存')")
            except Exception:
                pass
            pg.wait_for_timeout(500)
        pg.locator("button:has-text('＋ 开炉炼应用')").first.click()
        # 暂存恢复时直接落在第②步（没有 .pick）；否则从第①步开始
        if pg.locator(".pick").count():
            pg.locator(".pick").first.click()
            pg.locator("button:has-text('下一步：定型')").click()
        pg.wait_for_selector(".wz-form, .wz-guide", timeout=10000)
        pg.get_by_placeholder("例如：《政治学十五讲》领读人").fill("仪式测试丹灵")
        pg.get_by_placeholder("它是谁、站什么视角、怎么说话……").fill("你是测试人格")
        pg.locator("button:has-text('下一步：出炉')").click()
        pg.wait_for_selector("button:has-text('确认出炉')", timeout=8000)
        pg.locator("button:has-text('确认出炉')").click()
        pg.wait_for_selector(".ritual-mask", timeout=15000)

    walk_and_create()
    report("O14a 创建后仪式出现（前提）", pg.locator(".ritual-mask").count() == 1, "ritual-mask=1")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(600)
    report("O14b ESC 能退出仪式且停在列表页",
           pg.locator(".ritual-mask").count() == 0 and pg.url.rstrip("/").endswith("/furnace"),
           "mask=%d url=%s" % (pg.locator(".ritual-mask").count(), pg.url))

    walk_and_create()
    pg.locator(".ritual-x").click()
    pg.wait_for_timeout(600)
    report("O14c 右上角 × 能退出仪式", pg.locator(".ritual-mask").count() == 0,
           "mask=%d" % pg.locator(".ritual-mask").count())

    walk_and_create()
    pg.locator(".ritual-mask").click(position={"x": 6, "y": 6})
    pg.wait_for_timeout(600)
    report("O14d 点遮罩空白处能退出仪式", pg.locator(".ritual-mask").count() == 0,
           "mask=%d" % pg.locator(".ritual-mask").count())

    report("X1 列表页无 JS 异常", len(errs) == 0, "pageerror=%s" % errs[:2])
    pg.close()


# ---------------------------------------------------------------- 对话页：O10
def probe_o10(browser, errs):
    pg = new_page(browser, errs)
    msgs = [
        {"id": 11, "role": "user", "content": "这份资料讲了什么", "created_at": "2026-10-30T01:00:00"},
        {"id": 12, "role": "assistant", "content": "讲了淬火权杖的概念，见资料[1]。",
         "created_at": "2026-10-30T01:00:01", "answer_kind": "kb",
         "sources": [{"ref_type": "chunk", "ref_id": 9001, "material_id": 52, "title": "淬火权杖研究",
                      "page_no": 3, "snippet": "淬火权杖是本书虚构的政治学概念，指权力主体在危机时刻"}]},
    ]
    mock(pg, "/api/apps/1", lambda r: j(r, {"id": 1, "name": "炼读师", "status": "ok", "spirit": "book",
                                            "material_count": 2, "session_count": 3,
                                            "persona": "你是炼读师", "bounding": ""}))
    mock(pg, "/api/chat/sessions",
         lambda r: j(r, [{"id": 7, "title": "历史对话", "pinned": False, "message_count": 2,
                          "updated_at": "2026-10-30T01:00:01"}]))
    mock(pg, "/api/chat/sessions/7/messages", lambda r: j(r, msgs))
    mock(pg, "/api/apps/1/nudge", lambda r: j(r, {"text": "", "questions": []}))
    mock(pg, "/api/settings", lambda r: j(r, {"llm_models": []}))

    pg.goto(BASE + "/furnace/app/1", wait_until="domcontentloaded")
    pg.wait_for_selector(".fc-main", timeout=20000)
    # 打开历史会话（aside.fc-sess 本身也带 .fc-sess，用 .sess-item 精确定位会话行）
    try:
        pg.wait_for_selector(".sess-item", timeout=8000)
        pg.locator(".sess-item").first.click()
    except Exception:
        pass
    pg.wait_for_timeout(1200)

    has_toggle = pg.locator(".sources-toggle").count()
    if not has_toggle:
        skip("O10a 引用来源卡可见（前提未满足）", "页面上没有 .sources-toggle，无法验跳转")
        pg.close()
        return
    pg.locator(".sources-toggle").first.click()
    pg.wait_for_selector(".source-card", timeout=6000)
    report("O10a 展开后能看到来源卡（前提）", pg.locator(".source-card").count() == 1,
           "source-card=%d" % pg.locator(".source-card").count())

    n_pages_before = len(pg.context.pages)
    pg.locator(".source-card").first.click()
    pg.wait_for_timeout(1200)
    url = pg.url
    n_pages_after = len(pg.context.pages)
    report("O10b 点来源卡走站内跳转（URL 变 /study/52）",
           "/study/52" in url, "url=%s" % url)
    report("O10c 带上了页码参数", "page=3" in url, "url=%s" % url)
    report("O10d 没有新开窗口/标签（window.open 会 +1）",
           n_pages_after == n_pages_before, "pages %d → %d" % (n_pages_before, n_pages_after))
    report("O10e 对话页无 JS 异常", len(errs) == 0, "pageerror=%s" % errs[:2])
    pg.close()


# ---------------------------------------------------------------- 对话页：O8
def probe_o8(browser, errs):
    pg = new_page(browser, errs)
    st = {"sessions": 0, "app": 0}
    held = []

    def app_h(route):
        st["app"] += 1
        # 第一次 3 场；中止后回读时后端已多一场 → 4 场
        j(route, {"id": 1, "name": "炼读师", "status": "ok", "spirit": "book",
                  "material_count": 2, "session_count": 3 if st["app"] == 1 else 4,
                  "persona": "你是炼读师"})

    def sess_h(route):
        st["sessions"] += 1
        if st["sessions"] == 1:
            j(route, [])                       # 还没有会话 → 首问会新建（wasNewSession）
        else:
            j(route, [{"id": 8, "title": "新对话", "pinned": False, "message_count": 2,
                       "updated_at": "2026-10-30T01:00:01"}])

    def stream_h(route):
        held.append(route)                     # 一直挂着：留给「停止」按钮触发 abort

    mock(pg, "/api/apps/1", app_h)
    mock(pg, "/api/chat/sessions", sess_h)
    mock(pg, "/api/settings", lambda r: j(r, {"llm_models": []}))
    mock(pg, "/api/apps/1/nudge", lambda r: j(r, {"text": "", "questions": []}))
    mock(pg, "/api/chat/sessions/8/messages", lambda r: j(r, []))
    pg.route(lambda u: urllib.parse.urlparse(u).path == "/api/chat/ask/stream", stream_h)

    pg.goto(BASE + "/furnace/app/1", wait_until="domcontentloaded")
    pg.wait_for_selector(".bond-chip", timeout=20000)
    old = pg.locator(".bond-chip").inner_text().strip()
    report("O8a 初始默契度是旧值（前提）", "3" in old, "bond-chip=%r" % old)
    s_before = st["sessions"]

    pg.locator(".fc-input input, .fc-input textarea").first.fill("这东西能干嘛")
    pg.keyboard.press("Enter")
    # 等「停止」按钮出现 = 已进入流式
    ok_stop = False
    try:
        pg.wait_for_selector("button:has-text('停止')", timeout=8000)
        ok_stop = True
    except Exception:
        ok_stop = False
    report("O8b 流式期间出现「停止」按钮（前提）", ok_stop, "停止按钮=%s" % ok_stop)
    if ok_stop:
        pg.locator("button:has-text('停止')").first.click()
        pg.wait_for_timeout(1500)
        new = pg.locator(".bond-chip").inner_text().strip()
        report("O8c 停止后回读了会话列表", st["sessions"] > s_before,
               "sessions 请求 %d → %d" % (s_before, st["sessions"]))
        report("O8d 停止后默契度不再是旧值", "4" in new, "bond-chip %r → %r" % (old, new))
        report("O8e 气泡被标成「已停止」", pg.locator(".err-line:has-text('已停止')").count() >= 1,
               "err-line 已停止=%d" % pg.locator(".err-line:has-text('已停止')").count())
    else:
        skip("O8c 停止后回读会话列表", "没进流式态")
        skip("O8d 停止后默契度刷新", "没进流式态")
        skip("O8e 气泡标已停止", "没进流式态")

    for r in held:                              # 收尾：把挂起的请求放掉，避免关闭时卡住
        try:
            r.abort()
        except Exception:
            pass
    report("O8f 对话页无 JS 异常", len(errs) == 0, "pageerror=%s" % errs[:2])
    pg.close()


def main():
    precheck()
    probe_o4()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROME)
        errs = []
        try:
            probe_list_page(browser, errs)
        except Exception as e:
            report("LIST 场景异常", False, repr(e)[:300])
        errs2 = []
        try:
            probe_o10(browser, errs2)
        except Exception as e:
            report("O10 场景异常", False, repr(e)[:300])
        errs3 = []
        try:
            probe_o8(browser, errs3)
        except Exception as e:
            report("O8 场景异常", False, repr(e)[:300])
        browser.close()

    total = len(RESULTS)
    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    skipped = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    print("\n合计 %d 项，通过 %d，失败 %d，跳过 %d" % (total, passed, failed, skipped), flush=True)
    if failed:
        print("失败清单：", flush=True)
        for rid, s, d in RESULTS:
            if s == "FAIL":
                print("  - %s  %s" % (rid, d), flush=True)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
