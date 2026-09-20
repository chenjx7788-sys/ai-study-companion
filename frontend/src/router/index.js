import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'library', component: () => import('../views/LibraryView.vue'), meta: { title: '材料库' } },
  { path: '/study/:id', name: 'study', component: () => import('../views/StudyView.vue'), meta: { title: '学习页' } },
  // 临时阅读（P0-6）：正文只进后端内存快照，不落库、不建索引。
  // 进入姿势：候选列表「仅阅读」/ 最近阅读抽屉 / `?url=` 直接打开。
  // 刻意**不放进左侧主导航** —— 它是从"浏览"到"沉淀"的中间态，不是并列的功能区。
  { path: '/read', name: 'read', component: () => import('../views/EphemeralView.vue'), meta: { title: '临时阅读' } },
  { path: '/knowledge', name: 'knowledge', component: () => import('../views/KnowledgeView.vue'), meta: { title: '知识库' } },
  { path: '/chat', name: 'chat', component: () => import('../views/ChatView.vue'), meta: { title: 'AI 问答' } },
  { path: '/review', name: 'review', component: () => import('../views/ReviewView.vue'), meta: { title: '复习巩固' } },
  { path: '/podcast', name: 'podcast', component: () => import('../views/PodcastView.vue'), meta: { title: 'AI 播客' } },
  { path: '/stats', name: 'stats', component: () => import('../views/StatsView.vue'), meta: { title: '数据统计' } },
  { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue'), meta: { title: '管理中心' } },
  { path: '/editor', name: 'editor', component: () => import('../views/EditorView.vue'), meta: { title: '新增文档' } },
  { path: '/editor/:id', name: 'editorEdit', component: () => import('../views/EditorView.vue'), meta: { title: '编辑文档' } },
  { path: '/help', name: 'help', component: () => import('../views/HelpView.vue'), meta: { title: '使用帮助' } },
  // 应用内 AI 浏览器（阶段 2 · WP12）：AI 侧栏。
  // ⚠️ `bare: true` = 走「裸布局」：不渲染应用自己的左侧导航 / 悬浮问答入口 / 新手引导。
  //    该页会被嵌进 Qt 的 QWebEngineView 当侧栏用，套一层应用外壳会挤掉可用宽度、并出现第二套导航。
  { path: '/browser/sidebar', name: 'browserSidebar', component: () => import('../views/BrowserSidebarView.vue'), meta: { title: 'AI 侧栏', bare: true } }
]

export default createRouter({
  history: createWebHistory(),
  routes
})
