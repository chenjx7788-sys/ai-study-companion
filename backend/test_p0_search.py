# -*- coding: utf-8 -*-
"""P0 多路召回验证：全文检索（LIKE）+ RRF 融合 + 规则重排（note 权重 + MMR）

运行：backend 目录下  python test_p0_search.py
全部断言通过则输出 ALL PASS。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.services import fts, search as search_svc   # noqa: E402


def sep(title):
    print(f"\n===== {title} =====")


# ---------- 1. 查询词切分 + 停用词过滤 ----------
sep("1. 查询词切分与停用词")
kws = fts._keywords("费曼学习法是什么？")
assert "费曼学习法" in kws and "是什么" not in kws, kws
kws2 = fts._keywords("讲讲 需求管理 的 优先级")
assert "需求管理" in kws2 and "优先级" in kws2
assert "讲讲" not in kws2 and "的" not in kws2, kws2
print("停用词过滤 ✓", kws, "|", kws2)


# ---------- 2. 全文检索（内存 db） ----------
sep("2. 全文检索 LIKE")
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base, Material, MaterialChunk, Note

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
db = Session()

m1 = Material(title="费曼学习法", format="pdf", parsed_status="success")
m2 = Material(title="需求管理", format="docx", parsed_status="success")
db.add_all([m1, m2])
db.flush()
db.add_all([
    MaterialChunk(material_id=m1.id, content="费曼学习法的核心是以教代学，输出倒逼输入。", page_no=1, section_path=""),
    MaterialChunk(material_id=m1.id, content="产品型号 ABC-123 与 XYZ-789 的区别。", page_no=2, section_path=""),
    MaterialChunk(material_id=m2.id, content="需求管理讲的是优先级排序。", page_no=1, section_path=""),
])
db.add(Note(material_id=m2.id, title="高玮老师笔记", content="需求优先级用 RICE 打分。", anchor={"page_no": 3}))
db.commit()

# 2a. 2 字中文词命中
h = fts.search(db, "费曼")
assert h and any("费曼学习法" in x["text"] for x in h), h
print("2 字中文词命中 ✓", [(x["ref_type"], x["ref_id"]) for x in h])

# 2b. 英文型号命中（含连字符）
h2 = fts.search(db, "ABC-123")
assert h2 and any("ABC-123" in x["text"] for x in h2), h2
print("型号命中 ✓")

# 2c. 笔记标题命中
h3 = fts.search(db, "高玮", kinds=("note",))
assert h3 and h3[0]["ref_type"] == "note" and "高玮" in h3[0]["text"], h3
print("笔记标题命中 ✓")

# 2d. where 过滤 material_id
h4 = fts.search(db, "需求", kinds=("chunk",), where={"material_id": {"$in": [m1.id]}})
assert not any(x["material_id"] == m2.id for x in h4), h4
print("where 过滤 ✓（只命中指定材料）")


# ---------- 3. RRF 倒数排名融合 ----------
sep("3. RRF 融合")


def mk(ref_id, material_id, score, fv, ff):
    return {"ref_type": "chunk", "ref_id": ref_id, "material_id": material_id,
            "text": "x", "metadata": {}, "score": score,
            "from_vector": fv, "from_fts": ff,
            "vec_score": score if fv else None, "fts_score": score if ff else None}


vec = [mk(1, 1, 0.5, True, False), mk(2, 1, 0.4, True, False)]
fts_hits = [mk(2, 1, 2.0, False, True), mk(3, 2, 1.0, False, True)]
merged = search_svc._rrf_fuse([vec, fts_hits])
assert [x["ref_id"] for x in merged] == [2, 1, 3], [x["ref_id"] for x in merged]
assert merged[0]["from_vector"] and merged[0]["from_fts"], "两路都命中的应最靠前"
print("RRF 融合排序 ✓（两路命中靠前）", [(x["ref_id"], x["rrf_score"]) for x in merged])


# ---------- 4. 规则重排：MMR 去重 + note 权重 ----------
sep("4. 规则重排")


def mkr(ref_type, ref_id, material_id, rrf):
    return {"ref_type": ref_type, "ref_id": ref_id, "material_id": material_id,
            "rrf_score": rrf, "score": rrf}


hits = [
    mkr("chunk", 1, 1, 0.040),
    mkr("chunk", 2, 1, 0.038),
    mkr("chunk", 3, 2, 0.036),   # 异材料：应插队到同材料的 chunk2 之前
    mkr("chunk", 4, 1, 0.034),
]
re = search_svc._rerank(hits)
assert re[1]["ref_id"] == 3, f"异材料应插队: {[x['ref_id'] for x in re]}"
print("MMR 异材料去重 ✓（避免连续召回同材料片段）", [x["ref_id"] for x in re])

hits2 = [
    mkr("chunk", 10, 1, 0.030),
    mkr("note", 11, 1, 0.021),   # note 权重 1.5 → 归一化后相关性更高
]
re2 = search_svc._rerank(hits2)
assert re2[0]["ref_type"] == "note", f"note 应加权靠前: {[x['ref_type'] for x in re2]}"
print("note 权重 ✓（低分笔记加权靠前）", [x["ref_type"] for x in re2])


# ---------- 5. 边界回归：停用词不误伤 + LIKE 转义 ----------
sep("5. 边界回归")

# 5a. 停用词不得误伤实词（"解释/介绍/区别"曾误作子串停用词，把"解释器/产品介绍"删坏）
assert fts._keywords("什么是解释器") == ["解释器"], fts._keywords("什么是解释器")
assert fts._keywords("产品介绍") == ["产品介绍"]
assert fts._keywords("区别对待") == ["区别对待"]
print("停用词不误伤实词 ✓")

# 5b. LIKE 通配符转义：% 按字面匹配，不误中不含 % 的块
db.add(MaterialChunk(material_id=m1.id, content="转化率是 100% 说明全部转化。", page_no=3, section_path=""))
db.add(MaterialChunk(material_id=m1.id, content="价格是 100 元整。", page_no=4, section_path=""))
db.commit()
h_pct = fts.search(db, "100%")
texts = [x["text"] for x in h_pct]
assert any("100%" in t for t in texts), "应命中含字面 100% 的块"
assert not any("100 元" in t for t in texts), "不应误中 100元"
print("LIKE 通配符转义 ✓")


sep("ALL PASS")
print("P0 多路召回验证通过")
