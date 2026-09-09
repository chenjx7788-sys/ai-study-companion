"""全链路回归冒烟：改动代码后跑一遍，确认主流程没有回归

用法：backend 目录下，服务已启动时运行
    python e2e_regression.py

覆盖：健康检查 / 材料列表 / 详情+章节 / chunks / 知识库总览+检索 /
      问答流（范围隔离+指定条目）/ 会话 CRUD / 复习（队列+统计）/
      设置读取 / 存储统计 / Token 用量
不调用真实 LLM 生成（摘要/知识点/出题费时费 token），只验证接口骨架。
"""
import json
import sys
import requests

BASE = "http://127.0.0.1:8000/api"
FAILURES = []


def check(name, cond, extra=""):
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


print("== 1. 基础 ==")
r = requests.get(f"{BASE}/health", timeout=5)
check("健康检查", r.status_code == 200 and r.json()["status"] == "ok")

mats = requests.get(f"{BASE}/materials", timeout=10).json()
check("材料列表非空", len(mats) >= 1, f"got {len(mats)}")
ok_mats = [m for m in mats if m["parsed_status"] == "success"]
check("存在解析成功材料", len(ok_mats) >= 1)
if not ok_mats:
    print("\n没有可用材料，后续用例跳过")
    sys.exit(1)
m0 = ok_mats[0]

print(f"== 2. 材料（用「{m0['title']}」）==")
d = requests.get(f"{BASE}/materials/{m0['id']}", timeout=10).json()
check("详情含 sections 字段", "sections" in d)
chunks = requests.get(f"{BASE}/materials/{m0['id']}/chunks", timeout=10).json()
check("chunks 非空", len(chunks) >= 1)

print("== 3. 知识库 ==")
ov = requests.get(f"{BASE}/kb/overview", timeout=10).json()
check("总览含材料与笔记", "materials" in ov and "notes" in ov)
hits = requests.post(f"{BASE}/kb/search", json={"query": "核心概念"}, timeout=120).json()
check("向量检索返回结果", isinstance(hits, list))

print("== 4. 问答（只读到 sources 事件即关流，不等 LLM） ==")

def ask_sources(payload):
    r = requests.post(f"{BASE}/chat/ask/stream", json=payload, stream=True, timeout=60)
    p = None
    for line in r.iter_lines(decode_unicode=True):
        if line.startswith("data:") and "kb_hit" in line:
            p = json.loads(line[5:])
            break
    r.close()
    return p

p_all = ask_sources({"question": "测试问题", "scope": "all"})
check("全库问答返回 sources 事件", p_all is not None)
p_note = ask_sources({"question": "测试", "scope": "note"})
check("笔记范围只命中笔记", p_note is not None and all(s["ref_type"] == "note" for s in p_note["sources"]))

print("== 5. 会话 CRUD ==")
s = requests.post(f"{BASE}/chat/sessions", timeout=10).json()
r = requests.patch(f"{BASE}/chat/sessions/{s['id']}", json={"title": "回归测试"}, timeout=10).json()
check("会话重命名", r["title"] == "回归测试")
requests.delete(f"{BASE}/chat/sessions/{s['id']}", timeout=10)
check("会话删除", all(x["id"] != s["id"] for x in requests.get(f"{BASE}/chat/sessions", timeout=10).json()))

# 清理问答测试产生的会话
for x in requests.get(f"{BASE}/chat/sessions", timeout=10).json():
    if x["title"] in ("测试问题", "测试"):
        requests.delete(f"{BASE}/chat/sessions/{x['id']}", timeout=10)

print("== 6. 复习 ==")
st = requests.get(f"{BASE}/review/stats", timeout=10).json()
check("复习统计字段", all(k in st for k in ("total", "due", "reviewed_today", "mastered")))
requests.get(f"{BASE}/review/today", timeout=10).json()
check("今日队列可查询", True)

print("== 7. 设置 ==")
st_conf = requests.get(f"{BASE}/settings", timeout=10).json()
check("设置含提示词默认模板", "prompt_defaults" in st_conf and "prompt_review" in st_conf["prompt_defaults"])
sto = requests.get(f"{BASE}/settings/storage", timeout=10).json()
check("存储统计", "files_mb" in sto)
usg = requests.get(f"{BASE}/settings/usage", timeout=10).json()
check("Token 用量结构", "today" in usg and "_total" in usg["today"])

print("== 8. 新增能力（V2/增强）==")
ft = requests.get(f"{BASE}/materials/search/fulltext", params={"q": "学习"}, timeout=10).json()
check("全文搜索可用", isinstance(ft, list))
check("全文搜索结果结构", not ft or all("material_id" in x and "page_no" in x and "snippet" in x for x in ft))

exp = requests.get(f"{BASE}/materials/{m0['id']}/notes/export", timeout=10)
check("笔记导出接口", exp.status_code == 200 and "attachment" in exp.headers.get("content-disposition", ""))

media = [m for m in mats if m["format"] in ("mp3", "wav", "m4a", "mp4") and m["parsed_status"] == "success"]
if media:
    md = requests.get(f"{BASE}/materials/{media[0]['id']}/chunks", timeout=10).json()
    check("音视频转写块存在", len(md) >= 1)
    check("音视频分钟页码语义", md[0]["page_no"] >= 1)
else:
    check("音视频材料存在", False, "无音视频材料（可忽略）")

cards = requests.get(f"{BASE}/review/cards", timeout=10).json()
check("复习卡片列表", isinstance(cards, list))
st7 = requests.get(f"{BASE}/settings", timeout=10).json()
check("第 7 条提示词（推荐问题）", "prompt_suggest" in st7["prompt_defaults"])
check("第 9 条提示词（测一测）", "prompt_quiz" in st7["prompt_defaults"])

print()
if FAILURES:
    print(f"回归失败 {len(FAILURES)} 项: {FAILURES}")
    sys.exit(1)
print("全部回归用例通过")
