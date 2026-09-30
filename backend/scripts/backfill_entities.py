"""知识图谱实体抽取 backfill：为存量「已解析成功但未抽取实体」的材料补齐实体/关系/引用。

背景：实体抽取（P1）是在 `materials._parse_material` 解析成功后后台触发的，
只有「重新解析」的材料才会抽取。升级前入库的历史材料没有实体 → 知识图谱为空。
本脚本一次性补齐存量材料的实体抽取，幂等：已有实体的材料自动跳过，可重复跑。

用法（在 backend/ 目录，用项目 venv 的 python）：
    # 开发态（默认 backend/data/app.db）：
    python scripts/backfill_entities.py --dry-run
    # 真实用户库（打包版数据，~/.ai-study-companion/app.db）：
    python scripts/backfill_entities.py --db-url "sqlite:///C:/Users/陈锦祥/.ai-study-companion/app.db"
    python scripts/backfill_entities.py --db-url "sqlite:///C:/Users/陈锦祥/.ai-study-companion/app.db" --material 1,2,7

安全边界：
- 只对新抽取的材料写 entities/relations/entity_refs 三表，不碰其它数据；
- 逐份 try/except，单份失败不中断整体；
- 复用 services/entity.py::persist_entities（正式入口，含归并/过滤/引用回填）。
"""
import argparse
import sys
import time
from pathlib import Path

# 允许 import app 包（脚本位于 backend/scripts/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---- 先解析 --db-url 再 import app ----
# config.py 的 db_url 是模块级常量（默认值在类定义时用 DATA_DIR 拼死），
# 环境变量 ASC_DATA_DIR 只能改 data_dir 字段，改不了 db_url。
# 必须设 ASC_DB_URL 才能覆盖 db_url；且必须在 import app 包之前设置。
parser = argparse.ArgumentParser()
parser.add_argument("--db-url", help="目标数据库 URL（默认 backend/data/app.db）")
parser.add_argument("--material", help="逗号分隔的材料 id，留空=全部待补")
parser.add_argument("--dry-run", action="store_true", help="只列出待补清单，不调用 LLM")
parser.add_argument("--gap", type=float, default=1.0, help="每份材料之间的间隔秒数（默认 1，缓和限流）")
parser.add_argument("--force", action="store_true",
                    help="重抽已有实体的材料（如升级抽取提示词后全量重抽，避免新旧数据混杂）")
_args = parser.parse_args()

if _args.db_url:
    import os
    os.environ["ASC_DB_URL"] = _args.db_url
    os.environ["ASC_DATA_DIR"] = str(Path(_args.db_url.replace("sqlite:///", "")).parent)

from app.database import Base, SessionLocal, engine
from app.models import Material, MaterialChunk, Entity
from app.services import entity as entity_svc

# 幂等建表 + 迁移：真实用户 db 里可能还没有 entities/relations/entity_refs 三表
# （它们是新加的模型，只有新版 exe 启动时 main.py 的 create_all 才会建），
# 且 materials 表可能缺 extract_sig 列（C 方案内容指纹）。
Base.metadata.create_all(bind=engine)
from sqlalchemy import text as _text, inspect as _inspect
try:
    if "extract_sig" not in {c["name"] for c in _inspect(engine).get_columns("materials")}:
        with engine.begin() as _conn:
            _conn.execute(_text("ALTER TABLE materials ADD COLUMN extract_sig VARCHAR(32) DEFAULT ''"))
except Exception:
    pass


def list_targets(db, only_ids=None, include_existing=False):
    """列出待补实体抽取的材料：parsed_status=success 且（尚无任何实体 或 --force 重抽）。"""
    q = db.query(Material).filter(Material.parsed_status == "success")
    if only_ids:
        q = q.filter(Material.id.in_(only_ids))
    rows = q.order_by(Material.id).all()
    targets = []
    for m in rows:
        has_entity = db.query(Entity).filter(Entity.material_id == m.id).count() > 0
        if has_entity and not include_existing:
            continue
        n_chunks = db.query(MaterialChunk).filter(MaterialChunk.material_id == m.id).count()
        targets.append((m, n_chunks))
    return targets


def main():
    # 复用模块级已解析的 _args（--db-url/--material/--dry-run/--gap 在 import 前已注册并解析）
    args = _args

    only = [int(x) for x in args.material.split(",") if x.strip()] if args.material else None

    db = SessionLocal()
    try:
        targets = list_targets(db, only, include_existing=args.force)
    finally:
        db.close()

    if not targets:
        print("没有待补实体抽取的材料（全部已抽取或有实体的已就绪）。")
        return
    print(f"待补实体抽取的材料共 {len(targets)} 份：")
    for m, n in targets:
        print(f"  [{m.id}] {m.title}（{m.format}，{n} 块）")

    if args.dry_run:
        print("\n--dry-run：仅列出清单，未调用 LLM。去掉 --dry-run 执行抽取。")
        return

    # 逐份抽取（每份独立 session，失败不中断）
    done = 0
    for i, (m, n_chunks) in enumerate(targets, 1):
        db = SessionLocal()
        try:
            chunks = (db.query(MaterialChunk)
                      .filter(MaterialChunk.material_id == m.id)
                      .order_by(MaterialChunk.page_no, MaterialChunk.id).all())
            # --force：清空内容指纹，绕过 persist_entities 的「内容未变则跳过」去重
            if args.force:
                m2 = db.get(Material, m.id)
                if m2 and m2.extract_sig:
                    m2.extract_sig = ""
                    db.commit()
            n = entity_svc.persist_entities(db, m.id, chunks)
            print(f"  [{i}/{len(targets)}] [{m.id}] {m.title} → {n} 个实体")
            if n:
                done += 1
        except BaseException as e:
            print(f"  [{i}/{len(targets)}] [{m.id}] {m.title} → 失败：{e}")
        finally:
            db.close()
        if i < len(targets) and args.gap:
            time.sleep(args.gap)

    print(f"\nbackfill 完成：{done}/{len(targets)} 份成功抽取实体。")


if __name__ == "__main__":
    main()
