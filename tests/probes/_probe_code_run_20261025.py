# -*- coding: utf-8 -*-
"""A4 代码运行 sandbox 验收探针（2026-10-25 · dev 5173 直引 md.js）

判据：
  R1 运行按钮只在 html/js 块（4 个代码块：html/js/python/无语言 → 恰好 2 个按钮）
  R2 点 js 运行 → 面板出现 + iframe sandbox 恰好 'allow-scripts'（无 same-origin）
  R3 console 输出经 tap+postMessage 回传面板（hello-asc 2）
  R4 再点关闭 → 面板移除（toggle）
  R5 XSS 隔离：html 里试图写 parent.document.body.dataset.hacked → 父页面无该属性
     （这是分辨力主判据：sandbox 若没生效，这里必被写上）
  R6 全程零 pageerror
  NEG sandbox 判据分辨力：无 sandbox 的同 srcdoc iframe，父页面**能**读到其 DOM（对照）
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

CHROME = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\backend\data\browsers\chromium-1200\chrome-win64\chrome.exe"
DEV = "http://127.0.0.1:5173"

MD_TEXT = """```html
<div id="hm">html-ok</div>
<script>try{parent.document.body.dataset.hacked='1'}catch(e){};document.getElementById('hm').textContent='html-ran'</script>
```

```js
console.log('hello-asc', 1 + 1)
```

```python
print("不应有运行按钮")
```

```
无语言代码块也不应有运行按钮
```
"""

fails, oks = [], []


def ok(name, cond, detail=""):
    (oks if cond else fails).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  | " + str(detail)[:160]) if detail else ""))


def wait_dev():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with op.open(DEV, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


assert wait_dev(), "5173 dev 未就绪"

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=CHROME, headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page_errors = []
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    page.goto(DEV + "/", wait_until="domcontentloaded")
    page.evaluate("localStorage.setItem('asc_onboarded','1')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1200)

    setup = page.evaluate(
        "async (mdText) => {"
        "  const m = await import('/src/utils/md.js');"
        "  if (typeof m.renderMd !== 'function') return { fatal: 'renderMd 缺失' };"
        "  window.__probeMsgs = [];"
        "  window.addEventListener('message', (e) => { if (e.data && e.data.__ascRun) window.__probeMsgs.push(e.data) });"
        "  const md = m.createMd({ breaks: true });"
        "  const box = document.createElement('div');"
        "  box.id = '__probe_run_box';"
        "  box.innerHTML = m.renderMd(md, mdText);"
        "  document.body.appendChild(box);"
        "  return { runBtns: box.querySelectorAll('.md-code-run').length,"
        "           blocks: box.querySelectorAll('.md-code').length };"
        "}", MD_TEXT)
    if setup.get("fatal"):
        ok("R0 模块加载", False, setup["fatal"])
        print("===== 提前终止 =====")
        sys.exit(1)
    ok("R1 运行按钮恰好 2 个（html+js；python/无语言没有）",
       setup["runBtns"] == 2 and setup["blocks"] == 4, setup)

    # R2/R3：点 js 块的运行按钮（第 2 个）
    page.evaluate("document.querySelectorAll('#__probe_run_box .md-code-run')[1].click()")
    page.wait_for_timeout(300)
    info = page.evaluate(
        "(() => { const box = document.getElementById('__probe_run_box');"
        "  const panel = box.querySelector('.md-code-run-panel');"
        "  if (!panel) return { panel: false };"
        "  const f = panel.querySelector('iframe');"
        "  return { panel: true, sandbox: f ? f.getAttribute('sandbox') : null };"
        "})()")
    ok("R2 面板出现且 sandbox 恰好=allow-scripts（无 same-origin）",
       info.get("panel") and info.get("sandbox") == "allow-scripts", info)

    out_text = ""
    for _ in range(30):
        time.sleep(0.3)
        out_text = page.evaluate(
            "(() => { const o = document.querySelector('#__probe_run_box .md-code-run-out');"
            "  return o ? o.textContent : ''; })()")
        if "hello-asc" in out_text:
            break
    ok("R3 console 输出回传面板（含 'hello-asc 2'）", "hello-asc 2" in out_text, out_text[:80])

    # R4：再点关闭（按钮文本已变「关闭」）
    page.evaluate("document.querySelectorAll('#__probe_run_box .md-code-run')[1].click()")
    page.wait_for_timeout(300)
    ok("R4 再点关闭面板（toggle）",
       page.evaluate("!document.querySelector('#__probe_run_box .md-code-run-panel')"))

    # R5：html 块运行（含 parent 写尝试）→ 桥消息收到 + 父页面无污染
    page.evaluate("document.querySelectorAll('#__probe_run_box .md-code-run')[0].click()")
    got_done = False
    for _ in range(30):
        time.sleep(0.3)
        got_done = page.evaluate(
            "window.__probeMsgs.some((m) => m.__ascRun === 'done')")
        if got_done:
            break
    hacked = page.evaluate("document.body.dataset.hacked")
    ok("R5 XSS 隔离：父页面无 dataset.hacked（sandbox 真生效）",
       got_done and hacked is None, f"done={got_done} hacked={hacked}")

    # NEG：无 sandbox 的同 srcdoc iframe，父页面**能**读其 DOM（证明 sandbox 判据有分辨力）
    # ⚠️ 必须等 load 再读 contentDocument（异步装载，立刻读恒 null → 上一轮因此假红）
    neg = page.evaluate(
        "() => new Promise((resolve) => {"
        "  const f = document.createElement('iframe');"
        "  f.srcdoc = '<div id=\"x\">1</div>';"
        "  let done = false;"
        "  const fin = (v) => { if (!done) { done = true; f.remove(); resolve(v) } };"
        "  f.onload = () => {"
        "    let ok2 = false;"
        "    try { ok2 = !!(f.contentDocument && f.contentDocument.getElementById('x')); }"
        "    catch (e) { ok2 = false; }"
        "    fin(ok2);"
        "  };"
        "  document.body.appendChild(f);"
        "  setTimeout(() => fin(false), 2000);"
        "})")
    ok("NEG 对照：无 sandbox 的同 srcdoc iframe 父页面可读其 DOM", neg)

    ok("R6 全程零 pageerror", not page_errors, page_errors[:2])
    page.evaluate("document.getElementById('__probe_run_box')?.remove()")
    browser.close()

print("\n===== 总计: %d PASS / %d FAIL =====" % (len(oks), len(fails)))
for f in fails:
    print("FAILED:", f)
sys.exit(1 if fails else 0)
