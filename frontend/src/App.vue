<template>
  <el-container class="app-shell" :class="{ 'app-shell-bare': isBare }">
    <aside v-if="!isBare" class="app-aside" :class="{ collapsed: sidebarCollapsed }">
      <div class="logo" @click="$router.push('/')">
        <img :src="mascot" class="logo-mark-img" alt="伴学猫头鹰" />
        <span class="logo-text">AI 伴学助手</span>
      </div>
      <nav class="nav">
        <div v-for="item in navItems" :key="item.path"
          class="nav-item" :class="{ active: isActive(item.path) }"
          :data-nav="item.path" :title="sidebarCollapsed ? item.label : ''" @click="$router.push(item.path)">
          <span class="nav-icon" v-html="item.icon"></span>
          <span class="nav-label">{{ item.label }}</span>
          <span v-if="item.path === '/review' && reviewDue > 0" class="due-badge">{{ reviewDue }}</span>
        </div>
      </nav>
      <!-- AI 浏览器入口（阶段 2 · WP12）：不是路由，而是请求宿主打开 Qt 侧浏览器面板。
           ⚠️ 独立于上面的 v-for 循环 —— 该循环的 @click 是路由跳转，而这里要发起动作，
              故不改循环内任何一行（项目既定的「只加不拆」改动姿势）。 -->
      <div v-if="!isBare" class="nav-help nav-browser" :class="{ active: browserOpen }"
        title="在应用内打开网页，选中文字直接问 AI" @click="openBrowser">
        <span class="nav-icon" v-html="browserIcon"></span>
        <span class="nav-label">AI 浏览器</span>
      </div>
      <div class="nav-help" :class="{ active: isActive('/help') }"
        :title="sidebarCollapsed ? '使用帮助' : ''" @click="$router.push('/help')">
        <span class="nav-icon" v-html="helpIcon"></span>
        <span class="nav-label">使用帮助</span>
      </div>
      <div class="aside-footer"><span class="nav-label">个人学习知识中枢</span><span v-if="version" class="ver-tag">v{{ version }}</span></div>
      <div class="collapse-btn" :title="sidebarCollapsed ? '展开菜单' : '折叠菜单'" @click="toggleSidebar">
        <svg v-if="sidebarCollapsed" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 5l7 7-7 7"/><path d="M12 5l7 7-7 7"/></svg>
        <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 5l-7 7 7 7"/><path d="M12 5l-7 7 7 7"/></svg>
        <span class="nav-label">{{ sidebarCollapsed ? '' : '折叠' }}</span>
      </div>
    </aside>
    <el-main class="app-main">
      <div v-if="!isBare && needSetup" class="setup-banner">
        <span class="setup-banner-text">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v2M12 20v2M2 12h2M20 12h2"/><circle cx="12" cy="12" r="4"/></svg>
          还差一步：配置 LLM 模型后，即可开始导入资料、AI 问答与复习
        </span>
        <el-button size="small" type="primary" @click="$router.push('/settings')">前往配置</el-button>
      </div>
      <router-view v-slot="{ Component }">
        <component :is="Component" @tour="restartTour" />
      </router-view>
    </el-main>
    <!-- E1 全局悬浮问答入口（问答页自身不显示） -->
    <div v-if="!isBare && $route.path !== '/chat'" class="float-chat" :class="{ 'float-chat-study': $route.path.startsWith('/study') }"
      @click="goChat" title="问伴伴">
      <img :src="mascot" class="float-mascot" alt="伴学猫头鹰" />
      <span class="float-bubble">有学习问题，问伴伴</span>
    </div>

    <!-- 首次使用分步引导 -->
    <el-tour v-if="!isBare" v-model="tourOpen" placement="right" @close="onTourClose">
      <!-- 未配置模型：把「配置模型」作为第一步（已配置用户不显示此步） -->
      <el-tour-step v-if="needSetup" target="[data-nav='/settings']" placement="right" title="① 先配置 AI 模型">
        <template #default>
          首次使用需配置一个 AI 模型（1 分钟）。点这里前往管理中心：选厂商 → 填 API Key 即可，多数厂商新用户送免费额度。
        </template>
      </el-tour-step>
      <el-tour-step target="[data-nav='/']" placement="right" :title="(needSetup ? '②' : '①') + ' 导入材料'">
        <template #default>
          上传你的第一份学习材料，PDF / PPT / Word / EPUB / 音视频都行，系统会自动解析。
        </template>
      </el-tour-step>
      <el-tour-step target="[data-nav='/chat']" placement="right" :title="(needSetup ? '③' : '②') + ' 理解 & 提问'">
        <template #default>
          在学习页做 AI 总结、划线解读，或随时打开 AI 问答向你的知识库提问。
        </template>
      </el-tour-step>
      <el-tour-step target="[data-nav='/review']" placement="right" :title="(needSetup ? '④' : '③') + ' 沉淀 & 抗遗忘'">
        <template #default>
          把重点转成笔记进知识库，用复习巩固翻卡，让学过的内容不遗忘。
        </template>
      </el-tour-step>
      <el-tour-step target=".nav-help" placement="right" title="随时查看帮助">
        <template #default>
          任何疑问，点这里打开使用帮助。
        </template>
      </el-tour-step>
    </el-tour>

    <!-- 引导可随时跳过：遮罩会拦截其余区域点击，若无显式出口，用户会误以为「界面点不动」 -->
    <div v-if="!isBare && tourOpen" class="tour-skip" title="关闭引导，直接开始使用" @click="skipTour">跳过引导</div>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import mascot from './assets/mascot.png'
