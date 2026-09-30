"""知识图谱实体+关系抽取质量探测（只读，不改任何生产代码/数据）

目的：在决定「知识图谱功能怎么做」之前，先实测 LLM 对真实知识库做实体/关系抽取的
质量到底行不行——噪声比例、漏抽、类型约束有效性。这是「先实测、再下结论」的落地，
也补上《多路召回检索方案》P1 一直没做的验证步骤。

安全边界：
- 只读 app.db（sqlite3 直连，readonly 模式），不写生产数据库；
- 复用项目现有 llm.py 的 resolve_model + 调用链，但只在内存里拼 messages、
  直接 OpenAI 调用，不 import 会改动状态的模块；
- 全部输出落到 stdout / 结果 JSON 文件，不改任何 .py 生产文件、不写 DB。

用法：
    python scripts/probe_entity_extract.py --materials 2,7 --max-chunks 6 --out probe_out.json
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

# 实时输出进度（管道下 Python 默认全缓冲，LLM 逐块调用耗时会让 stdout 迟迟不出现）
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# 让 backend 包可 import（脚本运行时 cwd 应为 backend/）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 限定实体类型，给 LLM 强约束（学习场景闭环类型，避免开放类型导致图谱变蜘蛛网）
ENTITY_TYPES = ("人物", "产品", "概念", "知识点", "方法")

EXTRACT_SYSTEM = """你是一名知识抽取助手。请从给定的学习材料片段中抽取实体与关系，用于构建个人知识图谱。

实体类型严格限定为以下 5 类（不要引入其他类型）：
- 人物：真实或虚拟的人物、作者、专家
- 产品：具体产品、工具、软件、平台、App 名称
- 概念：有明确定义的专业概念、术语（如"费曼学习法""用户价值公式""需求管理"）
- 知识点：材料中要说明的一条可记忆的要点/结论
- 方法：方法论、流程、步骤、模型、框架（如"RICE 优先级排序""AARRR 模型"）

抽取要求：
1. 只抽取这段文字中**明确出现**的实体，不要凭空联想、不要编造片段没有的内容；
2. "用户""系统""我们""你"这类泛称、以及"的""了""是"等虚词**绝不能**作为实体；
3. 每个实体给一个 ≤20 字的标准名称；
4. 关系只抽取**明确表达**的关系（如"A 提出了 B""B 属于 A""A 用来做 B""A 包含 B"），
   关系名用一个简短动词短语（≤10 字），不要臆造关系；
5. 同一片段内实体重复出现只列一次。

严格输出 JSON（不要输出任何其他内容，不要 Markdown 代码块），格式：
{"entities": [{"name": "实体名", "type": "人物/产品/概念/知识点/方法"}],
 "relations": [{"src": "实体A名", "rel": "关系", "dst": "实体B名"}]}"""


def build_client():
    """复用项目配置，返回 (base_url, api_key, model)。不依赖 openai SDK，走原生 urllib。"""
    base = Path(__file__).resolve().parent.parent
    store = json.loads((base / "data" / "llm_settings.json").read_text(encoding="utf-8"))

    def resolve(mid):
        for m in store.get("llm_models") or []:
            if m.get("id") == mid:
                return (m.get("base_url") or store.get("llm_base_url"),
                        m.get("api_key") or store.get("llm_api_key"),
                        m.get("model") or "")
        return (store.get("llm_base_url"), store.get("llm_api_key"), "")

    b, k, model = resolve(store.get("summary_model_id") or store.get("chat_model_id"))
    if not model:
        b, k, model = resolve("legacy-chat")
    if not k:
        raise SystemExit("未找到可用 API Key，请先配置 LLM")
    return b, k, model


def load_db(db_path: str):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    return con


def get_materials(con, ids):
    if ids:
        q = "SELECT id,title,format FROM materials WHERE id IN (%s) ORDER BY id" % ",".join("?" * len(ids))
        rows = con.execute(q, ids).fetchall()
    else:
        rows = con.execute("SELECT id,title,format FROM materials ORDER BY id").fetchall()
    return rows


def get_chunks(con, mid, limit):
    rows = con.execute(
        "SELECT id,content,page_no FROM material_chunks WHERE material_id=? ORDER BY page_no,id LIMIT ?",
        (mid, limit)).fetchall()
    return rows


def extract(base_url, api_key, model, text: str) -> str:
    """原生 urllib 调 OpenAI 兼容 chat/completions，返回 message.content 原文。"""
    import urllib.request
    import urllib.error
    url = (base_url or "").rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": f"学习材料片段：\n\n{text}"},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {e.code}: {body[:300]}")
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"返回结构异常：{json.dumps(data, ensure_ascii=False)[:300]}")


def parse_json(raw: str):
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.lower().startswith("json"):
            txt = txt[4:]
    s, e = txt.find("{"), txt.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(txt[s:e + 1])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--materials", help="逗号分隔的材料 id；留空=全部")
    ap.add_argument("--max-chunks", type=int, default=6, help="每个材料抽样 chunk 数")
    ap.add_argument("--chunk-chars", type=int, default=2000, help="每 chunk 截断字数")
    ap.add_argument("--db", default=None, help="app.db 路径；默认 backend/data/app.db")
    ap.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    db_path = args.db or str(base / "data" / "app.db")
    ids = [int(x) for x in args.materials.split(",") if x.strip()] if args.materials else []

    base_url, api_key, model = build_client()
    print(f"[probe] 使用模型：{model}")
    con = load_db(db_path)
    mats = get_materials(con, ids)
    print(f"[probe] 待抽取材料 {len(mats)} 个：{[m[1] for m in mats]}")

    results = []
    total_ent = total_rel = 0
    for mid, title, fmt in mats:
        chunks = get_chunks(con, mid, args.max_chunks)
        if not chunks:
            print(f"  - [{mid}] {title}：无块，跳过")
            continue
        print(f"\n=== [{mid}] {title} ({fmt}, 抽 {len(chunks)} 块) ===")
        mat_res = {"material_id": mid, "title": title, "format": fmt, "chunks": []}
        for cid, content, page in chunks:
            text = (content or "").strip()
            if not text:
                continue
            text = text[:args.chunk_chars]
            raw = extract(base_url, api_key, model, text)
            obj = parse_json(raw)
            ents = (obj or {}).get("entities") or []
            rels = (obj or {}).get("relations") or []
            mat_res["chunks"].append({
                "chunk_id": cid, "page_no": page,
                "raw": raw, "parsed": obj, "parse_ok": obj is not None,
                "entities": ents, "relations": rels,
            })
            total_ent += len(ents)
            total_rel += len(rels)
            print(f"  [chunk {cid} P{page}] 实体 {len(ents)} 关系 {len(rels)}"
                  f"{'  ⚠️解析失败' if obj is None else ''}")
            for e in ents:
                print(f"      · {e.get('type','?'):4s} | {e.get('name','')}")
            for r in rels:
                print(f"        ~ {r.get('src','')} --{r.get('rel','')}--> {r.get('dst','')}")
        results.append(mat_res)

    print(f"\n[probe] 合计：实体 {total_ent}，关系 {total_rel}，"
          f"来自 {sum(1 for r in results for c in r['chunks'] if c['parse_ok'])} 个成功解析块")

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[probe] 结果已写入 {args.out}")


if __name__ == "__main__":
    main()
