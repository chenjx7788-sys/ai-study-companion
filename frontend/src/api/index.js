import http from './http'

// 材料管理（PRD 模块 A）
export const materialApi = {
  list: (params) => http.get('/materials', { params }),
  detail: (id) => http.get(`/materials/${id}`),
  chunks: (id) => http.get(`/materials/${id}/chunks`),
  updateChunk: (materialId, chunkId, content) => http.put(`/materials/${materialId}/chunks/${chunkId}`, { content }),
  deleteChunk: (materialId, chunkId) => http.delete(`/materials/${materialId}/chunks/${chunkId}`),
  deletePage: (materialId, pageNo) => http.delete(`/materials/${materialId}/pages`, { params: { page_no: pageNo } }),
  highlights: (materialId) => http.get(`/materials/${materialId}/highlights`),
  createHighlight: (materialId, data) => http.post(`/materials/${materialId}/highlights`, data),
  updateHighlight: (materialId, highlightId, data) => http.put(`/materials/${materialId}/highlights/${highlightId}`, data),
  deleteHighlight: (materialId, highlightId) => http.delete(`/materials/${materialId}/highlights/${highlightId}`),
  fileUrl: (id) => `/api/materials/${id}/file`,
  // EPUB 原文视图：章节清单（index 与 chunk.page_no 对齐）+ zip 资源镜像前缀
  epubChapters: (id) => http.get(`/materials/${id}/epub`),
  epubResBase: (id) => `/api/materials/${id}/epub-res/`,
  upload: (formData, onProgress) => http.post('/materials', formData, {
    onUploadProgress: (ev) => onProgress && ev.total && onProgress(Math.round(ev.loaded / ev.total * 100)),
  }),
  update: (id, payload) => http.patch(`/materials/${id}`, payload),   // 标签/阅读进度
  exportNotesUrl: (id) => `/api/materials/${id}/notes/export`,
  remove: (id) => http.delete(`/materials/${id}`),
  retryParse: (id) => http.post(`/materials/${id}/reparse`),
  fulltextSearch: (q, folderId) => http.get('/materials/search/fulltext', { params: { q, folder_id: folderId ?? undefined } }),
  createDocument: (data) => http.post('/materials/document', data),
  updateDocument: (id, data) => http.put(`/materials/${id}/document`, data),
  importLocal: (data) => http.post('/materials/import-local', data),   // 本地文件/文件夹直引
  relocate: (id, data) => http.post(`/materials/${id}/relocate`, data),   // 源文件重新定位
}

// 网页剪藏（P0-6）：抓取 → 预览 → **逐条确认**入库。
// ⚠️ 合规边界：不提供「全部入库」按钮（方案 §6.2），批量接口只产出候选列表。
export const clipApi = {
  // 单篇预览：抓取并抽正文，不落库。传 text 即走「粘贴降级」（SPA 抓不到正文时）
  preview: (payload) => http.post('/materials/clip/preview', payload, { timeout: 120000 }),
  // 逐条入库：复用「新增文档」同一条解析/索引链路；同 URL 幂等（回传 duplicated=true）
  save: (payload) => http.post('/materials/clip/save', payload, { timeout: 300000 }),
  // 批量预览：只产出候选（ready / paste / blocked），入库必须逐条调 save
  batchPreview: (payload) => http.post('/materials/clip/batch/preview', payload, { timeout: 300000 }),
}


// AI 理解（PRD 模块 B）；生成类接口长文档可能需数分钟，超时放宽到 300s
export const aiApi = {
  summary: (materialId, instruction) => http.post(`/ai/summary`, { material_id: materialId, instruction }, { timeout: 300000 }),
  keywords: (materialId, instruction) => http.post(`/ai/keywords`, { material_id: materialId, instruction }, { timeout: 300000 }),
  sectionSummary: (materialId, sectionPath) => http.post(`/ai/section-summary`, { material_id: materialId, section_path: sectionPath }, { timeout: 300000 }),
  explain: (payload) => http.post(`/ai/explain`, payload, { timeout: 180000 }),      // 划线解读/追问
  ask: (materialId, question) => http.post(`/ai/ask`, { material_id: materialId, question }, { timeout: 180000 }),   // 直接对材料提问
  assets: (materialId) => http.get('/ai/assets', { params: { material_id: materialId } }),
  chains: (materialId) => http.get('/ai/chains', { params: { material_id: materialId } })
}