import { reviewApi, settingsApi, materialApi, browserApi } from './api'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()

// 「裸布局」（阶段 2 · WP12）：AI 侧栏会被嵌进 Qt 的 QWebEngineView，不能带应用外壳，
// 否则侧栏里会再套一层 216px 的应用导航栏、可用宽度被挤掉。
// ⚠️ 只加 v-if 隐藏，不动任何 v-model / @click —— 项目既定的改动安全姿势。
const isBare = computed(() => !!route.meta.bare)

// 从资料详情页进入问答：把当前资料 id 作为检索范围传递过去
function goChat() {
  if (route.path.startsWith('/study') && route.params.id) {
    router.push({ path: '/chat', query: { material_id: route.params.id } })
  } else {
    router.push('/chat')
  }
}

// 复习待办徽标：挂载 + 路由切换 + 每 60s 轮询
const reviewDue = ref(0)
async function refreshDue() {
  try {
    const { data } = await reviewApi.stats()
    reviewDue.value = data.due
  } catch { /* 静默 */ }
}
let dueTimer = null
// 未配置 LLM 时的引导提示（配置完成后消失）
const needSetup = ref(false)
const version = ref('')
async function checkSetup() {
  try {
    const { data } = await settingsApi.get()
    needSetup.value = !data.configured
  } catch { /* 静默 */ }
  try {
    const r = await fetch('/api/version')
    const d = await r.json()
    version.value = d.version
  } catch { /* 静默 */ }
}
onMounted(() => {
  refreshDue()
  dueTimer = setInterval(refreshDue, 60000)
  // 先等配置状态就绪，再决定首次引导内容（未配置时第一步为「配置模型」）
  checkSetup().then(async () => {
    if (localStorage.getItem(ONBOARD_KEY)) return
    // 已有材料 → 说明不是首次使用（例如清过 WebView 缓存导致标记丢失），不再弹引导
    try {
      const { data } = await materialApi.list()
      if (Array.isArray(data) && data.length > 0) {
        localStorage.setItem(ONBOARD_KEY, '1')
        return
      }
    } catch { /* 静默：取不到就仍按首次处理 */ }
    // 裸布局（AI 侧栏）不弹新手引导：其 target 选择器在裸布局下不存在，弹出来只会是空框
    if (!isBare.value) setTimeout(() => { tourOpen.value = true }, 600)
  })
  // 点击遮罩空白处 = 跳过引导（否则遮罩会拦下所有点击，造成「界面卡死」的错觉）
  document.addEventListener('click', onMaskClick, true)
})
onUnmounted(() => dueTimer && clearInterval(dueTimer))
watch(() => route.path, refreshDue)

