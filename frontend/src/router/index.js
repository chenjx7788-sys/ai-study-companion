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
  { path: '/help', name: 'help', component: () => import('../views/HelpView.vue'), meta: { title: '使用帮助' } }
]

export default createRouter({
  history: createWebHistory(),
  routes
})