// 临时阅读（P0-5/P0-6）：正文只进内存快照，**不落库、不建索引**
// 「最近阅读」是"本次使用期间"的语义 —— 关闭应用即清空（UI 要照实写，不能叫"历史记录"）
export const ephemeralApi = {
  // 打开链接：抓取（或粘贴降级）→ 写入内存快照 → 回传正文与 chunks 供渲染
  open: (payload) => http.post('/ai/ephemeral/open', payload, { timeout: 120000 }),
  // SSE 流式接口要完整路径（streamSSE 用 fetch，不走 axios 的 /api 前缀协商）
  summaryStreamUrl: '/api/ai/ephemeral/summary/stream',
  askStreamUrl: '/api/ai/ephemeral/ask/stream',
  // 划线解读 / 追问（同步）：history 由前端携带 → 服务端无状态
  explain: (payload) => http.post('/ai/ephemeral/explain', payload, { timeout: 180000 }),
  recent: () => http.get('/ai/ephemeral/recent'),
  recentOne: (key) => http.get(`/ai/ephemeral/recent/${encodeURIComponent(key)}`),
  removeRecent: (key) => http.delete(`/ai/ephemeral/recent/${encodeURIComponent(key)}`),
}

// 笔记（PRD 模块 C）
export const noteApi = {
  // 不传 materialId 时返回「材料无关笔记」（AI 问答 / 学习周报 / 播客脚本），
  // 供统计页、播客页查询「这个产物转过笔记没有」
  // ⚠️ 只用 anchor 做状态查询的调用方请带 { slim: 1 }：不带的话会把所有笔记的
  // 正文一起拉下来（播客脚本 / 周报 / 问答的全文是这批数据里最大的一块）
  list: (materialId, params) => http.get('/notes', {
    params: { ...(materialId ? { material_id: materialId } : {}), ...(params || {}) },
  }),
  create: (data) => http.post('/notes', data),
  update: (id, data) => http.put(`/notes/${id}`, data),
  remove: (id) => http.delete(`/notes/${id}`),
  fromChat: (data) => http.post('/notes/from-chat', data),   // AI 问答转笔记（标题=问题，内容=回答）
  fromAi: (data) => http.post('/notes/from-ai', data),       // 材料无关 AI 产物转笔记（周报 / 播客脚本），按 anchor.key 幂等
}

// 知识库（PRD 模块 D）
export const kbApi = {
  overview: (params) => http.get('/kb/overview', { params }),
  rebuild: (materialId) => http.post(`/kb/rebuild`, { material_id: materialId }),
  search: (query) => http.post('/kb/search', { query }),
  entities: () => http.get('/kb/entities'),
  extractEntities: (materialId) => http.post(`/materials/${materialId}/extract-entities`),
  deleteEntities: (materialId) => http.delete(`/materials/${materialId}/entities`),
}

// 文件夹（材料库扩展）
export const foldersApi = {
  list: () => http.get('/folders'),
  create: (data) => http.post('/folders', data),
  update: (id, data) => http.patch(`/folders/${id}`, data),
  remove: (id) => http.delete(`/folders/${id}`),
}

// 全局问答（PRD 模块 E）
export const chatApi = {
  sessions: () => http.get('/chat/sessions'),
  createSession: () => http.post('/chat/sessions'),
  removeSession: (id) => http.delete(`/chat/sessions/${id}`),
  renameSession: (id, title) => http.patch(`/chat/sessions/${id}`, { title }),
  pinSession: (id, pinned) => http.patch(`/chat/sessions/${id}/pin`, { pinned }),
  messages: (sessionId) => http.get(`/chat/sessions/${sessionId}/messages`),
  truncateMessages: (sessionId, messageId) => http.delete(`/chat/sessions/${sessionId}/messages/from/${messageId}`),   // 截断会话（编辑问题重新生成）
  // 流式问答走 fetch（axios 不便处理 SSE），在 ChatView 内实现
}

