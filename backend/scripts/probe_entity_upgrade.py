"""知识图谱学习价值提升探测：验证「关系类型升级 + 孤立实体串联」的可行性。

对比两组提示词在真实材料上的抽取效果：
- 基线：当前提示词（只抽明确关系，不限类型）
- 升级：新增「因果/对比/应用/前提」关系类型引导 + 孤立实体主动串联

只读：只调 LLM，不写库、不改生产代码。
"""
import json
import sys
import urllib.request
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---- 基线提示词（当前生产版）----
BASELINE_SYSTEM = """你是一名知识抽取助手。请从给定的学习材料片段中抽取实体与关系，用于构建个人知识图谱。

实体类型严格限定为以下 5 类（type 用英文枚举）：
- person（人物）、product（产品）、concept（概念）、knowledge（知识点）、method（方法）

抽取要求：
1. 只抽取这段文字中明确出现的实体，不要凭空联想；
2. 泛称（用户/系统/我们）、虚词、单个汉字不要作为实体；
3. 关系只抽取明确表达的关系（如"A 提出了 B""B 属于 A""A 包含 B"），关系名用简短动词短语（≤10 字）。

严格输出 JSON（不要输出其他内容）：
{"entities": [{"name": "实体名", "type": "person/product/concept/knowledge/method"}],
 "relations": [{"src": "实体A名", "rel": "关系", "dst": "实体B名"}]}"""

# ---- 升级提示词（学习价值导向）----
UPGRADED_SYSTEM = """你是一名知识抽取助手。请从给定的学习材料片段中抽取实体与关系，用于构建帮助用户「学习与吸收」的知识图谱。

实体类型严格限定为以下 5 类（type 用英文枚举）：
- person（人物）、product（产品）、concept（概念）、knowledge（知识点）、method（方法）

抽取要求：
1. 只抽取这段文字中明确出现的实体，不要凭空联想；
2. 泛称（用户/系统/我们）、虚词、单个汉字不要作为实体；
3. 实体抽取时，优先把「可记忆的要点/结论」标为 knowledge（而非 concept）——
   例如「用户价值 = 新体验 - 旧体验 - 替换成本」是 knowledge，「用户价值」是 concept；
4. 关系抽取时，除层级关系（包含/属于）外，主动识别以下「学习价值高」的关系类型：
   - 因果：「A 导致 B」「A 是 B 的原因」「因为 A 所以 B」
   - 对比：「A 与 B 的区别」「A 优于 B」「A 相比 B 更…」
   - 应用：「A 用于 B」「A 可以解决 B」「学会 A 能做 B」
   - 前提：「A 是 B 的前提」「学 B 之前要先懂 A」「A 依赖于 B」
   关系名用简短动词短语（≤10 字），不要臆造；
5. 对于「孤立实体」（片段中出现但与其它实体无明显关系的），
   主动判断它是否与主干概念存在隐含关联（如「PC端」是「直播」的载体），若存在则建立关系。

严格输出 JSON（不要输出其他内容）：
{"entities": [{"name": "实体名", "type": "person/product/concept/knowledge/method"}],
 "relations": [{"src": "实体A名", "rel": "关系", "dst": "实体B名"}]}"""


def build_client():
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
    return b, k, model


def call_llm(base_url, api_key, model, system, text):
    url = (base_url or "").rstrip("/") + "/chat/completions"
    payload = {"model": model, "temperature": 0.2,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": f"学习材料片段：\n\n{text}"}]}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    raw = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return raw


def parse_json(raw):
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.lower().startswith("json"): txt = txt[4:]
    s, e = txt.find("{"), txt.rfind("}")
    if s < 0 or e <= s: return {}
    try: return json.loads(txt[s:e+1])
    except: return {}


def analyze(parsed):
    """分析抽取结果：关系类型分布 + 孤立实体数"""
    ents = parsed.get("entities") or []
    rels = parsed.get("relations") or []
    # 关系类型分类
    rel_types = {"层级": 0, "因果": 0, "对比": 0, "应用": 0, "前提": 0, "其它": 0}
    layer_kw = {"包含", "属于", "是", "有", "含"}
    for r in rels:
        rel = r.get("rel", "")
        if any(k in rel for k in layer_kw): rel_types["层级"] += 1
        elif any(k in rel for k in ("导致", "原因", "因为", "所以")): rel_types["因果"] += 1
        elif any(k in rel for k in ("区别", "优于", "相比", "对比", "不同")): rel_types["对比"] += 1
        elif any(k in rel for k in ("用于", "解决", "应用", "能做")): rel_types["应用"] += 1
        elif any(k in rel for k in ("前提", "依赖", "先懂", "基础")): rel_types["前提"] += 1
        else: rel_types["其它"] += 1
    # 孤立实体
    linked = set()
    for r in rels:
        linked.add(r.get("src")); linked.add(r.get("dst"))
    isolated = [e["name"] for e in ents if e["name"] not in linked]
    return rel_types, len(ents), len(rels), len(isolated), isolated[:5]


def main():
    base_url, api_key, model = build_client()
    print(f"[probe] 使用模型：{model}\n")

    # 从真实库取一段有实质内容的 chunk（长度 >500 字，含多概念/对比）
    con = sqlite3.connect("file:C:/Users/陈锦祥/.ai-study-companion/app.db?mode=ro", uri=True)
    chunk = con.execute(
        "SELECT content FROM material_chunks WHERE material_id=2 AND length(content) > 500 LIMIT 1").fetchone()
    con.close()
    text = (chunk[0] if chunk else "直播功能是培训班的核心，支持老师端发起直播、学员端观看直播。")[:2000]

    print("=== 测试文本（前 200 字）===")
    print(text[:200] + "...\n")

    # 基线
    print("=== 基线（当前提示词）===")
    raw_b = call_llm(base_url, api_key, model, BASELINE_SYSTEM, text)
    pb = parse_json(raw_b)
    rel_b, ne_b, nr_b, ni_b, iso_b = analyze(pb)
    print(f"实体 {ne_b} / 关系 {nr_b} / 孤立实体 {ni_b}")
    print(f"关系类型: {rel_b}")
    print(f"孤立样例: {iso_b}\n")

    # 升级
    print("=== 升级（学习价值导向提示词）===")
    raw_u = call_llm(base_url, api_key, model, UPGRADED_SYSTEM, text)
    pu = parse_json(raw_u)
    rel_u, ne_u, nr_u, ni_u, iso_u = analyze(pu)
    print(f"实体 {ne_u} / 关系 {nr_u} / 孤立实体 {ni_u}")
    print(f"关系类型: {rel_u}")
    print(f"孤立样例: {iso_u}\n")

    # 对比结论
    print("=== 对比 ===")
    print(f"关系数: 基线 {nr_b} → 升级 {nr_u}（{'+' if nr_u>nr_b else ''}{nr_u-nr_b}）")
    print(f"孤立实体: 基线 {ni_b} → 升级 {ni_u}（{'-' if ni_u<ni_b else ''}{ni_b-ni_u}）")
    print(f"高价值关系（因果/对比/应用/前提）: 基线 {rel_b['因果']+rel_b['对比']+rel_b['应用']+rel_b['前提']} → 升级 {rel_u['因果']+rel_u['对比']+rel_u['应用']+rel_u['前提']}")


if __name__ == "__main__":
    main()
