# -*- coding: utf-8 -*-
"""N2 探针（2026-10-26）：/read 临时阅读页页面级验收（此前只能 SKIP，见优化盘点 §10-N2）

单隔离实例自闭环：POST /ephemeral/open（text 直通，不发真实网络请求）在实例内存里
造快照 → 浏览器打同一实例的 /read?key=...（隔离实例同样 serve dist）。
零真实库写入、零外网请求；收尾 DELETE /ephemeral/recent/{key} 清内存快照。

判据：
  - 页面锚点：标题 / 「仅本次阅读」标记 / ep-block 分段 / 来源链接 target=_blank
  - md 管线：加粗渲染、代码块头部条（复制 + 运行按钮，A4 功能在此页同样生效）
  - 消毒实测：onerror / javascript: 伪协议 / <script> 全灭，window.__xss 未定义
  - 负向：/read 无 key → 「没有指定要阅读的内容」空态
  - API 卫生：recent 含 key → DELETE 后不再含（清场即验证）
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
WS = REPO.parent
BACKEND = REPO / "backend"
TMP = WS / "_tmp_probe_read_20261026"
CHROME = BACKEND / "data" / "browsers" / "chromium-1200" / "chrome-win64" / "chrome.exe"
RESULTS = []
TITLE = "_probe_临时阅读验收"
URL = "https://probe.invalid/read-e2e"
MD_TEXT = """这是一段**加粗**的临时阅读正文，用于验收 /read 页面的渲染管线。

```js
console.log('probe-read')
```

