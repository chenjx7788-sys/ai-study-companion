# -*- coding: utf-8 -*-
"""AI 问答渲染升级门禁探针（2026-10-23 · P0/P1/P2）

目标实例：5173（vite dev，新代码；/api 代理到 8000）。

判据（不只断言「渲染出来了」）：
  C1 代码块增强：.md-code 存在、语言标签 == python、内含 hljs-* 高亮 span
  C2 复制按钮：点击后文案变「已复制」
  C3 mermaid 合法块 → 水合成 svg（.mermaid-done，懒加载允许 30s）
  C4 mermaid 非法块 → 降级 .mermaid-error + 原文代码块（不崩整条回答）
  C5 图片点击 → .el-image-viewer__wrapper 出现，可关闭
  N1 负向自检（判据分辨力）：同会话纯文本气泡内不得有 .md-code / .mermaid-pending
  N2 消毒证据：构建产物内存在 DOMPurify（purify）代码（运行时难从 markdown 触发，静态断言）
  E0 全程无 console error（模块加载/水合失败必现）

种子：直插 sqlite（1 会话 + 2 条 assistant 消息），跑完 DELETE API 清会话（与既有探针同规）。
"""
import json, os, sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright

CHROME = r"C:\Users\陈锦祥\AppData\Local\ms-playwright\chromium-1223\chrome-win64\chrome.exe"
DB = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\backend\data\app.db"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5173"
OUT = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\_probe_shots_md_enhance_20261023"
os.makedirs(OUT, exist_ok=True)

TITLE = "_probe_md_enhance_20261023"
PNG_1PX = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

RICH = """好的，这是一个包含代码、流程图和图片的回答。

```python
def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
```

流程如下：

```mermaid
flowchart LR
    A[开始] --> B[处理]
    B --> C[结束]
```

语法错误的图：

```mermaid
flowchart LR
    A[未闭合 --> -->

```

配图：
![探针测试图](DATAURI)
""".replace("DATAURI", PNG_1PX)

PLAIN = "这是一条纯文本回答，没有代码块也没有流程图。"

def seed():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("DELETE FROM chat_messages WHERE session_id IN (SELECT id FROM chat_sessions WHERE title=?)", (TITLE,))
    cur.execute("DELETE FROM chat_sessions WHERE title=?", (TITLE,))
    cur.execute(
        "INSERT INTO chat_sessions (title, pinned, created_at, updated_at) VALUES (?, 0, datetime('now'), datetime('now'))",
        (TITLE,))
    sid = cur.lastrowid
    cur.execute(
        "INSERT INTO chat_messages (session_id, role, content, reasoning, kb_hit, error, sources, suggestions, created_at)"
        " VALUES (?, 'assistant', ?, '', 1, '', '[]', '[]', datetime('now'))",
        (sid, RICH))
    cur.execute(
        "INSERT INTO chat_messages (session_id, role, content, reasoning, kb_hit, error, sources, suggestions, created_at)"
        " VALUES (?, 'assistant', ?, '', 1, '', '[]', '[]', datetime('now'))",
        (sid, PLAIN))
    con.commit()
    con.close()
    return sid

results = []
def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS" if ok else "FAIL"), name, "|", str(detail)[:160])

sid = seed()
print("seeded session id =", sid)

