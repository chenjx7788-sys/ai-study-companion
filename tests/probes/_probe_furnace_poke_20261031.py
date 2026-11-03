# -*- coding: utf-8 -*-
"""炼丹炉第七轮 UI 走查（2026-10-31）：A1 窄屏抽屉 + B1 舞台卡成长/升级仪式 + B2 点击彩蛋

吃 dev 5173 + 后端 8000（真实库，app1 当前 bond=熟络 stage1 / 7 场 12 问 3 天）。
判据分辨力：
- U4b 未点汉堡时抽屉必须**不在**视口（否则「点了滑入」无分辨力）；
- U3 仪式断言前先写 localStorage=0（比当前低一档）—— 不预设的话「已记熟络」不会播，
  没播就成了假红；播了但没升级是逻辑错。
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
CHROME = r"C:\Users\陈锦祥\AppData\Local\ms-playwright\chromium-1223\chrome-win64\chrome.exe"
RESULTS = []


def report(rid, ok, detail=""):
    RESULTS.append((rid, ok))
    print(("[PASS] " if ok else "[FAIL] ") + rid + "  " + str(detail)[:190], flush=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # ---------- 桌面 1440px ----------
        pg.goto(f"{BASE}/furnace/app/1", wait_until="domcontentloaded")
        pg.wait_for_selector(".bond-chip", timeout=30000)
        pg.wait_for_timeout(600)

        chip = pg.inner_text(".bond-chip")
        bar = pg.query_selector(".bond-bar i")
        nxt = pg.inner_text(".bond-next") if pg.query_selector(".bond-next") else ""
        report("U1 舞台卡成长块（熟络徽章+进度条+缺口）",
               "熟络" in chip and bar is not None and "再" in nxt and "默契" in nxt,
               f"chip={chip!r} 进度条={bar is not None} next={nxt!r}")
        bar_w = pg.evaluate("() => document.querySelector('.bond-bar i').style.width") if bar else ""
        # ⚠️ 别写死百分比：progress 随真实库数据与口径漂移（本地日界让 days 变化就会变），
        # 正确判据是「渲染宽度 == 接口现读值」（该红时真红：宽度不跟接口走就 FAIL）。
        import json as _json
        import urllib.request as _urlreq
        _op2 = _urlreq.build_opener(_urlreq.ProxyHandler({}))
        with _op2.open("http://127.0.0.1:8000/api/apps/1", timeout=10) as _r:
            live_prog = _json.loads(_r.read().decode())["bond"]["progress"]
        expect_w = f"{round(live_prog * 100)}%"
        report("U1b 进度条宽度=接口现读 progress", bar_w == expect_w,
               f"width={bar_w!r} live={expect_w} (progress={live_prog})")

        # B2 点击彩蛋
        pg.click(".stage-spirit")
        pg.wait_for_selector(".poke-bubble", timeout=3000)
        q1 = pg.inner_text(".poke-bubble")
        pg.click(".stage-spirit")           # 600ms 节流内连点：文案必须不变
        q2 = pg.inner_text(".poke-bubble")
        report("U2 点丹灵冒气泡（节流内连点不换文案）",
               bool(q1.strip()) and q1 == q2, f"气泡={q1!r} 连点后={q2!r}")

        # B1 升级仪式：预设低一档 → 重载应播
        pg.evaluate("() => localStorage.setItem('furnace-bond-max-1', '0')")
        pg.reload(wait_until="domcontentloaded")
        try:
            pg.wait_for_selector(".bondup-mask", timeout=8000)
            label = pg.inner_text(".bondup-label")
            # ⚠️ 点遮罩必须点边缘空白：盒子居中，mask 中心 = 盒子（盒子 @click.stop 不收）
            pg.click(".bondup-mask", position={"x": 24, "y": 24})
            pg.wait_for_timeout(400)
            gone = pg.query_selector(".bondup-mask") is None
            report("U3 跨阶段播升级仪式（0→熟络），点遮罩边缘收起",
                   label == "熟络" and gone, f"label={label!r} 已收起={gone}")
        except Exception as e:
            report("U3 跨阶段播升级仪式", False, f"未出现 bondup-mask：{e}")

        # ESC 路径（与揭炉仪式同口径）
        pg.evaluate("() => localStorage.setItem('furnace-bond-max-1', '0')")
        pg.reload(wait_until="domcontentloaded")
        try:
            pg.wait_for_selector(".bondup-mask", timeout=8000)
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(400)
            report("U3b ESC 收起仪式", pg.query_selector(".bondup-mask") is None)
        except Exception as e:
            report("U3b ESC 收起仪式", False, f"未出现 bondup-mask：{e}")

        # 掉档回升不重播（2026-10-31 二轮）：max 记 1、当前档 1 → 不播
        pg.evaluate("() => localStorage.setItem('furnace-bond-max-1', '1')")
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_selector(".bond-chip", timeout=30000)
        pg.wait_for_timeout(1500)
        report("U3c 分辨力：历史最高档=当前档 → 不播仪式",
               pg.query_selector(".bondup-mask") is None)

        # ---------- 窄屏 900px（A1 抽屉） ----------
        pg2 = browser.new_page(viewport={"width": 900, "height": 800})
        pg2.on("pageerror", lambda e: errs.append(str(e)))
        pg2.goto(f"{BASE}/furnace/app/1", wait_until="domcontentloaded")
        pg2.wait_for_selector(".fc-topbar", timeout=30000)
        pg2.wait_for_timeout(500)

        topbar_vis = pg2.is_visible(".fc-topbar")
        stage_hidden = pg2.evaluate(
            "() => getComputedStyle(document.querySelector('.fc-stage')).display === 'none'")
        report("U4 窄屏：顶栏可见、舞台卡收走", topbar_vis and stage_hidden,
               f"topbar={topbar_vis} stage隐藏={stage_hidden}")

        drawer_rect = pg2.evaluate(
            "() => { const r = document.querySelector('.fc-sess').getBoundingClientRect();"
            " return {left: r.left, right: r.right}; }")
        report("U4b 分辨力：未点汉堡时抽屉在视口外",
               drawer_rect["right"] <= 2, f"fc-sess rect={drawer_rect}")

        pg2.click(".tb-btn")
        pg2.wait_for_timeout(400)
        opened = pg2.evaluate(
            "() => { const r = document.querySelector('.fc-sess').getBoundingClientRect();"
            " return {left: r.left, right: r.right}; }")
        mask_vis = pg2.is_visible(".sess-mask")
        report("U5 点汉堡抽屉滑入+遮罩出现",
               abs(opened["left"]) <= 2 and mask_vis, f"rect={opened} 遮罩={mask_vis}")

        pg2.click(".sess-mask")
        pg2.wait_for_timeout(400)
        closed = pg2.evaluate(
            "() => { const r = document.querySelector('.fc-sess').getBoundingClientRect();"
            " return r.right; }")
        report("U6 点遮罩抽屉收起", closed <= 2, f"收起后 right={closed}")

        # 2026-10-31 二轮新修：点「当前会话」行也要收抽屉（原实现在提前 return 之后）
        pg2.click(".tb-btn"); pg2.wait_for_timeout(400)
        pg2.click(".sess-item")          # 先打开一个会话（抽屉会自收）
        pg2.wait_for_timeout(800)
        pg2.click(".tb-btn"); pg2.wait_for_timeout(400)
        pg2.click(".sess-item.active")   # 再点「当前会话」行
        pg2.wait_for_timeout(500)
        cur_right = pg2.evaluate(
            "() => { const r = document.querySelector('.fc-sess').getBoundingClientRect();"
            " return r.right; }")
        report("U6b 点当前会话行也收抽屉（修复固化）", cur_right <= 2, f"right={cur_right}")

        # ESC 收抽屉（2026-10-31 二轮）
        pg2.click(".tb-btn"); pg2.wait_for_timeout(400)
        pg2.keyboard.press("Escape")
        pg2.wait_for_timeout(400)
        esc_right = pg2.evaluate(
            "() => { const r = document.querySelector('.fc-sess').getBoundingClientRect();"
            " return r.right; }")
        report("U6c ESC 收抽屉", esc_right <= 2, f"right={esc_right}")

        # 抽屉里点「新会话」应自动收起抽屉
        pg2.click(".tb-btn")
        pg2.wait_for_timeout(400)
        pg2.click(".fc-sess .new-btn")
        pg2.wait_for_timeout(400)
        after_new = pg2.evaluate(
            "() => { const r = document.querySelector('.fc-sess').getBoundingClientRect();"
            " return r.right; }")
        report("U7 抽屉里点新会话自动收起", after_new <= 2, f"right={after_new}")

        # ---------- C3 看板 miss 卡（真实库 app1 有 1 条 miss）----------
        pg3 = browser.new_page(viewport={"width": 1440, "height": 900})
        pg3.on("pageerror", lambda e: errs.append(str(e)))
        pg3.goto(f"{BASE}/furnace/app/1/edit?tab=stats", wait_until="domcontentloaded")
        try:
            pg3.wait_for_selector(".miss-card", timeout=30000)
            miss_q = pg3.inner_text(".miss-row .miss-q")
            pg3.click(".miss-ops .el-button")
            pg3.wait_for_timeout(600)
            on_mat = pg3.evaluate("() => location.search.includes('tab=materials')")
            pool = pg3.query_selector(".mat-pool") is not None
            report("U8 miss 卡渲染 + 「去补料」直达资料绑定 tab",
                   "上证指数" in miss_q and on_mat and pool,
                   f"miss={miss_q[:24]!r} tab跳转={on_mat} 追加池={pool}")
        except Exception as e:
            report("U8 miss 卡渲染", False, f"未等到 .miss-card：{e}")
        pg3.close()

        # ---------- A3 列表卡片：bond 徽章 + 活跃标签 ----------
        pg4 = browser.new_page(viewport={"width": 1440, "height": 900})
        pg4.on("pageerror", lambda e: errs.append(str(e)))
        pg4.goto(f"{BASE}/furnace", wait_until="domcontentloaded")
        pg4.wait_for_selector(".app-card", timeout=30000)
        pg4.wait_for_timeout(500)
        bond_badges = pg4.eval_on_selector_all(".ac-bond", "els => els.map(e => e.innerText)")
        # ⚠️ 排序正确性已由后端探针 A3-T1 验过；这里不假设首卡是谁（最近活跃的应用随时变），
        # 只验「每卡都有徽章」且「带活跃标签的卡存在」（app1 聊过 → 必有一张含「聊过」）。
        metas = pg4.eval_on_selector_all(".app-card .ac-meta", "els => els.map(e => e.innerText)")
        report("U9 列表卡片带默契徽章 + 最近活跃",
               bool(bond_badges) and all(bond_badges) and any("聊过" in m for m in metas),
               f"徽章={bond_badges[:5]} 含活跃标签的卡={sum(1 for m in metas if '聊过' in m)}")
        pg4.close()

        # ---------- U10 miss 行「再问一次」（C3+，2026-10-31 二轮） ----------
        # ⚠️ 真发送会烧 LLM → mock 精确路径 ask/stream；绝不拦 **/api/**（会拦掉 /src/api/index.js）
        pg5 = browser.new_page(viewport={"width": 1440, "height": 900})
        pg5.on("pageerror", lambda e: errs.append(str(e)))
        pg5.route("**/api/chat/ask/stream", lambda r: r.fulfill(
            status=200, content_type="text/event-stream",
            body=('event: sources\ndata: {"session_id": 9999, "user_message_id": 1,'
                  ' "kb_hit": true, "answer_kind": "kb", "sources": []}\n\n'
                  'event: done\ndata: {"ok": true, "message_id": 2}\n\n')))
        pg5.goto(f"{BASE}/furnace/app/1/edit?tab=stats", wait_until="domcontentloaded")
        try:
            pg5.wait_for_selector(".miss-ask", timeout=30000)
            pg5.click(".miss-ask")
            # SPA pushState 不触发 load → wait_for_url 必超时，用 wait_for_function
            pg5.wait_for_function(
                "() => location.pathname === '/furnace/app/1' && location.search.includes('ask=')",
                timeout=8000)
            # 发送完成后 query 必须被清掉（防刷新重发）
            pg5.wait_for_function("() => !location.search.includes('ask=')", timeout=15000)
            pg5.wait_for_timeout(500)
            body = pg5.inner_text(".fc-msgarea")
            report("U10 再问一次：跳对话页自动发送 + query 已清 + 气泡出现",
                   "上证指数" in body, f"消息区含问题={'上证指数' in body}")
        except Exception as e:
            report("U10 再问一次闭环", False, f"{e}")
        pg5.close()

        # ---------- U11 close 池解锁（B2+）：mock 详情接口把 stage 改成 2 ----------
        pg6 = browser.new_page(viewport={"width": 1440, "height": 900})
        pg6.on("pageerror", lambda e: errs.append(str(e)))

        def _stage2(route):
            import json as _j
            resp = route.fetch()
            data = _j.loads(resp.body())
            data["bond"]["stage"] = 2
            data["bond"]["label"] = "默契"
            route.fulfill(response=resp, json=data)

        pg6.route("**/api/apps/1", _stage2)
        # max 预设 3（高于 mock 的 2）→ 不弹升级仪式遮罩，否则舞台卡点不到
        pg6.add_init_script("localStorage.setItem('furnace-bond-max-1', '3')")
        pg6.goto(f"{BASE}/furnace/app/1", wait_until="domcontentloaded")
        pg6.wait_for_selector(".stage-spirit", timeout=30000)
        CLOSE_BOOK = {"这本书我们快啃透了", "你问得越来越深了", "下一本读什么？"}
        seen = set()
        for _ in range(8):
            pg6.click(".stage-spirit")
            pg6.wait_for_timeout(300)
            el = pg6.query_selector(".poke-bubble")
            if el:
                seen.add(el.inner_text())
            pg6.wait_for_timeout(450)      # 等过 600ms 节流
        report("U11 stage≥2 并入 close 池（8 连点必出现熟络文案）",
               bool(seen & CLOSE_BOOK), f"出现过的文案={sorted(seen)}")
        pg6.close()

        report("U0 全程无 JS 运行时异常", not errs, f"errors={errs[:2]}")
        browser.close()

    n_pass = sum(1 for _, ok in RESULTS if ok)
    n_fail = sum(1 for _, ok in RESULTS if not ok)
    print("\n" + "=" * 60)
    print(f"总计 {len(RESULTS)} 项：PASS {n_pass} / FAIL {n_fail}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