正文中有一个[外部链接](https://example.com/x)需要新窗口打开。

<img src="x" onerror="window.__xss=1">

[伪协议链接](javascript:alert(1))

<script>window.__xss2 = 1</script>

""" + "临时阅读正文填充段落，确保长度足够通过直通校验。" * 6


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {rid}  {detail}")


TMP.mkdir(parents=True, exist_ok=True)
(TMP / "files").mkdir(exist_ok=True)
(TMP / "chroma").mkdir(exist_ok=True)
_db = TMP / "app.db"
if _db.exists():
    _db.unlink()

REAL_BGE = BACKEND / "data" / "models" / "bge-small-zh-v1.5"
ISO_BGE = TMP / "models" / "bge-small-zh-v1.5"
for src in REAL_BGE.rglob("*"):
    if src.is_file():
        dst = ISO_BGE / src.relative_to(REAL_BGE)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            os.link(src, dst)

s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
env = dict(os.environ)
env["ASC_DATA_DIR"] = str(TMP)
env["ASC_FILES_DIR"] = str(TMP / "files")
env["ASC_DB_URL"] = f"sqlite:///{TMP / 'app.db'}"
env["ASC_CHROMA_DIR"] = str(TMP / "chroma")
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = f"http://127.0.0.1:{PORT}/api"
APP = f"http://127.0.0.1:{PORT}"


def http_json(method, path, body=None, timeout=20, expect_error=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if expect_error:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        raise


def wait_ready():
    for _ in range(60):
        try:
            with opener.open(BASE + "/version", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


try:
    if not wait_ready():
        report("READY", False, "隔离实例未就绪")
        sys.exit(1)
    report("READY", True, f"port={PORT}")

    # ---------- API：造快照 ----------
    code, d = http_json("POST", "/ai/ephemeral/open",
                        {"url": URL, "title": TITLE, "text": MD_TEXT})
    key = d.get("key")
    report("A1 open 直通成功（key + chunks）",
           code == 200 and bool(key) and (d.get("chunk_count") or 0) >= 1,
           f"code={code} key={key} chunks={d.get('chunk_count')}")
    report("A2 标题透传", d.get("title") == TITLE, str(d.get("title")))

    code, recent = http_json("GET", "/ai/ephemeral/recent")
    items = recent["items"] if isinstance(recent, dict) else recent
    keys = [x.get("key") for x in items]
    report("A3 最近阅读含该快照", key in keys, f"keys={keys}")

    # ---------- 页面级 ----------
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME))
        page = browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{APP}/read?key={key}", wait_until="domcontentloaded")
        # 确定性等待：正文（v-else）或空态（loadError）任一出现即落定，不再拍脑袋 sleep
        page.wait_for_selector(".rp-content, .rp-empty", timeout=15000)
        if not page.locator(".rp-content").count():
            dump = page.locator("body").inner_text()[:200].replace("\n", " | ")
            print(f"[diag] 正文未渲染，body= {dump}")
        page.wait_for_timeout(400)

        title_txt = page.locator(".rp-title").inner_text() if page.locator(".rp-title").count() else ""
        report("P1 标题渲染", TITLE in title_txt, title_txt)
        report("P2 「仅本次阅读」标记可见",
               page.locator(".rp-ephemeral").count() > 0, "")

        blocks = page.locator(".ep-block")
        report("P3 正文分段渲染（ep-block ≥ 1）", blocks.count() >= 1, f"n={blocks.count()}")
        html = page.locator(".rp-content").inner_html()
        report("P4 加粗渲染（md 管线生效）", "<strong>加粗</strong>" in html, "")
        report("P5 代码块头部条（复制+运行按钮齐全）",
               "md-code-head" in html and "md-code-copy" in html and "md-code-run" in html, "")
        link = page.locator('.rp-content a[href="https://example.com/x"]')
        report("P6 正文外链 target=_blank",
               link.count() > 0 and link.first.get_attribute("target") == "_blank", "")
        src = page.locator(".rp-source-url")
        report("P7 来源链接展示且新窗口",
               src.count() > 0 and URL in (src.first.inner_text() or "")
               and src.first.get_attribute("target") == "_blank", "")

        xss = page.evaluate("(() => ({ x1: window.__xss, x2: window.__xss2 }))()")
        report("P8 XSS 未执行（__xss/__xss2 均为空）",
               xss["x1"] is None and xss["x2"] is None, str(xss))
        n_img = page.locator(".rp-content img").count()
        n_script = page.locator(".rp-content script").count()
        n_jslink = page.locator('.rp-content a[href^="javascript:"]').count()
        n_onerr = page.locator(".rp-content [onerror]").count()
        report("P9 DOM 级消毒：无 img/script 元素、无 javascript: 链接、无 onerror 属性",
               n_img == 0 and n_script == 0 and n_jslink == 0 and n_onerr == 0,
               f"img={n_img} script={n_script} jslink={n_jslink} onerror={n_onerr}")
        report("P10 零 console/page 错误", not errors, errors[:3])
        browser.close()

        # 负向：无 key 打开 → 空态
        b2 = pw.chromium.launch(executable_path=str(CHROME))
        p2 = b2.new_page()
        p2.goto(f"{APP}/read", wait_until="domcontentloaded")
        p2.wait_for_selector(".rp-content, .rp-empty", timeout=15000)
        empty = p2.locator(".rp-empty")
        etxt = empty.inner_text() if empty.count() else ""
        report("N1 无 key 打开 → 空态提示「没有指定要阅读的内容」",
               "没有指定要阅读的内容" in etxt, etxt[:60])
        b2.close()

    # ---------- 清场即验证 ----------
    code, _ = http_json("DELETE", f"/ai/ephemeral/recent/{key}")
    code2, recent2 = http_json("GET", "/ai/ephemeral/recent")
    items2 = recent2["items"] if isinstance(recent2, dict) else recent2
    keys2 = [x.get("key") for x in items2]
    report("C1 快照已删除（recent 不再含 key）", code == 200 and key not in keys2,
           f"del={code} keys={keys2}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()

npass = sum(1 for r in RESULTS if r[1] == "PASS")
nfail = sum(1 for r in RESULTS if r[1] == "FAIL")
print(f"\n===== 总计 {npass} PASS / {nfail} FAIL =====")
sys.exit(1 if nfail else 0)