// 复习（V2 记忆曲线）
export const reviewApi = {
  createCard: (noteId) => http.post('/review/cards', { note_id: noteId }, { timeout: 180000 }),
  createRecallCard: (noteId) => http.post('/review/cards/recall', { note_id: noteId }, { timeout: 180000 }),
  createCardsBatch: (materialId) => http.post('/review/cards/batch', { material_id: materialId }, { timeout: 300000 }),
  cards: (params) => http.get('/review/cards', { params }),
  today: (params) => http.get('/review/today', { params }),
  cardsByStatus: (status, params) => http.get('/review/cards/status', { params: { status, ...params } }),
  filters: () => http.get('/review/filters'),
  grade: (id, result) => http.post(`/review/cards/${id}/grade`, { result }),
  undoGrade: (id) => http.post(`/review/cards/${id}/undo-grade`),
  convert: (id) => http.post(`/review/cards/${id}/convert`, {}, { timeout: 180000 }),
  update: (id, payload) => http.put(`/review/cards/${id}`, payload),
  split: (noteId) => http.post('/review/cards/split', { note_id: noteId }, { timeout: 180000 }),
  quiz: (materialId, count) => http.post('/review/quiz', { material_id: materialId, count: count ?? 8 }, { timeout: 300000 }),
  quizAnswer: (cardId, correct) => http.post('/review/quiz-answer', { card_id: cardId, correct }),
  remove: (id) => http.delete(`/review/cards/${id}`),
  stats: () => http.get('/review/stats')
}

// 简单学习（V 新增）：选材料 → 一键生成结构化学习笔记 + 出题入复习队列
export const simpleLearnApi = {
  // SSE 流式生成（启动或附着），要完整路径（streamSSE 走 fetch，不走 axios 前缀）
  runStreamUrl: '/api/simple-learn/run/stream',
  status: (materialId) => http.get('/simple-learn/run/status', { params: { material_id: materialId } }),
  note: (materialId) => http.get('/simple-learn/note', { params: { material_id: materialId } }),
  // 只重跑出题（讲义不动）：用于「讲义已生成但出题失败」时的重试
  quiz: (materialId, quizCount) => http.post('/simple-learn/quiz', { material_id: materialId, quiz_count: quizCount }),
}

// 语音模型（Whisper 转写模型：状态 / 下载 / 进度）
export const asrApi = {
  models: () => http.get('/asr/models'),
  download: (size) => http.post('/asr/download', { size }),
  downloadStatus: () => http.get('/asr/download/status')
}

// 语音合成模型（本地 ZipVoice：状态 / 下载 / 进度 / 音色）
// 与 asrApi 同构 —— 前端复用同一套「下载卡片 + 轮询」交互
export const zipvoiceApi = {
  models: () => http.get('/zipvoice/models'),
  download: () => http.post('/zipvoice/download'),
  downloadStatus: () => http.get('/zipvoice/download/status'),
  voices: () => http.get('/zipvoice/voices')
}

// 设置（PRD 模块 F）
export const settingsApi = {
  get: () => http.get('/settings'),
  update: (payload) => http.put('/settings', payload),
  testModel: (payload) => http.post('/settings/test-model', payload),   // 按单个模型配置测试连接
  listModels: (payload) => http.post('/settings/list-models', payload),   // 按 base_url+key 拉取服务商模型清单
  testEmbedding: () => http.post('/settings/test-embedding'),
  storage: () => http.get('/settings/storage'),
  usage: () => http.get('/settings/usage'),
  backupUrl: '/api/settings/backup',
  restore: (formData) => http.post('/settings/restore', formData, { timeout: 300000 })
}

// 数据统计面板（模块 H）
export const statsApi = {
  track: (data) => http.post('/stats/track', data),
  overview: () => http.get('/stats/overview'),
  report: () => http.post('/stats/report', {}, { timeout: 180000 }),
  reports: () => http.get('/stats/reports'),
  getReport: (id) => http.get(`/stats/reports/${id}`)
}

