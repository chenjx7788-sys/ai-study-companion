# tests/ —— 验收探针（E2E 级）

这里收的是**现役验收探针**（2026-10-26 起从工作区根迁入，N3）。它们不是 pytest 单测，
而是独立可执行脚本：自带种子、自带隔离实例或打真实端口，跑完打印 `PASS/FAIL` 总计并以
退出码给结论。**入仓门槛：最近一次运行全绿**；一次性实验/诊断脚本不进这里。

## 运行方式

统一用 WorkBuddy 内置 Python（带 playwright / fastapi / uvicorn）：

```bash
PY="C:/Users/陈锦祥/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
"$PY" tests/probes/_probe_fresh_boost_20261026.py
```

探针内部路径一律按 `__file__` 相对定位（tests/probes/x.py → parents[2]=repo 根、
parents[3]=工作区根），**与运行目录无关**，从任何 cwd 跑都行。

## 探针清单与外部依赖

| 探针 | 验收面 | 依赖 | 最近全绿 |
|---|---|---|---|
| `_probe_fresh_boost_20261026.py` | 复习队列：新建卡当日排前且不被每日上限截断（N1，北京日界、交错、筛选联动） | 无（隔离实例 + 内置 chromium） | 2026-10-26 16/16 |
| `_probe_read_ephemeral_20261026.py` | /read 临时阅读页：渲染管线、外链、DOM 级消毒、空态、recent 增删（N2） | 无（隔离实例自闭环） | 2026-10-26 16/16 |
| `_probe_chat_modes_20261025.py` | 三档问答（auto/kb/general）装配契约 | 无（隔离实例） | 2026-10-26 迁移后复跑 |
| `_probe_modes_live_20261025.py` | 三档问答真模型（kb_miss 文案/general 零检索/auto 命中） | 真实 8000 + 已配 LLM | 2026-10-25 12/12 |
| `_probe_tour_skills.py` | 新手引导（技能步文案、三态） | 4176 preview | 2026-10-26 迁移后复跑 |
| `_probe_md_enhance_20261023.py` | 渲染管线门禁（高亮/mermaid/复制/消毒） | 5173 dev（默认） | 2026-10-25 10/10 |
| `_probe_md_pipeline_p2_20261025.py` | 渲染管线二期（六视图统一 createMd+renderMd） | 5173 dev + 真实 8000 | 2026-10-25 22/0/1SKIP |
| `_probe_code_run_20261025.py` | 代码运行沙箱（A4：sandbox/控制台回传/XSS 隔离） | 5173 dev | 2026-10-25 7/7 |
| `_probe_closed_loop_20261025.py` | 学习闭环后端（B7 视频→卡 / B8 薄弱点→卡） | 无（隔离实例 + 1 次真 LLM） | 2026-10-26 迁移后复跑 |
| `_probe_closed_loop_ui_20261025.py` | 学习闭环 UI（按钮三态、复习页总装） | 真实 8000 + 真库（自带种子+清场） | 2026-10-25 11/11 |
| `_probe_boundary_quiz_20261025.py` | 边界测试题（A5：混题/降级/计数修正） | 无（隔离实例 + 真 LLM） | 2026-10-26 迁移后复跑 |
| `_probe_drill_smoke_20261025.py` | 陪练合并冒烟（B4：预览/CRUD/finish/趋势/tts/asr） | 无（隔离实例） | 2026-10-26 迁移后复跑 |
| `_probe_p0_skills.py` | 技能注册表（7 内置、启停、增删、未污染实例） | 真实 8000 | 2026-10-26 迁移后复跑 |
| `_probe_p1_backend.py` | 技能建议卡装配（后端） | 无（离线） | 2026-10-26 迁移后复跑 |
| `_probe_p4_backend.py` | 带工具技能全链路（mock LLM 脚本化） | 无（离线） | 2026-10-26 迁移后复跑 |

另：`backend/e2e_regression.py` 是更早的回归脚本（留在 backend/ 原位）。

## 写新探针的纪律（从翻车史里长出来的）

- **隔离实例四要素**：`ASC_DATA_DIR / ASC_FILES_DIR / ASC_DB_URL / ASC_CHROMA_DIR` 一起重定向
  到独立临时目录；BGE 模型用硬链接指真身（`backend/data/models/bge-small-zh-v1.5`），
  缺了会联网下载卡死 30s+。
- **哨兵卡**先把 `max(id)` 顶高再验删除类行为；哨兵必须「非到期 + 非当日新建」，
  否则会混进今日队列 fresh 计数（2026-10-26 实测翻车）。
- **分辨力守卫**：每个关键断言配一条「旧逻辑下必红」的反向断言；`want==0` 的断言
  必须配同文件 `want>=1` 守卫。
- **页面等待用 `wait_for_selector`，不要拍脑袋 `wait_for_timeout`**（时序抖动实测翻车）。
- 浏览器一律传 `executable_path` 指向项目内 chromium-1200（本机 playwright 1.62
  裸 launch 必崩）。
- 探针只杀自己 Popen 的进程；临时目录留在工作区根由用户手动清（删除配额纪律）。