const navItems = [
  { path: '/', label: '材料库', icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1.5" stroke="currentColor" stroke-width="1.3"/><rect x="9" y="2" width="5" height="5" rx="1.5" stroke="currentColor" stroke-width="1.3"/><rect x="2" y="9" width="5" height="5" rx="1.5" stroke="currentColor" stroke-width="1.3"/><rect x="9" y="9" width="5" height="5" rx="1.5" stroke="currentColor" stroke-width="1.3"/></svg>' },
  { path: '/knowledge', label: '知识库', icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2.5c-1.2-.9-3-1.2-5-.8v10.6c2-.4 3.8-.1 5 .8 1.2-.9 3-1.2 5-.8V1.7c-2-.4-3.8-.1-5 .8z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 2.5v10.6" stroke="currentColor" stroke-width="1.3"/></svg>' },
  { path: '/chat', label: 'AI 问答', icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6a1.5 1.5 0 0 1-1.5 1.5H8l-3.5 3v-3h-1A1.5 1.5 0 0 1 2 9.5v-6z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>' },
  { path: '/podcast', label: 'AI 播客', icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2.6 9.4V8a5.4 5.4 0 0 1 10.8 0v1.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><rect x="1.7" y="8.7" width="3.1" height="4.5" rx="1.55" stroke="currentColor" stroke-width="1.3"/><rect x="11.2" y="8.7" width="3.1" height="4.5" rx="1.55" stroke="currentColor" stroke-width="1.3"/></svg>' },
  { path: '/review', label: '复习巩固', icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M13.5 1.8v2.6h-2.6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>' },
  { path: '/stats', label: '数据统计', icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 13h2V8H3v5zM7 13h2V3H7v10zM11 13h2V6h-2v7z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>' },
  { path: '/settings', label: '管理中心', icon: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="2.2" stroke="currentColor" stroke-width="1.3"/><path d="M8 1.8v1.7M8 12.5v1.7M1.8 8h1.7M12.5 8h1.7M3.6 3.6l1.2 1.2M11.2 11.2l1.2 1.2M12.4 3.6l-1.2 1.2M4.8 11.2l-1.2 1.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>' },
]

const helpIcon = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.3"/><path d="M6.2 6.2a1.9 1.9 0 1 1 2.7 1.7c-.6.3-.9.8-.9 1.5v.4" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="8" cy="11.6" r=".7" fill="currentColor"/></svg>'

// AI 浏览器（阶段 2 · WP12）：入口是**动作**而非路由 —— 它请求宿主在 Qt 侧打开浏览器面板。
const browserIcon = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="1.8" y="3" width="12.4" height="10" rx="1.8" stroke="currentColor" stroke-width="1.3"/><path d="M1.8 6.2h12.4" stroke="currentColor" stroke-width="1.3"/><circle cx="4.1" cy="4.6" r=".55" fill="currentColor"/><circle cx="6" cy="4.6" r=".55" fill="currentColor"/></svg>'
const browserOpen = ref(false)
async function openBrowser() {
  // ⚠️ 浏览器模式（无原生窗口）下 opened=false 是**正确值**，不该弹错误吓用户；
  //    只有真投递失败（host_error 非 host_unavailable）才提示。
  try {
    const { data } = await browserApi.open({})
    browserOpen.value = !!data.opened
    if (!data.opened && data.host_error && data.host_error !== 'host_unavailable') {
      ElMessage.warning('浏览器面板打开失败：' + data.host_error)
    }
  } catch (e) {
    ElMessage.warning('浏览器面板打开失败')
  }
}

// 首次使用分步引导（localStorage 记忆，仅首次弹出）
const tourOpen = ref(false)
const ONBOARD_KEY = 'asc_onboarded'
function restartTour() { tourOpen.value = true }
function onTourClose() { localStorage.setItem(ONBOARD_KEY, '1') }
// 跳过引导：显式写入标记（v-model 置 false 不会触发 close 事件）
function skipTour() {
  localStorage.setItem(ONBOARD_KEY, '1')
  tourOpen.value = false
}
// 遮罩空白区点击 → 跳过
function onMaskClick(e) {
  if (!tourOpen.value) return
  const cls = e.target && e.target.classList
  if (cls && (cls.contains('el-tour__hollow') || cls.contains('el-tour__mask'))) skipTour()
}

const isActive = (path) => path === '/' ? route.path === '/' : route.path.startsWith(path)

// 侧边栏折叠（小屏幕释放横向空间，localStorage 记忆）
const sidebarCollapsed = ref(localStorage.getItem('asc_sidebar_collapsed') === '1')
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('asc_sidebar_collapsed', sidebarCollapsed.value ? '1' : '0')
}
// 进入材料详情页默认折叠菜单（给阅读区腾空间），离开后恢复 localStorage 记忆状态
// 离开学习页时错开一帧再展开：先让当前帧完成 StudyView 卸载（PDF canvas / Word 原文 DOM）与目标页挂载，
// 再触发侧边栏 width 过渡，避免主线程争抢导致「菜单展开卡顿」
watch(() => route.path, (path) => {
  if (path.startsWith('/study')) {
    sidebarCollapsed.value = true
  } else {
    requestAnimationFrame(() => {
      sidebarCollapsed.value = localStorage.getItem('asc_sidebar_collapsed') === '1'
    })
  }
}, { immediate: true })
</script>

<style scoped>
.app-shell { height: 100vh; }
.app-aside {
  width: 216px; flex-shrink: 0; background: var(--asc-bg);
  border-right: 1px solid var(--asc-border);
  display: flex; flex-direction: column;
  transition: width .2s ease;
}
.app-aside.collapsed { width: 64px; }
.app-aside.collapsed .nav-label,
.app-aside.collapsed .logo-text,
.app-aside.collapsed .ver-tag { display: none; }
.app-aside.collapsed .nav-item,
.app-aside.collapsed .nav-help { justify-content: center; padding-left: 0; padding-right: 0; }
.app-aside.collapsed .nav-item { position: relative; }
.app-aside.collapsed .due-badge { position: absolute; right: 8px; top: 4px; min-width: 16px; height: 16px; font-size: 10px; }
.app-aside.collapsed .collapse-btn { justify-content: center; }
.collapse-btn {
  display: flex; align-items: center; gap: 6px;
  margin: 0 12px 10px; padding: 10px 12px;
  border-top: 1px solid var(--asc-divider);
  font-size: 12px; color: var(--asc-text-3); cursor: pointer;
  transition: color .15s ease;
}
.collapse-btn:hover { color: var(--asc-primary); }
.logo {
  display: flex; align-items: center; gap: 10px;
  padding: 20px 18px 16px; cursor: pointer;
}
.logo-mark-img {
  width: 34px; height: 34px; border-radius: 10px;
  background: var(--asc-primary-soft); padding: 1px;
}
.logo-text { font-size: 15px; font-weight: 600; letter-spacing: .5px; }
.nav { flex: 1; padding: 8px 12px; }
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px; margin-bottom: 2px;
  border-radius: 9px; font-size: 14px; color: var(--asc-text-2);
  cursor: pointer; transition: all .15s ease;
}
.nav-icon { display: flex; align-items: center; opacity: .8; }
.nav-item:hover { background: var(--asc-surface-2); color: var(--asc-text); }
.due-badge {
  margin-left: auto; min-width: 18px; height: 18px; padding: 0 5px;
  background: var(--asc-primary); color: #fff;
  border-radius: 9px; font-size: 11px; font-weight: 600;
  display: inline-flex; align-items: center; justify-content: center;
}
.nav-item.active {
  background: var(--asc-primary-soft); color: var(--asc-primary); font-weight: 500;
  box-shadow: inset 2px 0 0 var(--asc-primary);
}
.nav-help {
  display: flex; align-items: center; gap: 10px;
  margin: 8px 12px 0; padding: 9px 12px 8px;
  border-top: 1px solid var(--asc-divider);
  border-radius: 9px; font-size: 14px; color: var(--asc-text-2);
  cursor: pointer; transition: all .15s ease;
}
.nav-help:hover { background: var(--asc-surface-2); color: var(--asc-text); }
.nav-help.active {
  background: var(--asc-primary-soft); color: var(--asc-primary); font-weight: 500;
  box-shadow: inset 2px 0 0 var(--asc-primary);
}
/* AI 浏览器入口：与「使用帮助」同一视觉族，但**去掉上边框**——
   两者相邻时两条分隔线会连成一段，看起来像把菜单切成了三块。
   ⚠️ 只覆盖 border-top，其余样式全部继承 .nav-help（减少装饰、避免两处漂移）。 */
.nav-browser { border-top: 0; }
.aside-footer {
  padding: 14px 18px; font-size: 11px; color: var(--asc-text-3);
  border-top: 1px solid var(--asc-border);
  display: flex; align-items: center; gap: 8px;
}
.ver-tag {
  margin-left: auto; font-size: 10.5px; color: var(--asc-text-3);
  background: var(--asc-surface-2); padding: 1px 7px; border-radius: 8px;
}
.app-main { padding: 0; background: var(--asc-bg); }
/* 未配置 LLM 的顶部引导条 */
.setup-banner {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding: 10px 22px;
  background: var(--asc-primary-soft);
  border-bottom: 1px solid rgba(124, 92, 252, .16);
}
.setup-banner-text {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 13px; color: var(--asc-primary); font-weight: 500;
}
.setup-banner-text svg { flex-shrink: 0; }
/* ===== E1 全局悬浮问答入口（伴伴 IP） ===== */
.float-chat {
  position: fixed; right: 26px; bottom: 26px; z-index: 90;
  width: 56px; height: 56px; cursor: pointer;
  border-radius: 50%;
  background: linear-gradient(135deg, #8f7bff, #7c5cfc);
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 6px 20px rgba(124, 92, 252, .38);
  transition: transform .2s ease, box-shadow .2s ease;
}
.float-chat:hover { transform: translateY(-3px) scale(1.06); box-shadow: 0 10px 26px rgba(124, 92, 252, .5); }
.float-chat::before {
  content: ''; position: absolute; inset: -6px; border-radius: 50%;
  background: radial-gradient(circle, rgba(124, 92, 252, .3), transparent 70%);
  animation: float-halo 2.8s ease-in-out infinite;
  z-index: -1;
}
.float-mascot {
  width: 44px; height: 44px; border-radius: 50%;
  animation: mascot-float 3.6s ease-in-out infinite;
}
.float-bubble {
  position: absolute; right: 66px; top: 50%; transform: translateY(-50%);
  white-space: nowrap; padding: 8px 14px; border-radius: 12px 4px 12px 12px;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  color: var(--asc-text-2); font-size: 13px; box-shadow: 0 4px 14px rgba(0, 0, 0, .08);
  opacity: 0; pointer-events: none;
  transition: opacity .2s ease, transform .2s ease;
}
.float-chat:hover .float-bubble { opacity: 1; transform: translateY(-50%) translateX(-4px); }
/* 资料详情页上移 40px */
.float-chat-study { bottom: 108px; }
@keyframes mascot-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}
@keyframes float-halo {
  0%, 100% { transform: scale(1); opacity: .7; }
  50% { transform: scale(1.12); opacity: 1; }
}
/* 引导跳过按钮：需位于 el-tour 遮罩（z-index 2001）之上，保证始终可点 */
.tour-skip {
  position: fixed; right: 24px; bottom: 100px; z-index: 2020;
  padding: 8px 16px; border-radius: 999px; cursor: pointer;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  color: var(--asc-text-2); font-size: 13px; box-shadow: 0 4px 14px rgba(0, 0, 0, .12);
  transition: all .2s ease;
}
.tour-skip:hover { color: var(--asc-primary); border-color: var(--asc-primary); }
</style>