// AI 播客（模块 I）：素材 → 知识简报 → 对话脚本（可编辑确认）→ 本地音频
// 生成与合成均耗时较长（含 LLM 两次调用 / 逐句 TTS），超时放宽到 600s
export const podcastApi = {
  options: () => http.get('/podcasts/options'),
  list: () => http.get('/podcasts'),
  detail: (id) => http.get(`/podcasts/${id}`),
  generate: (payload) => http.post('/podcasts/generate', payload, { timeout: 600000 }),
  regenerate: (id, payload) => http.post(`/podcasts/${id}/regenerate`, payload, { timeout: 600000 }),
  // SSE 流式生成脚本（带阶段进度）。streamSSE 要完整路径，不走 axios 的 /api 前缀
  generateStreamUrl: () => '/api/podcasts/generate/stream',
  regenerateStreamUrl: (id) => `/api/podcasts/${id}/regenerate/stream`,
  saveScript: (id, payload) => http.put(`/podcasts/${id}/script`, payload),
  synthesize: (id, payload) => http.post(`/podcasts/${id}/synthesize`, payload || {}, { timeout: 600000 }),
  // SSE 流式合成（带逐句进度）。streamSSE 要完整路径，不走 axios 的 /api 前缀
  synthesizeStreamUrl: (id) => `/api/podcasts/${id}/synthesize/stream`,
  synthStatus: (id) => http.get(`/podcasts/${id}/synthesize/status`),
  rename: (id, title) => http.put(`/podcasts/${id}`, { title }),
  remove: (id) => http.delete(`/podcasts/${id}`),
  // v 传音频字节数：重新合成后字节数变化 → URL 变化 → <audio> 才会丢掉旧缓冲
  audioUrl: (id, v) => `/api/podcasts/${id}/audio${v ? `?v=${v}` : ''}`,
  reveal: (id) => http.post(`/podcasts/${id}/reveal`),
  audioPath: (id) => http.get(`/podcasts/${id}/path`),
  exportUrl: (id) => `/api/podcasts/${id}/export`,
  srtUrl: (id) => `/api/podcasts/${id}/srt`,
  voicePreviewUrl: (voiceId) => `/api/podcasts/voices/${encodeURIComponent(voiceId)}/preview`,
  // 背景音乐
  bgmList: () => http.get('/podcasts/bgm'),
  bgmPreviewUrl: (bgmId) => `/api/podcasts/bgm/${encodeURIComponent(bgmId)}/preview`,
  uploadBgm: (formData) => http.post('/podcasts/bgm/upload', formData, { timeout: 300000 }),
  removeBgm: (bgmId) => http.delete(`/podcasts/bgm/${encodeURIComponent(bgmId)}`),
  setBgm: (id, payload) => http.put(`/podcasts/${id}`, payload),
  testVoice: () => http.post('/podcasts/test-voice', {}, { timeout: 120000 })
}

// AI 陪练（模块 J · V1.0）：选题纲 → 逐题作答（AI 评分）→ 成绩单 + 错题回写复习
// ⚠️ 两条 SSE 流用**完整路径**：streamSSE / 自定义 fetch 不走 axios 的 /api 前缀协商
export const drillApi = {
  // 范围内容量估算（纯 DB 统计，不触发检索）→ 用于判断「开始陪练」是否可点
  scopePreview: (payload) => http.post('/drill/scope/preview', payload),
  create: (payload) => http.post('/drill/sessions', payload),
  list: (params) => http.get('/drill/sessions', { params }),
  detail: (sid) => http.get(`/drill/sessions/${sid}`),
  remove: (sid) => http.delete(`/drill/sessions/${sid}`),
  // 跨场次趋势（优化 3）：服务端聚合已落库的漏点，看短板是否在收敛
  trend: (limit) => http.get('/drill/trend', { params: { limit } }),
  startStreamUrl: (sid) => `/api/drill/sessions/${sid}/start/stream`,
  answerStreamUrl: (sid) => `/api/drill/sessions/${sid}/answer/stream`,
  finish: (sid, reason) => http.post(`/drill/sessions/${sid}/finish`, { reason }),
  // 语音（C 档）：念题返回 mp3 字节流；口述作答返回 { text }
  // ⚠️ 念题的 responseType 必须是 blob —— 走默认的 json 解析会把音频当成坏 JSON 报错
  tts: (text, voice) => http.post('/drill/tts', { text, voice }, { responseType: 'blob', timeout: 120000 }),
  asr: (blob, filename) => {
    const fd = new FormData()
    fd.append('file', blob, filename || 'answer.webm')
    return http.post('/drill/asr', fd, { timeout: 180000 })
  },
}

