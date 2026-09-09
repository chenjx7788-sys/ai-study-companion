# 🦉 AI 伴学助手 · AI Study Companion

> 你的**本地私有 AI 学习知识中枢**：导入 → 理解 → 沉淀 → 调用 → 抗遗忘，一套打通。
> 资料、笔记、向量索引全部存在你自己电脑上——**数据不出设备，只需一个 LLM API Key**。

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![version](https://img.shields.io/badge/version-v0.1.2-purple)
![local](https://img.shields.io/badge/data-100%25%20local-brightgreen)
[![download](https://img.shields.io/badge/%E4%B8%8B%E8%BD%BD-v0.1.2%20zip-orange)](https://github.com/chenjx7788-sys/ai-study-companion/releases/latest)

> 👉 **下载 Windows 版**：点击上方橙色「下载」徽章，或到 [Releases 页面](https://github.com/chenjx7788-sys/ai-study-companion/releases/latest) 下载 `AIStudyCompanion-v0.1.2-win.zip`，解压后双击 `AIStudyCompanion.exe` 即可运行。

---

## 为什么需要它？

多数学习工具只帮你「读」，读完之后呢？划线就丢、笔记散落、合上书全忘。

AI 伴学助手把 **资料消化** 变成一条流水线：上传任何学习材料 → AI 自动理解 → 沉淀为可检索的个人知识库 → 随时问答调用 → 按记忆曲线安排复习。全程本地运行，学习数据是你自己的资产。

适用人群：**备考人群**（考研 / 法考 / CPA / 公考）、**在职学习者**（读行业报告与专业书）、**深度阅读者**（研究者 / 内容创作者，建立可检索可调用的个人知识库）。

## ✨ 核心特性

- 🔒 **数据私有**：资料、笔记、向量索引全部本地存储，不上云、不共享，数据不出设备
- 💰 **零边际成本**：向量化 / OCR / 语音转写全部本地 CPU 推理，AI 费用仅是你自己的 LLM Token 消耗
- 🧩 **多模型灵活切换**：任意 OpenAI 兼容厂商（DeepSeek / Kimi 等）并存配置，问答时一键切换 + 推理强度三档 + 思维链
- 📎 **答案可溯源**：AI 回答带内联引用角标，点击即可查看来源、跳转定位原文，拒绝幻觉
- 🗂 **文件夹管理**：多级文件夹组织资料，导入保留目录层级，问答可限定在指定文件夹（含子夹）
- 🧠 **科学抗遗忘**：AI 出题 + 简化 SM-2 间隔复习，误触可撤销、忘了当日重学

## 🧩 八大模块

| 模块 | 你能做什么 |
|---|---|
| **材料库** | PDF / PPT / Word / Markdown / 图片 / 音视频多格式；文件夹多级管理；图片与扫描 PDF 本地 OCR；本地文件直引（引用 / 复制可选）；内置 Markdown 文档编辑器 |
| **学习页** | 三栏沉浸阅读（目录 / 阅读器 / AI 面板可拖拽调宽）；文本、PDF 原文、音视频转写三种视图；黄 / 绿 / 蓝三色划线跨视图持久化 |
| **AI 理解** | 脉络摘要、核心知识点卡片、划线 AI 解读 + 持续追问；AI 加工四件套（改写 / 扩写 / 续写 / 总结）；「测一测」随学随测 |
| **知识库** | 原文 + 笔记自动向量化；多路召回检索（向量语义 + 全文精确双路）；笔记权重可调，一键重建索引 |
| **AI 问答** | SSE 流式回答；范围选择（全库 / 资料 / 笔记 / 文件夹）；内联引用可溯源；回答一键转笔记；编辑提问、一键复制 |
| **复习巩固** | AI 出题（选择 / 复述 / 测一测）；简化 SM-2 间隔调度；题型筛选、误触撤销、遗忘曲线、连续打卡 |
| **数据统计** | 学习 / 问答 / 复习四维可视化看板；AI 学习报告（一键生成 + 每周自动周报），薄弱点自动诊断 |
| **管理中心** | 多模型并存配置、单模型测试连接、动态拉取模型清单；向量本地 / 在线切换；提示词自定义；备份恢复；Token 统计 |

## 📥 下载与开始

> 当前提供 **Windows 10 / 11（64 位）绿色版**，macOS 版开发中。

1. 到 [Releases](https://github.com/chenjx7788-sys/ai-study-companion/releases) 下载最新版 `AIStudyCompanion-v0.1.2-win.zip`（约 246 MB）
2. 解压后双击 `AIStudyCompanion/AIStudyCompanion.exe`，首次启动会自动就位内置向量模型（约 10 秒）
3. 打开「管理中心 → 大模型配置」填入你的 **LLM API Key**（DeepSeek 等任意 OpenAI 兼容厂商，注册即得）即可使用全部 AI 能力

> - 系统要求：Windows 10 / 11（64 位），建议 8 GB 以上内存
> - 语音转写模型（可选）首次使用时按需下载，界面有引导与进度；OCR 模型已内置
> - 全程无需注册账号；数据默认保存在本机用户目录，卸载不影响源文件（引用模式不复制资料）

## 🔧 技术架构

```
前端：Vue 3 · Vite · Element Plus · Pinia · pdf.js
后端：FastAPI · SQLite · ChromaDB（向量）· faster-whisper（本地 ASR）· RapidOCR（本地 OCR）
向量：本地 BGE-small-zh-v1.5（量化 ONNX）/ 可切换在线向量 API
客户端：PyInstaller 绿色打包 + pywebview 原生窗口（单端口运行）
```

**RAG 链路**：入库（解析 → 去噪 → 结构化切分 → 向量化）→ 召回（向量语义 + 全文精确双路）→ RRF 融合 → 规则重排 → 流式回答并附来源。LLM 走 OpenAI 兼容协议，默认 DeepSeek。

## 📝 更新日志

完整按版本记录见 [CHANGELOG.md](./CHANGELOG.md)。v0.1.2 主要新增：数据统计面板与 AI 周报、材料库文件夹管理、AI 问答一键转笔记、文本 AI 加工四件套、内置 Markdown 文档编辑器等。

## 🚀 从源码运行（开发者）

详见 [DEVELOPMENT.md](./DEVELOPMENT.md)（启动方式、目录结构、已知边界）。简版：

```bash
# 后端（Python 3.11+）
cd backend && pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（Node 18+）
cd frontend && npm install && npm run dev   # http://localhost:5173
```

## 📄 许可证

License 待定（项目作者决定后补充，可考虑 MIT / AGPL-3.0）。

---

> 🦉 **伴伴**陪你学习 —— 让每一次输入都沉淀为长期记忆。
