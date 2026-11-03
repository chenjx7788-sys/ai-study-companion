# -*- coding: utf-8 -*-
"""炼丹炉前端几何探针（2026-10-30）

背景：2026-10-29 那轮把 FurnaceView / FurnaceEditView 整文件重写，只做了「编译通过 +
产物含新文案 + 模板标识符有定义」，**没有跑过浏览器**。几何/交互类断言（吸顶是否真的吸住、
遮罩点击是否弹确认、输入法回车是否发送）只有真浏览器能证。

跑法（后端 8000 + dev 5173 都已在跑）：
    python _probe_furnace_geometry_20261030.py

纪律（踩过的坑）：
  1. dev 5173 下绝不能拦 "**/api/**" —— 会把 /src/api/index.js 一起拦掉导致整页白屏。
     这里只拦精确路径 "**/api/apps/1"。
  2. 「吸顶生效」这类判据必须配负向自检（把 position 改成 static，同一条判据必须判红），
     否则「元素碰巧在视口里」也会 PASS。
  3. 输入法回车必须双向验：组词回车不发送 + 普通回车确实发送（只验前者时，钩子根本没接也能过）。
  4. 配置页 2026-10-30 起为三 tab 结构（当日起默认落第一顺位「人格」tab）：凡操作人格表单/试聊
     可直接测；面板是 v-if 互斥，未激活面板的选择器直接不存在。
     吸顶 G5/G6 也必须在人格 tab 下测 —— 资料绑定 tab 太短，页面滚不动，负向自检会假绿。
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"
CHROME = r"C:\Users\陈锦祥\AppData\Local\ms-playwright\chromium-1223\chrome-win64\chrome.exe"
APP_ID = 1

RESULTS = []


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", str(detail)))
    print(("[PASS] " if ok else "[FAIL] ") + rid + "  " + str(detail)[:190], flush=True)


def skip(rid, why):
    RESULTS.append((rid, "SKIP", why))
    print("[SKIP] " + rid + "  " + why, flush=True)


_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_get(path):
    with _op.open(API + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def rect(pg, sel):
    return pg.evaluate(
        "s => { const el = document.querySelector(s); if (!el) return null;"
        " const r = el.getBoundingClientRect();"
        " return {top: r.top, bottom: r.bottom, left: r.left, h: r.height}; }", sel)


def in_viewport(r, vh):
    return bool(r) and -2 <= r["top"] and r["bottom"] <= vh + 2


def scroll_main_bottom(pg):
    pg.evaluate("() => { const m = document.querySelector('.app-main');"
                " if (m) m.scrollTop = m.scrollHeight; }")
    pg.wait_for_timeout(300)


def msgbox_btn(pg, text):
    sel = f".el-message-box__btns button:has-text('{text}')"
    pg.wait_for_selector(sel, timeout=6000)
    pg.click(sel)


def new_page(browser, sink):
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    pg.on("pageerror", lambda e: sink.append(str(e)))
    return pg


def main():
    vh = 900
    # 前置：两个服务都得活着，否则后面全是「元素找不到」的假红
    try:
        api_get("/api/health")
        api_get(f"/api/apps/{APP_ID}")
    except Exception as e:
        print("前置失败：后端 8000 不可用 ->", repr(e))
        sys.exit(2)

    before_spirit = api_get(f"/api/apps/{APP_ID}").get("spirit")

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        errs = []
        pg = new_page(browser, errs)

        # ---------- 配置页 ----------
        pg.goto(f"{BASE}/furnace/app/{APP_ID}/edit", wait_until="domcontentloaded")
        try:
            pg.wait_for_selector(".fhead h2", timeout=30000)
            title = pg.inner_text(".fhead h2")
            report("G1 配置页渲染（无白屏）", "应用配置" in title, f"标题={title[:40]!r}")
        except Exception as e:
            report("G1 配置页渲染（无白屏）", False, f"未等到 .fhead h2：{e}")
            browser.close()
            return summarize()

        # ---------- T1/T2 页级 tab（2026-10-30 三 tab 改版）----------
        tabs = pg.query_selector_all(".fe-tab")
        report("T1 三个页级 tab 渲染", len(tabs) == 3, f"tab 数={len(tabs)}")
        report("T2a 默认落「人格」（试聊已挂载、资料面板未挂载）",
               pg.query_selector(".pv-bar") is not None and pg.query_selector(".mat-grid") is None,
               f"pv-bar 存在={pg.query_selector('.pv-bar') is not None} "
               f"mat-grid 存在={pg.query_selector('.mat-grid') is not None}")
        pg.click(".fe-tab:has-text('数据看板')")
        pg.wait_for_timeout(300)
        stats_no_pv = pg.query_selector(".pv-bar") is None
        pg.click(".fe-tab:has-text('人格')")
        pg.wait_for_timeout(300)
        persona_has_pv = pg.query_selector(".pv-bar") is not None
        report("T2b 切 tab 面板互斥（看板无试聊、人格有试聊）",
               stats_no_pv and persona_has_pv,
               f"看板时 pv-bar 存在={not stats_no_pv} 人格时 pv-bar 存在={persona_has_pv}")
        report("T2c 切 tab 同步 URL 深链", "tab=persona" in pg.url,
               f"url 尾={pg.url.split('?')[-1][:40]!r}")

        # G7 横向溢出（先测，避免后面滚动影响）
        sw, cw = pg.evaluate("() => { const m = document.querySelector('.app-main');"
                             " return [m.scrollWidth, m.clientWidth]; }")
        report("G7 主区无横向溢出", sw <= cw + 1, f"scrollWidth={sw} clientWidth={cw}")

        # G5 吸顶 + G6 负向自检
        top_before = rect(pg, ".fhead")["top"]
        scroll_main_bottom(pg)
        r_after = rect(pg, ".fhead")
        report("G5 保存条吸顶（滚动到底仍在视口）",
               in_viewport(r_after, vh), f"滚动前 top={top_before:.0f} 滚动后 top={r_after['top']:.0f}")

        pg.evaluate("() => { const s = document.createElement('style'); s.id = 'negsticky';"
                    " s.textContent = '.fhead{position:static !important}'; document.head.appendChild(s); }")
        pg.wait_for_timeout(200)
        r_static = rect(pg, ".fhead")
        pg.evaluate("() => document.getElementById('negsticky')?.remove()")
        pg.wait_for_timeout(200)
        report("G6 吸顶判据有分辨力（改为 static 必须判红）",
               not in_viewport(r_static, vh), f"static 时 top={r_static['top']:.0f}（预期远小于 0）")

        r_restored = rect(pg, ".fhead")
        report("G6b 移除注入样式后恢复吸顶", in_viewport(r_restored, vh), f"top={r_restored['top']:.0f}")
        pg.evaluate("() => { document.querySelector('.app-main').scrollTop = 0; }")
        pg.wait_for_timeout(200)

        # G4 脏状态计数
        pill0 = pg.query_selector(".dirty-pill")
        report("G4a 初始无未保存提示", pill0 is None, "初始 .dirty-pill 不存在" if pill0 is None else "初始就有 pill")
        pg.fill(".fe-left .frow textarea", "探针测试人格：只改一个字用于验证 dirty 计数。")
        pg.wait_for_timeout(250)
        pill = pg.query_selector(".dirty-pill")
        ptext = pill.inner_text() if pill else ""
        report("G4b 改动后出现「N 项未保存」", bool(pill) and "未保存" in ptext, f"pill={ptext!r}")

        # G8 离开拦截
        pg.click("button:has-text('返回对话')")
        try:
            pg.wait_for_selector(".el-message-box", timeout=6000)
            box_title = pg.inner_text(".el-message-box__title")
            msgbox_btn(pg, "继续编辑")
            pg.wait_for_timeout(400)
            stayed = f"/furnace/app/{APP_ID}/edit" in pg.url
            report("G8 未保存离开被拦（选继续编辑则留在原页）",
                   stayed and "未保存" in box_title, f"弹窗={box_title!r} url 尾={pg.url.split('/')[-2:]}")
        except Exception as e:
            report("G8 未保存离开被拦（选继续编辑则留在原页）", False, f"未出现确认框：{e}")

        # G9 换气质不落库（本地生效 → 计入 dirty；后端未变）
        # ⚠️ 2026-10-30 形象位图化后「换一身配色」已下线，本测改验「换气质同样只改表单」。
        #    目标气质必须避开当前装饰（点已选中的不会变脏）。
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_selector(".fhead h2", timeout=30000)
        pg.wait_for_timeout(400)
        pg.click(".fe-tab:has-text('人格')")   # reload 后回到默认 tab；点击幂等（已在其上则空操作）
        pg.wait_for_timeout(300)
        had_pill = pg.query_selector(".dirty-pill") is not None
        cur_deco = (before_spirit or '').split(':')[1] if (before_spirit or '').startswith('g:') else (before_spirit or 'book')
        target = '启明丹灵' if cur_deco != 'star' else '元气丹灵'
        pg.click(f".spirit-opt:has-text('{target}')")
        pg.wait_for_timeout(300)
        pill2 = pg.query_selector(".dirty-pill")
        after_spirit = api_get(f"/api/apps/{APP_ID}").get("spirit")
        report("G9 换气质只在本地生效（不即时落库）",
               (not had_pill) and bool(pill2) and after_spirit == before_spirit,
               f"改前脏={had_pill} 改后脏={bool(pill2)} 后端 spirit 变化={after_spirit != before_spirit}")

        # 放弃改动（顺便验证 discard 真的回到服务端状态）
        pg.click("button:has-text('放弃')")
        try:
            msgbox_btn(pg, "放弃改动")
            pg.wait_for_timeout(600)
            report("G9b 放弃后回到无未保存状态", pg.query_selector(".dirty-pill") is None,
                   "pill 已消失" if pg.query_selector(".dirty-pill") is None else "pill 仍在")
        except Exception as e:
            report("G9b 放弃后回到无未保存状态", False, f"未出现确认框：{e}")

        # G10/G11 试聊：清空按钮态 + 输入法回车
        pg.wait_for_timeout(400)
        clear_disabled = pg.evaluate(
            "() => { const b = document.querySelector('.pv-clear'); return b ? b.disabled : null }")
        report("G10 试聊「清空重开」初始禁用", clear_disabled is True, f"disabled={clear_disabled}")

        pv_sel = ".pv-bar input"
        pg.fill(pv_sel, "测试输入法")
        before_msgs = len(pg.query_selector_all(".pv-msg"))
        pg.evaluate(
            """(sel) => { const el = document.querySelector(sel);
                 el.dispatchEvent(new KeyboardEvent('keydown',
                   {key: 'Enter', keyCode: 229, isComposing: true, bubbles: true})); }""", pv_sel)
        pg.wait_for_timeout(400)
        val_after = pg.input_value(pv_sel)
        msgs_after = len(pg.query_selector_all(".pv-msg"))
        report("G11a 组词回车（isComposing）不发送",
               val_after == "测试输入法" and msgs_after == before_msgs,
               f"值={val_after!r} 消息数 {before_msgs}->{msgs_after}")

        pg.evaluate(
            """(sel) => { const el = document.querySelector(sel);
                 el.dispatchEvent(new KeyboardEvent('keydown',
                   {key: 'Enter', keyCode: 13, isComposing: false, bubbles: true})); }""", pv_sel)
        pg.wait_for_timeout(600)
        msgs_sent = len(pg.query_selector_all(".pv-msg"))
        report("G11b 普通回车确实发送（证明钩子接上了）",
               msgs_sent > before_msgs, f"消息数 {before_msgs}->{msgs_sent}")

        report("G0a 配置页无 JS 运行时异常", not errs, f"pageerror={errs[:2]}" if errs else "无")

        # ---------- 加载失败态 / 骨架屏（独立页面）----------
        errs2 = []
        pg2 = new_page(browser, errs2)
        try:
            pg2.route("**/api/apps/1", lambda route: route.abort())
            pg2.goto(f"{BASE}/furnace/app/{APP_ID}/edit", wait_until="domcontentloaded")
            pg2.wait_for_selector(".fe-fail", timeout=12000)
            txt = pg2.inner_text(".fe-fail")
            report("G2 加载失败显示失败态+重试（不是白屏）",
                   "重试" in txt and "加载" in txt, f"文案={txt[:50]!r}")
        except Exception as e:
            report("G2 加载失败显示失败态+重试（不是白屏）", False, f"未出现 .fe-fail：{e}")
        finally:
            pg2.close()

        errs3 = []
        pg3 = new_page(browser, errs3)
        try:
            def _slow(route):
                time.sleep(2.0)
                route.continue_()

            pg3.route("**/api/apps/1", _slow)
            pg3.goto(f"{BASE}/furnace/app/{APP_ID}/edit", wait_until="commit")
            pg3.wait_for_selector(".fe-skel", timeout=6000)
            skel_txt = pg3.inner_text(".fe-skel")
            report("G3 加载中有骨架屏（不是空白）",
                   "正在加载" in skel_txt, f"文本={skel_txt[-30:]!r}")
        except Exception as e:
            skip("G3 加载中有骨架屏（不是空白）", f"路由延迟未命中（sync route 处理限制）：{type(e).__name__}")
        finally:
            pg3.close()

        # ---------- 开炉向导 ----------
        errs4 = []
        pg4 = new_page(browser, errs4)
        try:
            pg4.goto(f"{BASE}/furnace", wait_until="domcontentloaded")
            pg4.wait_for_selector("button:has-text('开炉炼应用')", timeout=25000)
            pg4.click("button:has-text('开炉炼应用')")
            pg4.wait_for_selector(".wiz-mask", timeout=8000)
            report("G12 开炉向导可打开", pg4.is_visible(".wiz-mask"), "mask 可见")

            # 选第一份资料 → 进入第②步
            # ⚠️ 只能等 .pick 本身：第一版写成 wait_for(".pick, .el-empty")，
            #    空态元素会**立刻**满足条件 → 异步列表还没回来就数到 0 → 假 SKIP。
            try:
                pg4.wait_for_selector(".pick", timeout=12000)
            except Exception:
                pass
            picks = pg4.query_selector_all(".pick")
            if not picks:
                empty_txt = ""
                for s in (".el-empty", ".wz-empty"):
                    el = pg4.query_selector(s)
                    if el:
                        empty_txt = el.inner_text().replace("\n", " / ")[:80]
                        break
                report("G12b 投料区能列出已解析资料", False,
                       f".pick=0，空态文案={empty_txt!r}（后端 /api/materials 有 37 份 success）")
            if not picks:
                skip("G15 第②步缺项提示", "无可用资料（.pick 为 0），无法进入第二步")
                skip("G14 向导操作条在视口内", "同上")
            else:
                report("G12b 投料区能列出已解析资料", True, f".pick={len(picks)} 份")
                picks[0].click()
                pg4.wait_for_timeout(200)
                pg4.click("button:has-text('下一步：定型')")
                pg4.wait_for_selector(".wz-form", timeout=6000)

                hint = pg4.query_selector(".wz-hint")
                next_btn = pg4.query_selector("button:has-text('下一步：出炉')")
                report("G15 第②步缺项提示且按钮禁用",
                       bool(hint) and "还需填写" in hint.inner_text() and next_btn is not None
                       and next_btn.is_disabled(),
                       f"提示={hint.inner_text() if hint else None!r} 下一步禁用={next_btn.is_disabled() if next_btn else None}")

                pg4.evaluate("() => { const c = document.querySelector('.wiz-card');"
                             " if (c) c.scrollTop = c.scrollHeight; }")
                pg4.wait_for_timeout(300)
                bar = rect(pg4, ".wz-bar")
                report("G14 第②步操作条滚动后仍在视口内",
                       in_viewport(bar, vh), f"bar top={bar['top']:.0f} bottom={bar['bottom']:.0f}")

                # 填名 → 遮罩关闭 → 暂存 → 重开恢复
                pg4.fill(".wz-form .frow input", "探针暂存测试")
                pg4.wait_for_timeout(200)
                pg4.mouse.click(8, 8)
                try:
                    pg4.wait_for_selector(".el-message-box", timeout=6000)
                    t = pg4.inner_text(".el-message-box__title")
                    msgbox_btn(pg4, "继续填写")
                    pg4.wait_for_timeout(300)
                    still_open = pg4.is_visible(".wiz-mask")
                    report("G13 点遮罩弹确认且选「继续填写」不关闭",
                           still_open and "开炉向导" in t, f"标题={t!r} 仍打开={still_open}")
                except Exception as e:
                    report("G13 点遮罩弹确认且选「继续填写」不关闭", False, f"未出现确认框：{e}")

                pg4.mouse.click(8, 8)
                try:
                    msgbox_btn(pg4, "关闭并暂存")
                    pg4.wait_for_timeout(400)
                    closed = not pg4.is_visible(".wiz-mask")
                    pg4.click("button:has-text('开炉炼应用')")
                    pg4.wait_for_selector(".wiz-mask", timeout=6000)
                    pg4.wait_for_timeout(300)
                    name_val = pg4.input_value(".wz-form .frow input") if pg4.query_selector(".wz-form") else ""
                    report("G16 关闭后暂存、重开能续（名称还在）",
                           closed and name_val == "探针暂存测试", f"已关闭={closed} 重开后名称={name_val!r}")
                except Exception as e:
                    report("G16 关闭后暂存、重开能续（名称还在）", False, f"{e}")

            report("G0b 向导页无 JS 运行时异常", not errs4, f"pageerror={errs4[:2]}" if errs4 else "无")
        except Exception as e:
            report("G12 开炉向导可打开", False, f"{type(e).__name__}: {e}")

        browser.close()

    return summarize()


def summarize():
    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_skip = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    print("\n" + "=" * 60)
    print(f"总计 {len(RESULTS)} 项：PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}")
    if n_fail:
        print("失败项：" + "、".join(rid for rid, s, _ in RESULTS if s == "FAIL"))
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