// AI 视频（PRD V1.0.1）：讲稿与分镜编排 → 视觉步点 → 质量闸门 → 渲染 → 播放 / 导出。
// 与播客的关系：**独立作品类型 + 独立入口**；音轨「复用播客（绝不重新合成）」或本片单独生成。
// ⚠️ 两条约束贯穿前端：
//   ① 生成是「启动或附着」的长任务 —— SSE 只是进度订阅者，用户离开页面不会中断生成，
//      回来时用 planStatus 问一句「还在跑吗」再决定是否重新订阅；
//   ② 产物 URL 带上字节数（`?v=`）做 cache-buster：重新生成后 <audio>/<video> 才会丢掉旧缓冲。
//      （后端对这三个端点用的是 `Cache-Control: private, no-cache`，不解析这个参数，纯前端用。）
export const videoApi = {
  options: () => http.get('/videos/options'),
  list: () => http.get('/videos'),
  detail: (id) => http.get(`/videos/${id}`),
  create: (payload) => http.post('/videos', payload),
  update: (id, payload) => http.put(`/videos/${id}`, payload),
  remove: (id) => http.delete(`/videos/${id}`),
  // 讲稿保存在**已有音轨**的前提下进行：改一句丢一句时间戳（时间轴唯一来源是音轨）
  saveScript: (id, script) => http.put(`/videos/${id}/script`, { script }),
  // 兼容性预检（硬规则）：某份材料做不了「材料原件」时，UI 据此置灰并说明原因
  precheck: (id) => http.get(`/videos/${id}/precheck`),
  // SSE 流式生成（启动或附着）。streamSSE 走 fetch，不走 axios 的 /api 前缀协商
  planStreamUrl: (id) => `/api/videos/${id}/plan/stream`,
  planStatus: (id) => http.get(`/videos/${id}/plan/status`),
  // 重跑质量闸门（改讲稿后调用；不重新生成讲稿与锚点）
  gate: (id) => http.post(`/videos/${id}/gate`),
  // 一键补步（PRD 3.9.6）：只重跑定位/步点生成 + 闸门，不动讲稿与音轨（含 LLM 调用，超时放宽）
  refillSteps: (id) => http.post(`/videos/${id}/steps/refill`, {}, { timeout: 300000 }),
  // 手动指定未命中句的原文位置（PRD 3.9.5 判据 4 修复入口）
  manualStep: (id, seg, anchor) => http.post(`/videos/${id}/steps/manual`, { seg, anchor }),
  // 渲染（阶段 2）：闸门通过才会入队；**并发上限 1**，超限不是失败而是排队并展示位次。
  // ⚠️ render 是幂等的：已在队列/正在渲染时返回同一个任务的位次，不会重复入队。
  // skipGate：连续 3 次未通过自检时才允许（PRD 3.9.7），其余情况后端照拦
  render: (id, skipGate = false) => http.post(`/videos/${id}/render`, skipGate ? { skip_gate: true } : {}),
  renderStreamUrl: (id) => `/api/videos/${id}/render/stream`,
  renderStatus: (id) => http.get(`/videos/${id}/render/status`),
  audioUrl: (id, v) => `/api/videos/${id}/audio${v ? `?v=${v}` : ''}`,
  srtUrl: (id) => `/api/videos/${id}/srt`,
  // 播放器内字幕轨（<track> 只认 WebVTT；与 SRT 同一条时间轴）
  vttUrl: (id, v) => `/api/videos/${id}/vtt${v ? `?v=${v}` : ''}`,
  fileUrl: (id, v) => `/api/videos/${id}/file${v ? `?v=${v}` : ''}`,
  posterUrl: (id, v) => `/api/videos/${id}/poster${v ? `?v=${v}` : ''}`,
  reveal: (id) => http.post(`/videos/${id}/reveal`),
  exportUrl: (id) => `/api/videos/${id}/export`,
  // 转笔记：幂等键 = video:{id}:{讲稿指纹}，anchor.kind = video_script（不与摘要抢认领）
  toNote: (id) => http.post(`/videos/${id}/to-note`),
  // 数字人形象上传（竖版 ≥3:4 且短边 ≥512px）
  uploadAvatar: (formData) => http.post('/videos/avatar/upload', formData, { timeout: 120000 }),
  // 删除一个用户上传的形象（预置形象后端会拒）。返回带 used_by：
  // 「有几个作品正在用它」，前端据此在确认框里如实提示后果。
  deleteAvatar: (id) => http.delete(`/videos/avatar/${id}`),
  // 自定义背景图（解压型皮肤 radio / night / ambient 共用一份**全局背景库**）。
  // ⚠️ 与形象同构：作品只存库里的 id，不存路径 —— 删作品不删素材。
  uploadBackground: (formData) => http.post('/videos/background/upload', formData, { timeout: 120000 }),
  // 删除一张上传的背景（内置背景不是文件、后端会拒）。返回带 used_by：
  // 「有几个作品正在用它」，前端据此在确认框里如实说明后果（与 deleteAvatar 同一口径）。
  deleteBackground: (id) => http.delete(`/videos/background/${id}`),
}
