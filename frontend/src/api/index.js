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

// 应用内 AI 浏览器（阶段 2 · WP12）：宿主状态 + 侧栏数据面。
// ⚠️ 后端把 `/api/browser/*` 注册在 SPA catch-all **之前**；若哪天挪到之后，
//    这里所有请求会变成 404（症状像"路径写错了"），排查请先看 main.py 的注册顺序。
export const browserApi = {
  // 宿主只读状态；浏览器模式下 ready 恒为 false，是**正确值**不是故障
  state: () => http.get('/browser/state'),
  // 侧栏载荷（无载荷时是确定空态：四个字段都在、值为空串）
  sidebarPayload: () => http.get('/browser/sidebar/payload'),
  // 把选中内容投递给侧栏；返回 payload_saved（数据面）与 host_shown（显示面）两个独立字段
  openSidebar: (payload) => http.post('/browser/sidebar/open', payload),
  clearSidebar: () => http.post('/browser/sidebar/clear'),
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

// 语音模型（Whisper 转写模型：状态 / 下载 / 进度）
export const asrApi = {
  models: () => http.get('/asr/models'),
  download: (size) => http.post('/asr/download', { size }),
  downloadStatus: () => http.get('/asr/download/status')
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
