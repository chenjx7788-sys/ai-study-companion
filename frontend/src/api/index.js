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

// 笔记（PRD 模块 C）
export const noteApi = {
  list: (materialId) => http.get('/notes', { params: { material_id: materialId } }),
  create: (data) => http.post('/notes', data),
  update: (id, data) => http.put(`/notes/${id}`, data),
  remove: (id) => http.delete(`/notes/${id}`),
  fromChat: (data) => http.post('/notes/from-chat', data),   // AI 问答转笔记（标题=问题，内容=回答）
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