errors = []
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=CHROME, headless=True)
    pg = browser.new_page(viewport={"width": 1400, "height": 900})
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(BASE + "/chat", wait_until="networkidle")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=os.path.join(OUT, "0_chat_home.png"))

    # 打开种子会话（侧栏按标题点）
    item = pg.locator(".session-item", has_text=TITLE).first
    if item.count() == 0:
        check("前置:种子会话可见", False, "sidebar 无该会话")
        browser.close()
        sys.exit(1)
    item.click()
    pg.wait_for_timeout(1200)

    bubbles = pg.locator(".msg-list .md-body, .app-main .md-body")
    n = bubbles.count()
    check("前置:两条 assistant 气泡", n >= 2, f"md-body count={n}")
    rich = bubbles.nth(0)
    plain = bubbles.nth(1)

    # C1 代码块增强
    lang = rich.locator(".md-code .md-code-lang").first
    hl = rich.locator(".md-code code .hljs-keyword, .md-code code [class*='hljs-']").first
    check("C1 代码块高亮+语言标签",
          lang.count() > 0 and lang.inner_text() == "python" and hl.count() > 0,
          f"lang={lang.inner_text() if lang.count() else '无'} hl={hl.count()}")

    # C2 复制按钮反馈
    btn = rich.locator(".md-code-copy").first
    if btn.count():
        btn.click()
        pg.wait_for_timeout(300)
        check("C2 复制按钮反馈", btn.inner_text() == "已复制", btn.inner_text())
    else:
        check("C2 复制按钮反馈", False, "无按钮")

    # C3 合法 mermaid → svg（懒加载，留 30s）
    try:
        rich.locator(".mermaid-pending.mermaid-done svg").first.wait_for(timeout=30000)
        check("C3 mermaid 水合成 svg", True)
    except Exception as e:
        check("C3 mermaid 水合成 svg", False, str(e)[:120])
    pg.screenshot(path=os.path.join(OUT, "1_rich_rendered.png"))

    # C4 非法 mermaid → 降级
    err = rich.locator(".mermaid-error")
    check("C4 非法 mermaid 降级", err.count() > 0 and "语法有误" in err.first.inner_text(),
          f"err count={err.count()}")

    # C5 图片点击放大
    img = rich.locator("img").first
    if img.count():
        img.click()
        try:
            pg.locator(".el-image-viewer__wrapper").wait_for(timeout=5000)
            pg.screenshot(path=os.path.join(OUT, "2_img_viewer.png"))
            pg.locator(".el-image-viewer__close").click()
            pg.wait_for_timeout(400)
            gone = pg.locator(".el-image-viewer__wrapper").count() == 0
            check("C5 图片点击放大+关闭", gone)
        except Exception as e:
            check("C5 图片点击放大+关闭", False, str(e)[:120])
    else:
        check("C5 图片点击放大+关闭", False, "气泡内无 img")

    # N1 负向自检：纯文本气泡无任何增强痕迹
    check("N1 纯文本气泡无增强元素",
          plain.locator(".md-code, .mermaid-pending, .mermaid-error").count() == 0,
          f"hits={plain.locator('.md-code, .mermaid-pending, .mermaid-error').count()}")

    browser.close()

# E0 console 错误
real_errors = [e for e in errors if "favicon" not in e]
check("E0 无 console/page 错误", len(real_errors) == 0, real_errors[:2])

# N2 消毒证据（静态）：构建产物或 dev 模块图含 DOMPurify
dist_dir = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\frontend\dist_md_enhance_20261023\assets"
found = False
if os.path.isdir(dist_dir):
    for fn in os.listdir(dist_dir):
        if fn.endswith(".js"):
            try:
                if "purify" in open(os.path.join(dist_dir, fn), encoding="utf-8", errors="ignore").read(2_000_000):
                    found = True
                    break
            except Exception:
                pass
check("N2 构建产物含 DOMPurify", found, f"dir exists={os.path.isdir(dist_dir)}")

# 清理种子（API 删会话 + 核验消息归零）
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    r = urllib.request.Request(BASE + f"/api/chat/sessions/{sid}", method="DELETE")
    st = opener.open(r, timeout=15).status
except Exception as e:
    st = str(e)
con = sqlite3.connect(DB)
left = con.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id=?", (sid,)).fetchone()[0]
con.close()
check("清理:种子会话已删除", left == 0, f"DELETE status={st} left_msgs={left}")

fails = [n for n, ok in results if not ok]
print("=" * 50)
print(f"总计 {len(results)} 项，失败 {len(fails)} 项" + (f"：{fails}" if fails else "，全绿"))
sys.exit(1 if fails else 0)
