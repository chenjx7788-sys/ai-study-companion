# AI 伴学助手 · 开发指南（Development）

> 版本对齐：**v0.1.3（2026-09-11）**。本文面向开发者/自维护者；产品向说明见根目录 `README.md`，按版本变更见 `CHANGELOG.md`。
> 一句话：导入 → 理解 → 沉淀 → 调用 → 抗遗忘 → 数据洞察 的本地私有 AI 学习闭环。

## 功能总览（9 大模块）

| 模块 | 能力 |
|---|---|
| 材料库 | 上传（PDF/PPT/Word/Markdown/EPUB/图片/音视频，多文件+进度+拖拽）+ **文件夹管理（Folder 表：多级嵌套/CRUD/导入保留层级/夹内搜索含子夹）** + **本地文件直引（引用/复制，默认引用）** + **站内新增/编辑 md 文档（Vditor）** + 标签库（预置标签）+ 图片与扫描 PDF 本地 OCR + 平铺/文件夹两态视图 |
| 学习页 | 三栏布局（目录 / 阅读器 / AI 面板，可拖拽调宽）；文本视图 + 原文视图（PDF 用 pdf.js 可划线 / Word docx-preview / EPUB 服务端镜像 zip + iframe）+ 音视频转写文本；三色主动划线跨视图持久化；文本编辑模式（选区 AI 加工） |
| AI 理解 | 脉络摘要（层级大纲带页码、长文档两段式、SSE）、核心知识点（卡片带出处、可跳原文）、分章总结、划线 AI 解读 + 持续追问、直接对材料提问、测一测 |
| 笔记 | 划线/AI 产物/问答回答一键转笔记；Markdown 编辑；锚点跳原文高亮；**AI 加工四件套（改写/扩写/续写/总结，整条或选区）**；加入复习；删除联动清理索引与复习卡 |
| 知识库 | 原文+笔记自动向量化（本地 BGE 默认，可切 API）；按材料/按笔记双视图 + **类型筛选（问答/AI/周报/播客/手动）** + **笔记权重可配置（默认 ×1.5，运行时生效）**；多路召回检索（向量+全文 LIKE→RRF→规则重排）；一键重建索引 |
| AI 问答 | SSE 流式；范围选择（全库/资料/笔记/**文件夹含子夹**）；内联引用角标溯源跳转；**回答一键转笔记（弹窗内编辑/加工/复习）**；多模型切换 + 推理强度三档 + 思维链；会话管理 |
| AI 播客 | 来源（单篇材料/划线文档/笔记/错题卡）→ 知识简报 → 对话脚本（dialogue 知识对谈 / solo 单人精讲，3/5/10 分钟）→ edge-tts 逐句合成 + 可选 BGM 混音（PyAV）；音色试听、逐句时间戳跟读、导出文稿/SRT；**分句缓存续传**（只补失败句）、**脚本指纹判「上一版」**（不删旧音频）；生成与合成均走 SSE 阶段进度 |
| 复习巩固 | AI 出题（选择/简答/费曼复述/测一测）；简化 SM-2（1/2/4/7/15/30 天 + ease + 四档自评）；题型筛选、误触撤销、忘了当日重学、快捷键；遗忘曲线（参考线+样本量保护）；统计/连续打卡 |
| 数据统计 | 四维面板（学习/沉淀/调用/复习，原生 SVG 图表）；KPI + 14 天打卡轨迹；**AI 学习报告（四段式诊断，SSE 流式，同周去重）+ 每周自动周报** |
| 管理中心 | 多模型（增删改/测试连接/动态拉取/分角色）；向量与语音模型配置（Whisper small/base 档位可配）；检索与去噪规则；提示词自定义（含播客 / 周报，按分类 + 搜索）；标签库预设；存储备份/恢复；Token 统计（11 类业务）；帮助页含更新日志 |

> 说明：另有「使用帮助」页与站内「编辑器」视图（/editor）——共 10 个视图（Library/Study/Knowledge/Chat/Podcast/Review/Stats/Settings/Editor/Help）。

## 技术栈

- 前端：Vue 3 + Vite + Element Plus + Pinia + pdfjs-dist + markdown-it + Vditor（md 编辑）
- 后端：FastAPI + SQLite（SQLAlchemy）+ Chroma（向量）+ faster-whisper（本地 ASR）
- LLM：OpenAI 兼容协议（默认 DeepSeek，可多厂商并存），提示词可在设置页自定义
- 向量：本地 BGE-small-zh-v1.5（量化 ONNX，512 维），可切换 API embedding
- OCR：RapidOCR（rapidocr_onnxruntime，模型随 wheel 随包）+ PyMuPDF（扫描页渲染）
- 运行时：onnxruntime（BGE/OCR 共用）、ctranslate2（Whisper）
- 语音合成：edge-tts（云端免费 TTS，逐句 MP3 字节直拼，零额外 Key）+ PyAV（解码/重采样/混音，随 faster-whisper 依赖链自带，无需外挂 ffmpeg）
- EPUB：lxml 解析容器 / OPF / NCX，原文视图走「服务端镜像 zip 资源 + iframe」（不引入 epub.js）

## 目录结构（v0.1.3）

```
ai-study-companion/
├── start.bat                  # 开发态一键启动（后端 8000 + 前端 5173，依赖本机 .workbuddy 托管环境）
├── README.md / CHANGELOG.md   # 产品向 / 按版本变更
├── backend/
│   ├── app/
│   │   ├── main.py            # 路由注册 + 静态托管 dist（单端口）+ 启动迁移/备份/周报
│   │   ├── models.py          # 13 个实体（Folder/Material/MaterialChunk/AIAsset/Note/
│   │   │                      #   Highlight/KbEntry/ChatSession/ChatMessage/ReviewCard/ActivityLog/LLMUsage/Podcast）
│   │   ├── routers/           # materials/ai/notes/kb/chat/podcasts/review/settings/asr/stats/folders
│   │   ├── services/          # parser(解析,含 docx/ppt/epub) llm(提示词+记账+流式) vector(BGE+切分)
│   │   │                      #   search(多路召回) fts(全文LIKE) kb_index(索引) cleaner(去噪)
│   │   │                      #   ocr(RapidOCR) asr(语音模型) settings_store(配置) auto_backup(备份)
│   │   │                      #   epub(EPUB 解析) tts(edge-tts) podcast(两阶段生成) bgm(曲库) mixer(混音)
│   │   └── core/config.py     # 数据路径：开发态 backend/data，打包态 ~/.ai-study-companion
│   ├── data/                  # files/(材料) app.db chroma/ models/(BGE+whisper) stats_reports
│   ├── e2e_regression.py      # 全链路回归冒烟（改动后跑一遍）
│   ├── AIStudyCompanion.spec  # PyInstaller onedir 打包配置
│   └── launcher.py            # pywebview 原生窗口 + js_api（原生文件选择）
└── frontend/src/
    ├── views/                 # Library/Study/Knowledge/Chat/Podcast/Review/Stats/Settings/Editor/Help
    ├── components/            # PdfReader(pdf.js 可划线) DocxReader(Word 原文视图) EpubReader
    │                          #   NoteEditorDialog(共用笔记弹窗) PageHead(统一页头)
    ├── api/index.js           # 全部后端接口封装
    ├── utils/sse.js           # 公共 SSE 流式工具（第 6 参 onMeta）
    └── styles/global.css      # 设计令牌（浅色主题，主色 #7c5cfc）
```

## 启动方式（开发态）

### 一键启动（Windows，本机已装 .workbuddy 托管环境）
双击 `start.bat`（启动后端 8000 + 前端 5173 两个窗口）。若用普通 Python/Node 环境，见下方手动方式。

### 手动
```bash
# 后端（Python 3.11+；依赖安装见 requirements.txt）
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（Node 18+）
cd frontend
npm install && npm run dev        # http://localhost:5173，/api 已代理到 8000
```

首次使用：浏览器打开 http://localhost:5173 → 「管理中心」配置 LLM API Key（DeepSeek 等任意 OpenAI 兼容厂商）。

> 数据路径约定：开发态存 `backend/data/`；打包态（sys.frozen）自动落到 `~/.ai-study-companion`。**开发态数据与打包态数据互不影响。**

## 构建与打包（Windows 绿色版）

> ★ **推荐：一键脚本**（产物统一输出到 **D 盘**，不占用 C 盘空间）：
>
> ```bash
> bash backend/scripts/build_windows.sh
> ```
>
> 脚本依次完成：前端构建 → PyInstaller → 冒烟测试（`/api/health`）→ 打 zip。
> 默认产物位置（可用 `OUT_ROOT` 覆盖）：
>
> - `D:/AIStudyCompanion-build/dist/AIStudyCompanion/` — PyInstaller 产物（约 600MB）
> - `D:/AIStudyCompanion-build/release/AIStudyCompanion-v0.1.3-win.zip` — 发布包（约 247MB）
> - `D:/AIStudyCompanion-build/build/` — PyInstaller 中间缓存

<details>
<summary>手动步骤（备用）</summary>

```bash
# 1) 前端构建
cd frontend && npm run build        # 产物 frontend/dist

# 2) 后端单端口托管 dist（生产自测）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8899   # 浏览器访问 8899 即完整应用

# 3) PyInstaller onedir 打包（backend/ 下）—— 注意用 Windows 风格路径
python -m PyInstaller --noconfirm \
  --distpath D:/AIStudyCompanion-build/dist \
  --workpath D:/AIStudyCompanion-build/build \
  AIStudyCompanion.spec

# 4) 压缩分发：把 onedir 压成 zip（v0.1.3 ≈ 247MB）
# 5) 发布：Windows 包用 GitHub API 上传到 Release `v0.1.3`；macOS 由打 tag（v0.1.3-macos）触发 Actions 自动发布
#    官网下载链接指向 Release 固定 URL —— 替换资产即生效（但换版本号需同步改 HTML）
```

</details>

> ⚠️ **路径陷阱**：PyInstaller 是 Windows 程序，`--distpath` / `--workpath` **必须传 Windows 风格路径**（`D:/...`）。
> 若传 MSYS/Git-Bash 风格的 `/d/...`，会被它解释为 `C:\d\...`，产物反而落到 C 盘。
>
> ⚠️ **批量删除拦截**：本开发环境对「一次删除 >50 个文件」有安全拦截（报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`），
> `rm -rf` 与 Python `shutil.rmtree` 都可能被挡。**可靠做法是改名而非删除**（`mv dist dist.old-<时间戳>`），
> `build_windows.sh` 已内置该处理。
>
> 打包态冒烟：`ASC_BROWSER=1 ASC_PORT=<端口> .\AIStudyCompanion.exe`，检查 `/api/health` 与 `/api/version`。

## 测试与自查

- `backend/e2e_regression.py`：全链路回归冒烟（健康/字段/文件夹 CRUD/引用导入/全文搜索 folder_id/级联删除安全等），改动后必跑。
- 前端构建门禁：`npm run build`（有构建报错即回归）。
- 后端语法：`python -m py_compile <文件>`；改路由/版本后**必须重启 uvicorn**（无 --reload 时旧进程不生效）。
- 破坏性 UI 自测纪律：涉及真实数据评分/删除的测试，先记录状态、测后无条件还原（API undo / 删除测试数据），避免污染用户库。

## 已知边界与约定

- 扫描版 PDF 走本地 OCR（无文本层自动逐页兜底）；图片模糊/空白/手写仍可能失败；OCR 错字可走文本块校对（update_chunk 重建索引）。
- 全文检索用 SQLite LIKE（实测 FTS5 trigram 对 2 字中文失效）；中文无分词，"连写长句"靠向量兜底。
- ASR：Whisper CPU 推理（默认 small≈464MB，可切 base≈139MB）；**2 路并发 × 分核**（cpu_threads=8），模型单实例加载；首次转写按需下载（界面引导）。
- 引用模式材料：`data/files` 零副本；删除只解绑不物理删源文件（安全红线，勿破坏）。
- 单人使用无账号体系；备份 = 设置页一键备份/恢复，另有每日自动备份（KEEP=7，建议调 2–3）。
- 中文文案与 A 股配色约定不适用（非金融场景）；UI 主色 #7c5cfc，风格迭代遵循"去原型感、留白简约"。
