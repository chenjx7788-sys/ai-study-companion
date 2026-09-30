<template>
  <div class="page help-page">
    <!-- 头部 -->
    <div class="help-hero">
      <div class="hero-title">
        <h2>使用帮助</h2>
        <p>你的私人学习知识中枢：把看到的、听到的收进来，读透、存好、随时调用、科学复习——全程本地、自带 Key、开箱即用。</p>
      </div>
      <el-button text type="primary" @click="emit('tour')">再看一遍引导</el-button>
    </div>

    <!-- ① 核心亮点 -->
    <section class="help-section">
      <h3 class="section-title">核心亮点</h3>
      <div class="point-grid">
        <div v-for="p in points" :key="p.title" class="point-card">
          <div class="point-icon" v-html="p.icon"></div>
          <div class="point-body">
            <div class="point-title">{{ p.title }}</div>
            <div class="point-desc">{{ p.desc }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- ② 快速上手三步 -->
    <section class="help-section">
      <h3 class="section-title">快速上手</h3>
      <div class="step-row">
        <div v-for="(s, i) in steps" :key="i" class="step-card">
          <span class="step-num">{{ i + 1 }}</span>
          <div class="step-body">
            <div class="step-name">{{ s.name }}</div>
            <div class="step-desc">{{ s.desc }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- ③ 模块速览 -->
    <section class="help-section">
      <h3 class="section-title">模块速览</h3>
      <div class="module-grid">
        <div v-for="m in modules" :key="m.path + m.name" class="module-card" @click="go(m.path)">
          <div class="module-head">
            <span class="module-name">{{ m.name }}</span>
            <span class="module-go">前往 →</span>
          </div>
          <div class="module-desc">{{ m.desc }}</div>
          <ul class="module-points">
            <li v-for="p in m.points" :key="p">{{ p }}</li>
          </ul>
        </div>
      </div>
    </section>

    <!-- ④ 常见问题 -->
    <section class="help-section">
      <h3 class="section-title">常见问题</h3>
      <el-collapse v-model="openFaq" class="faq-collapse">
        <el-collapse-item v-for="f in faqs" :key="f.q" :name="f.q">
          <template #title>
            <span class="faq-q">{{ f.q }}</span>
          </template>
          <div class="faq-a" v-html="renderMd(f.a)"></div>
        </el-collapse-item>
      </el-collapse>
    </section>

    <!-- ⑤ 更新日志 -->
    <section class="help-section">
      <h3 class="section-title">更新日志</h3>
      <div class="changelog">
        <div v-for="v in changelog" :key="v.version" class="clog-version">
          <div class="clog-head">
            <span class="clog-ver">{{ v.version }}</span>
            <span class="clog-date">{{ v.date }}</span>
          </div>
          <div v-for="g in v.groups" :key="g.label" class="clog-group">
            <span class="clog-tag" :class="'clog-' + g.type">{{ g.label }}</span>
            <ul class="clog-list">
              <li v-for="it in g.items" :key="it">{{ it }}</li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'

const emit = defineEmits(['tour'])
const router = useRouter()
const md = new MarkdownIt({ breaks: true })
const renderMd = (t) => md.render(t || '')
const openFaq = ref([])

const go = (path) => (path === '/study' || path === '/study/ai') ? router.push('/') : router.push(path)

// ---------- 核心亮点（产品卖点） ----------
const points = [
  {
    title: '数据私有 · 本地存储',
    desc: '资料、笔记、向量索引全部存在你的设备本地，不上云、不共享，知识资产只属于你。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/><path d="M9.5 12l2 2 3.5-3.5"/></svg>'
  },
  {
    title: '零成本 · 自带 Key',
    desc: '向量化、语音转写、OCR 全部本地免费推理，只需一个 LLM API Key（如 DeepSeek）即可解锁全部 AI 能力。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/></svg>'
  },
  {
    title: '多模型 · 灵活切换',
    desc: '可同时配置多个大模型，总结 / 问答分别指定，问答时底部一键切换，按需取用。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/></svg>'
  },
  {
    title: '全闭环 · 一套打通',
    desc: '从读材料到记得住一步到位：导入 → 理解 → 沉淀 → 调用 → 抗遗忘，无需在多个工具间切换。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.3"/><path d="M21 3v4h-4"/></svg>'
  },
  {
    title: '收得进来 · 网页剪藏',
    desc: '看到的文章、公众号、小红书笔记直接粘链接收进来：先预览、逐条确认才入库；还没想好的可以「仅阅读」，不占磁盘、不写知识库。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.1.5l3-3a5 5 0 0 0-7.1-7.1l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.1-.5l-3 3a5 5 0 0 0 7.1 7.1l1.7-1.7"/></svg>'
  },
  {
    title: '划线即懂 · 三色标记',
    desc: '选中文字即 AI 解读、改写扩写总结，黄 / 绿 / 蓝三色划线，重点、已懂、待深入一眼区分，可随时换色或取消。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>'
  },
  {
    title: '科学复习 · 抗遗忘',
    desc: 'AI 一键出题 + 间隔重复（SM-2），误触可撤销、忘了当天再学，该复习时自动提醒。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>'
  },
  {
    title: '数据洞察 · 学有所证',
    desc: '学习时长、问答命中、复习节奏自动记录，一键生成 AI 学习报告，每周自动出周报，薄弱点看得见。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5"/><path d="M4 19h16"/><rect x="7" y="11" width="3" height="6" rx="1"/><rect x="12" y="7" width="3" height="10" rx="1"/><rect x="17" y="13" width="3" height="4" rx="1"/></svg>'
  },
  {
    title: '听得见 · AI 播客',
    desc: '把材料、划线、笔记、错题卡生成知识简报与对话脚本，本地合成音频，通勤路上也能「听」自己的知识库。',
    icon: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 13a8 8 0 0 1 16 0"/><rect x="3" y="13" width="4" height="7" rx="2"/><rect x="17" y="13" width="4" height="7" rx="2"/></svg>'
  }
]

const steps = [
  { name: '导入材料', desc: '本地文件直接上传或引用，网页文章粘贴链接即可收进来，自动解析成可读文本。' },
  { name: '理解 & 沉淀', desc: 'AI 总结、三色划线、划词解读，把回答一键转成笔记，存进知识库。' },
  { name: '复盘 & 抗遗忘', desc: '复习巩固翻卡对抗遗忘，数据统计看学习报告，也能把材料生成播客随时听。' }
]

const modules = [
  { path: '/', name: '材料库', desc: '所有学习材料的收纳中心', points: ['文件夹分组管理，批量导入保留层级', '本地文件直引或复制副本，不重复占磁盘', '网页剪藏：粘贴链接 → 预览确认 → 逐条入库', 'EPUB 原文视图、图片与扫描件 OCR，标签 / 搜索 / 阅读进度'] },
  { path: '/study', name: '学习页', desc: '沉浸式阅读与加工', points: ['三栏布局自由调宽，文本 + 原文视图', '黄 / 绿 / 蓝三色划线，跨视图持久化', '选中文字即可 AI 解读、改写、转笔记', 'md 富文本编辑，修订同步更新检索'] },
  { path: '/study/ai', name: 'AI 理解', desc: '读懂每一份资料', points: ['脉络摘要流式生成，出处可点击定位', '核心知识点卡片，自动提取并回链原文', '划线 AI 解读 + 持续追问', '「测一测」一键出题，摘要 / 知识点均可转笔记'] },
  { path: '/knowledge', name: '知识库', desc: '你的第二大脑，AI 答案来源', points: ['原文与笔记自动向量化，修改即重索引', '多路召回：向量语义 + 全文精确检索', '笔记权重可调，按材料 / 笔记双视图分类可筛'] },
  { path: '/chat', name: 'AI 问答', desc: '基于知识库的流式问答', points: ['检索范围：全库 / 资料 / 笔记 / 文件夹', '内联引用角标，看来源、跳原文', '回答一键转笔记，多模型切换 + 思维链展示'] },
  { path: '/podcast', name: 'AI 播客', desc: '把知识讲给你听', points: ['来源：材料 / 划线 / 笔记 / 错题卡', '先出知识简报，再出对话脚本', '14 种音色试听，本地合成，可导出字幕'] },
  { path: '/review', name: '复习巩固', desc: '把笔记变题目，抗遗忘', points: ['AI 出题：选择题 / 复述卡 / 测一测', '翻卡四档自评，科学间隔调度', '误触撤销、忘了当日重学，遗忘曲线打卡'] },
  { path: '/stats', name: '数据统计', desc: '学习行为的可视化洞察', points: ['学习 / 沉淀 / 调用 / 复习四维看板', 'AI 学习报告一键生成 + 每周自动周报', '命中率异常、复习衰减自动诊断'] },
  { path: '/settings', name: '管理中心', desc: '配置与数据管理', points: ['多模型并存、测试连接、动态拉取清单', '向量 / 语音模型管理与智能体提示词自定义', '切分检索参数可调，备份 / 恢复、Token 统计'] }
]

const faqs = [
  { q: '首次使用如何配置？', a: '打开 **管理中心 → LLM 模型**，点击「添加模型」填入**名称、Base URL、API Key、模型名**（如 DeepSeek 注册即得），保存后设为「问答模型」，即可使用 AI 总结、问答、出题等能力。' },
  { q: '能同时用多个大模型吗？', a: '可以。在 **管理中心 → LLM 模型** 添加多个模型（支持多厂商混用），并分别指定「总结模型」「问答模型」；在 **AI 问答** 底部还可一键切换当前提问所用的模型。每个模型都能单独「测试连接」。' },
  { q: '扫描版 PDF 能解析吗？', a: '**支持 OCR**。无文本层的扫描版 PDF 会自动转图进行**本地 OCR 识别**，识别出的文字可进入知识库检索与 AI 问答。仅当识别失败（图片模糊 / 空白 / 手写内容）时才会标记为「扫描件」无法解析。' },
  { q: '删除材料后，笔记还在吗？', a: '**笔记会保留**。删除材料只移除原文和索引，你的笔记仍可在知识库中检索、预览；AI 问答转存的笔记同样保留，可继续编辑与复习。' },
  { q: '怎么限定问答只针对某部分资料？', a: '在 **AI 问答** 的「检索范围」中可指定：整个知识库、某份资料、某条笔记，或**某个文件夹（含子文件夹）**。正在阅读某份资料时，点右下角「问伴伴」会**自动关联当前资料**。' },
  { q: '如何把 AI 回答变成笔记？', a: '每条 AI 回答底部有**「转笔记」**按钮：标题自动取你的问题、内容为回答全文，可立即在弹出的笔记里改名、编辑、做 AI 加工或加入复习，保存后自动进入知识库。同一回答不会重复转存。' },
  { q: '材料是复制一份还是直接引用？', a: '导入本地文件时可二选一：**引用原文件**（不占额外磁盘，删除资料不会动源文件）或**复制副本**入库（源文件移动也不受影响）。引用后若源文件被移动，可在详情页「重新定位」。客户端使用原生文件对话框选择。' },
  { q: '网页上看到的文章怎么收进知识库？', a: '在 **材料库** 点「**粘贴网页链接**」：单篇粘一条，多篇用「**批量粘贴链接**」每行一条（一次最多 20 个，也可以直接粘整段分享文案，会自动提取其中的链接）。系统先**抓取预览**，你在候选列表里**逐条确认**后才入库；支持博客 / 新闻站 / **微信公众号文章** / **小红书图文**（正文配图会自动下载到本地，离线、断网也能看）。若正文抓不到，可直接**粘贴正文**继续，后续流程完全一样。' },
  { q: '「仅阅读」（临时阅读）和入库有什么区别？', a: '「**仅阅读**」是「先看看再说」：正文只进内存，**不写磁盘、不进知识库**，应用重启即清空，同样能看 AI 摘要、划词解读与追问。想长期留着，点「**加入知识库**」即可转成正式材料（之后能转笔记、能检索）；「最近阅读」抽屉里可以随时找回本次使用期间读过的链接。' },
  { q: '划线高亮和 AI 加工怎么用？', a: '在文本视图或 PDF 原文视图中**选中文字**：工具条上**黄/绿/蓝**三个色点即划线（再次选中可换色或取消）；**改写/扩写/总结/续写**会对选中文字做 AI 加工，结果可对比后采用。笔记编辑弹窗、站内文档编辑页同样支持对选中文字的加工。' },
  { q: '复习的间隔规则是什么？', a: '采用**简化 SM-2 调度**：按 1 / 2 / 4 / 7 / 15 / 30 天的间隔安排复习，根据你的自评（忘了/模糊/记得/简单）动态调整。误评可**撤销**；「忘了」的卡片**当天会再次出现**巩固。' },
  { q: '数据统计里的 AI 报告是怎么生成的？', a: '应用会自动记录学习时长、问答、复习等行为。在 **数据统计** 点击「生成 AI 分析报告」可随时生成；每周打开应用时若已跨周还会**自动生成周报**。报告会诊断断档、只进不出、复习衰减等问题并给出建议。' },
  { q: '音频 / 视频转写为什么比较慢？', a: '转写为**本地 CPU 推理**，采用多路并发分核，速度约为音视频时长的 1/3；同音专有名词可能被误识别，建议转写后快速校对。可先在管理中心预下载语音模型，减少首次等待。' },
  { q: 'AI 播客怎么用？会额外花钱吗？', a: '在 **AI 播客** 里选来源（单篇材料 / 划线文档 / 笔记 / 错题卡）、风格（知识对谈 / 单人精讲）与时长（3 / 5 / 10 分钟），先生成**知识简报**，确认后生成**对话脚本**，脚本可逐句修改，最后在本地**合成音频**。语音合成走 edge-tts，免费且不需要额外 Key；只有生成简报与脚本会消耗你在管理中心配置的 LLM。' },
  { q: '改了脚本或换了音色，之前的音频怎么办？', a: '旧音频**会保留**并标注为「**上一版**」，不会被悄悄删掉。想让它与当前脚本一致，点「**重新合成**」即可（已合成过的句子命中缓存，只补差异部分）；也可以继续听旧版。' },
  { q: '「转笔记」转存的笔记在哪里看？', a: '所有「转笔记」都进入 **知识库 → 按笔记**，并带类型标签（问答 / AI / 周报 / 播客 / 手动）便于筛选。周报、播客脚本这类不属于某份材料的笔记同样可以编辑、AI 改写与加入复习；同一产物重复转存会自动识别，内容更新时可一键覆盖同步。' }
]

// ---------- 更新日志 ----------
const changelog = [
  {
    version: 'v0.1.5',
    date: '2026-09-28',
    groups: [
      {
        type: 'add', label: '新增',
        items: [
          '简单学：材料学习页新增「简单学」入口，选一份材料一键生成结构化学习讲义（必记要点 + 探索历程 + 逐章小结），讲义可一键转笔记、并自动出题排进复习队列',
          '浏览器扩展（知萤）：任意网页划词即可「解释 / 总结 / 存知识库」，支持右键菜单与 Alt+B 快捷键，结果逐字呈现、可拖动、可复制',
          '知识图谱检索：自动为知识库抽取实体与关系，问答多一路「实体关联」召回，跨材料关联提问（如「A 和 B 是什么关系」）更准',
          'AI 播客「原文朗读」：整理好的内容不改写、直接原样做成音频（上限 6 万字，超限明确提示）',
          'AI 播客「粘贴文本」：直接粘贴文本即可生成播客'
        ]
      },
      {
        type: 'opt', label: '优化',
        items: [
          '笔记编辑器全屏化：更大书写空间，本地加载、断网可写，支持 Ctrl+S 与自动草稿',
          '笔记编辑器视觉简约化：空状态、排版与按钮布局统一精美化',
          'AI 播客合成不中断：生成中途切走页面，音频照常合成完成',
          'AI 播客生成台：生成按钮与快速模式固定吸附底部；新增 20 分钟档'
        ]
      }
    ]
  },
  {
    version: 'v0.1.4',
    date: '2026-09-22',
    groups: [
      {
        type: 'add', label: '新增',
        items: [
          '网页剪藏：材料库「粘贴网页链接」/「批量粘贴链接」（一次最多 20 个），先抓取预览、逐条确认才入库；支持博客 / 新闻站 / 公众号文章 / 小红书图文（配图自动下载到本地）',
          '临时阅读：还没决定要不要收的内容先「仅阅读」，不写磁盘、不进知识库，可摘要与划词追问；「最近阅读」可随时找回，想留下再一键加入知识库',
          'EPUB 原文视图补齐四个动作：划线、AI 解读、转笔记、复制，与 PDF、Markdown 口径一致',
          '原文视图随分栏宽度自适应重排（PDF / Word / EPUB），并保持阅读位置'
        ]
      },
      {
        type: 'fix', label: '修复',
        items: [
          '编辑器改为本地加载，断网也能正常打开与书写',
          '图片「原文视图」显示偏大；PDF / Word / EPUB 原文视图在窄栏下被横向裁切',
          '阅读进度：切换「文本 / 原文」视图后滚动位置错位'
        ]
      },
      {
        type: 'opt', label: '优化',
        items: [
          '新增运行环境启动检测：缺少系统组件时给出明确提示与安装引导，不再空白无提示'
        ]
      }
    ]
  },
  {
    version: 'v0.1.3',
    date: '2026-09-11',
    groups: [
      {
        type: 'add', label: '新增',
        items: [
          'AI 播客：材料 / 划线 / 笔记 / 错题卡生成知识简报与对话脚本（含单人精讲），本地合成音频，14 种音色可试听、可选背景音乐',
          'EPUB 电子书支持：文本视图 + 原文视图，原书排版、插图与样式保真呈现',
          '「转笔记」覆盖全部 8 个 AI 产物入口：新增材料全文摘要、AI 学习周报、播客对话脚本',
          'Word 文档「原文视图」：高保真还原 .docx 版面，表格、图片、字体样式、分页、页眉页脚均可查看',
          '材料库「全部材料 / 按文件夹」双视图切换：平铺模式直接显示每份材料归属的文件夹',
          '模型配置内置 13 家厂商模板：选中自动填充 Base URL 与模型名，附「去领免费额度」入口',
          '提示词管理补齐播客与周报，支持分类浏览与搜索'
        ]
      },
      {
        type: 'fix', label: '修复',
        items: [
          '音频与脚本不一致时不再静默：旧音频保留并标注「上一版」，一键重新合成即可对齐',
          '生成台与所选作品真正对齐：来源、时长、音色、附加要求全部回填并可查看',
          '长音频合成支持断点续传：单句失败只补失败句；修复删除作品报 500、选中文档后点生成无反应',
          '「转笔记」后的「查看」改为当前页弹窗，不再跳转知识库',
          '长文档摘要不再「像卡死」：先看分组进度，实测 115 秒 → 约 43 秒，并修掉尾部被静默截断',
          'Word 表格内容此前被整段丢弃；升级后沿用旧缓存导致「点击没反应」',
          '阅读进度：切换「文本 / 原文」视图保持所在页；无页锚点的视图不再误写进度',
          '新手引导不再拦截点击；模型配置修复连接测试通过但问答无返回、保存无提示等问题'
        ]
      },
      {
        type: 'opt', label: '优化',
        items: [
          'AI 播客页三栏布局，脚本改气泡对话 + 当前句高亮，生成与合成均带阶段进度',
          '播放器支持 ±15 秒、上下句、倍速；作品库支持搜索、状态筛选与重命名',
          '复习 / 知识库 / AI 播客 / 数据统计 / 管理中心统一页头样式',
          '提示词管理页重构：分类导航 + 搜索 +「默认 / 已自定义」标识',
          'Token 统计纳入播客消耗；统计页笔记列表请求体积减少约 60%'
        ]
      }
    ]
  },
  {
    version: 'v0.1.2',
    date: '2026-09-09',
    groups: [
      {
        type: 'add', label: '新增',
        items: [
          '数据统计面板：学习/问答/复习行为四维看板 + 连续打卡',
          'AI 学习报告：一键生成 + 每周自动周报，诊断薄弱环节',
          '材料库文件夹管理：多级嵌套、导入保留目录层级、夹内搜索',
          '本地文件直引：引用或复制可选，源文件重定位',
          'AI 问答：回答一键转笔记、按文件夹（含子夹）限定检索',
          '内置 Markdown 文档编辑器，md 材料富文本编辑',
          '文本 AI 加工四件套：改写/扩写/续写/总结（笔记与材料通用）',
          '标签库模式：预置标签，先建后用'
        ]
      },
      {
        type: 'fix', label: '修复',
        items: [
          '修复 md 文档二次编辑内容丢失（缓存与草稿加固）',
          '修复重建索引在孤儿笔记场景崩溃',
          '修复问答命中率统计口径错误（恒为 0%）',
          '修复客户端新手引导每次启动弹出、导出笔记无反应',
          '修复音视频批量转写互相拖慢（多路并发分核）'
        ]
      },
      {
        type: 'opt', label: '优化',
        items: [
          '复习巩固：题型筛选、误触撤销、忘了当日重学、遗忘曲线可读性',
          'AI 报告与摘要流式生成，同周报告自动去重',
          '复习页 / 材料库 / AI 问答 / 数据统计页面视觉升级',
          '客户端启动提速：目录版免解压（约 19s → 7s）',
          'AI 回答引用内容可滚动查看，气泡智能避让'
        ]
      }
    ]
  },
  {
    version: 'v0.1.1',
    date: '2026-09-07',
    groups: [
      {
        type: 'add', label: '新增',
        items: [
          '图片与扫描版 PDF 本地 OCR 识别',
          '语音转写模型下载引导、默认版本可配置',
          '多路召回检索：向量语义 + 全文精确匹配，命中率提升',
          '入库去噪（页眉/水印/乱码）与结构化切分',
          '问答推理强度三档 + 思维链展示',
          '多模型管理：单模型测试连接、动态拉取模型清单',
          '问答支持编辑提问、一键复制回答',
          '问「知识库有什么内容」可直接查看资料与笔记清单'
        ]
      },
      {
        type: 'fix', label: '修复',
        items: [
          '修复 md 文件目录章节点击不跳转',
          '修复 PDF 原文视图划线 / 解读 / 转笔记失效',
          '修复部分模型 temperature 参数报错（自动降级重试）',
          '修复图片 OCR 文本换行丢失'
        ]
      },
      {
        type: 'opt', label: '优化',
        items: [
          '材料详情页进入默认折叠菜单、退出恢复',
          '侧边栏支持折叠，小屏释放空间',
          '问答输入区布局与视觉优化',
          'AI 回答排版美化（表格 / 引用 / 标题层级）',
          '管理中心向量模型独立版块',
          '启动自动备份 + 向量索引自修复'
        ]
      }
    ]
  },
  {
    version: 'v0.1.0',
    date: '2026-09-04',
    groups: [
      {
        type: 'add', label: '新增',
        items: [
          '材料库：多格式上传（PDF/PPT/Word/EPUB/音视频）与解析进度',
          '学习页：三栏沉浸阅读 + 文本/PDF 原文双视图 + 音视频转写',
          'AI 理解：脉络摘要、核心知识点、划线 AI 解读、持续追问',
          '笔记：划线/内容一键转笔记、Markdown 编辑、加入复习',
          '知识库：原文与笔记自动向量化、双视图、一键重建索引',
          'AI 问答：流式回答、范围选择、来源标注、会话管理',
          '复习巩固：AI 出题、翻卡自评、SM-2 间隔复习',
          '管理中心：LLM 配置、提示词自定义、存储备份、Token 统计'
        ]
      },
      {
        type: 'client', label: '客户端',
        items: [
          '桌面客户端形态：原生窗口、免安装绿色版',
          '首次使用分步引导',
          '使用帮助页（快速上手 + 模块速览 + 常见问题）'
        ]
      }
    ]
  }
]
</script>

<style scoped>
.help-page { max-width: 980px; }
.help-hero {
  display: flex; align-items: flex-end; justify-content: space-between;
  padding-bottom: 18px; margin-bottom: 8px; border-bottom: 1px solid var(--asc-divider);
}
.hero-title h2 { margin: 0 0 6px; font-size: 22px; font-weight: 600; }
.hero-title p { margin: 0; font-size: 13px; color: var(--asc-text-3); }

.help-section { margin-top: 28px; }
.section-title {
  font-size: 14px; font-weight: 600; margin: 0 0 14px;
  padding-left: 10px; border-left: 3px solid var(--asc-primary);
}

/* 核心亮点 */
.point-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 14px; }
.point-card {
  display: flex; gap: 12px; align-items: flex-start;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 16px;
  transition: all .2s ease;
}
.point-card:hover { transform: translateY(-2px); border-color: transparent; box-shadow: var(--asc-shadow-hover); }
.point-icon {
  flex-shrink: 0; width: 40px; height: 40px; border-radius: 10px;
  background: var(--asc-primary-soft); color: var(--asc-primary);
  display: flex; align-items: center; justify-content: center;
}
.point-title { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.point-desc { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.6; }

.step-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.step-card {
  display: flex; gap: 12px; align-items: flex-start;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 16px;
}
.step-num {
  flex-shrink: 0; width: 24px; height: 24px; border-radius: 50%;
  background: var(--asc-primary-soft); color: var(--asc-primary);
  font-size: 13px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.step-name { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
.step-desc { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.6; }

.module-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.module-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 16px; cursor: pointer;
  transition: all .2s ease;
}
.module-card:hover { transform: translateY(-2px); border-color: transparent; box-shadow: var(--asc-shadow-hover); }
.module-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.module-name { font-size: 14px; font-weight: 600; }
.module-go { font-size: 12px; color: var(--asc-primary); opacity: 0; transition: opacity .15s; }
.module-card:hover .module-go { opacity: 1; }
.module-desc { font-size: 12.5px; color: var(--asc-text-2); margin-bottom: 10px; }
.module-points { margin: 0; padding-left: 16px; }
.module-points li { font-size: 12px; color: var(--asc-text-3); line-height: 1.8; }
.module-points li::marker { color: var(--asc-primary); }

.faq-collapse { background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 12px; padding: 4px 16px; }
.faq-q { font-size: 13px; font-weight: 500; }
.faq-a { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.8; }
.faq-a :deep(strong) { color: var(--asc-text); font-weight: 600; }

/* 更新日志 */
.changelog { display: flex; flex-direction: column; gap: 16px; }
.clog-version {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 16px 18px;
}
.clog-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.clog-ver {
  font-size: 13px; font-weight: 600; color: var(--asc-primary);
  background: var(--asc-primary-soft); padding: 2px 10px; border-radius: 6px;
}
.clog-date { font-size: 12px; color: var(--asc-text-3); }
.clog-group { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.clog-group:last-child { margin-bottom: 0; }
.clog-tag {
  flex-shrink: 0; width: 40px; text-align: center; margin-top: 2px;
  font-size: 11px; font-weight: 600; padding: 2px 0; border-radius: 5px;
}
.clog-add { color: #16a34a; background: rgba(22, 163, 74, .1); }
.clog-fix { color: #e8a33d; background: rgba(232, 163, 61, .12); }
.clog-opt { color: var(--asc-primary); background: var(--asc-primary-soft); }
.clog-client { color: #2f6fed; background: rgba(47, 111, 237, .1); }
.clog-list { margin: 0; padding: 0; list-style: none; flex: 1; }
.clog-list li {
  font-size: 12.5px; color: var(--asc-text-2); line-height: 1.9;
  position: relative; padding-left: 14px;
}
.clog-list li::before {
  content: ''; position: absolute; left: 0; top: 10px;
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--asc-text-3);
}
</style>
