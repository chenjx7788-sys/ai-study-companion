# AI 伴学助手

自用的 AI 学习工具：**导入 → 理解 → 沉淀 → 调用 → 抗遗忘** 完整闭环。

## 功能总览

| 模块 | 能力 |
|---|---|
| 材料库 | PDF/PPT/Word/音频(mp3,wav,m4a)/视频(mp4) 上传（多文件+进度条+拖拽）、解析四态（解析中/成功/失败/扫描件）、标签分类筛选、标题/全文搜索、阅读进度条 |
| 学习页 | 三栏布局（目录+笔记 / 阅读器 / AI 面板）；文本视图 + PDF 原文视图（pdf.js 自渲染可划线）+ 音视频转写文本（顶部常驻播放器）；页码/分钟导航 |
| AI 理解 | 脉络摘要（层级大纲带页码、长文档两段式）、核心知识点（JSON 卡片带出处）、分章总结、划线 AI 解读 + 持续追问、知识点直接追问 |
| 笔记 | 划线/AI 产物一键转笔记、Markdown 编辑/预览、锚点跳原文、导出 Markdown、加入复习、删除二次确认；删除资料时笔记保留（孤儿笔记仍可在知识库检索/预览） |
| 划线足迹 | 已解读（紫）/已转笔记（琥珀）段落常驻高亮，来源跳转时目标段落闪烁定位 |
| 知识库 | 原文+笔记自动向量化（本地 BGE-small-zh 中文模型，可切换 API 模型），按材料/按笔记双视图，重建/全量重建索引 |
| AI 问答 | SSE 流式回答；范围选择（整个知识库/资料/笔记 + 指定条目）；指定单份资料时优先引用其摘要产物；来源卡片（原文/笔记/摘要）点击跳学习页并高亮；会话重命名/删除确认/默认新会话 |
| 复习 | 笔记一键 AI 出题 → 翻卡自评（忘了/模糊/记得）→ 简化 SM-2 间隔调度（1/2/4/7/15/30 天）→ 今日队列 + 统计 |
| 设置 | LLM 配置（Base URL/Key/双模型）、向量模型（本地/API 切换+一键重建）、6 条智能体提示词自定义、Token 消耗统计、存储占用、一键备份/从备份恢复 |

## 技术栈

- 前端：Vue 3 + Vite + Element Plus + Pinia + pdfjs-dist + markdown-it
- 后端：FastAPI + SQLite（SQLAlchemy）+ Chroma（向量）+ faster-whisper（本地 ASR）
- LLM：OpenAI 兼容协议（默认 DeepSeek），提示词可在设置页自定义
- 向量：本地 BGE-small-zh-v1.5（量化 ONNX，512 维），可切换硅基流动/百炼/智谱 API

## 启动方式

### 一键启动（Windows）
双击 `start.bat`（启动后端 8000 + 前端 5173 两个窗口）。

### 手动
```bash
# 后端
cd backend
<venv>/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端
cd frontend
npm run dev        # 5173，已配置 /api 代理到 8000
```

首次使用：打开 http://localhost:5173/settings 配置 LLM API Key（DeepSeek 注册即得）。

## 目录结构

```
ai-study-companion/
├── start.bat                  # 一键启动
├── backend/
│   ├── app/
│   │   ├── main.py            # 路由注册
│   │   ├── models.py          # 9 个实体（含 ReviewCard、LLMUsage）
│   │   ├── routers/           # materials/ai/notes/kb/chat/review/settings
│   │   └── services/          # parser(解析+ASR) / llm(提示词+记账) / vector(BGE) / kb_index / settings_store
│   ├── data/                  # files/ app.db chroma/ models/(BGE+whisper)
│   └── e2e_regression.py      # 全链路回归冒烟（改动后跑一遍）
└── frontend/src/
    ├── views/                 # Library/Study/Knowledge/Chat/Review/Settings
    ├── components/PdfReader.vue  # pdf.js 自渲染（可划线）
    └── styles/global.css      # 设计令牌（Obsidian 风浅色主题）
```

## 已知边界

- 扫描版 PDF 走本地 OCR 识别（无文本层自动转图识别；图片模糊/空白/手写才标记「扫描件」）；旧版 .doc/.ppt 需另存为新格式
- ASR 为 CPU 推理，速度约音频时长的 1/3；同音专有名词可能误识别
- 划线高亮在文本视图与 PDF 原文视图均支持
- 单人使用，无账号体系；备份=设置页一键导出 data 目录 zip
