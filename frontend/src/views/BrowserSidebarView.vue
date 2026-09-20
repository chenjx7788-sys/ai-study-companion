<template>
  <div class="page browser-sidebar">
    <header class="sb-head">
      <div class="sb-title">
        <span class="sb-dot" :class="{ on: hostReady }"></span>
        AI 侧栏
      </div>
      <span class="sb-host">{{ hostReady ? '浏览器视图已就绪' : '当前无浏览器视图' }}</span>
    </header>

    <!-- 空态：文案要能被验收脚本反向断言（"种子出现 / 空态不出现"这对判据要求它稳定存在） -->
    <section v-if="!hasContent" class="sb-empty">
      <img :src="mascot" class="sb-empty-img" alt="伴伴" />
      <p class="sb-empty-title">等待你从网页里选中内容</p>
      <p class="sb-empty-desc">在网页里划选一段文字后，解释 / 总结 / 出题的结果会出现在这里。</p>
    </section>

    <section v-else class="sb-body">
      <div class="sb-source">
        <div class="sb-src-title" :title="payload.title">{{ payload.title || '未命名页面' }}</div>
        <div v-if="payload.url" class="sb-src-url" :title="payload.url">{{ payload.url }}</div>
      </div>
      <div class="sb-label">选中内容</div>
      <div class="sb-selection" data-role="sidebar-selection">{{ payload.selection }}</div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import mascot from '../assets/mascot.png'
import { browserApi } from '../api'

const hostReady = ref(false)
const payload = ref({ url: '', title: '', selection: '', ts: 0 })

// ⚠️ 判「有没有内容」只看 selection：侧栏的核心是「有没有一段可交给 AI 的文本」。
//    若改判 url，会出现「只投了链接、没有正文」也被当成有内容 → 空态判据失真。
const hasContent = computed(() => !!(payload.value.selection || '').trim())

async function pullPayload() {
  try {
    const { data } = await browserApi.sidebarPayload()
    payload.value = data
  } catch { /* 静默：拿不到就保持空态，不在侧栏里弹错误 */ }
}

async function pullHost() {
  try {
    const { data } = await browserApi.state()
    hostReady.value = !!data.ready
  } catch { /* 静默 */ }
}

let timer = null
onMounted(() => {
  pullHost()
  pullPayload()
  // ⚠️ 用轮询而不是推送：投递方（WP16 的注入脚本经后端）与侧栏是**两个独立的文档**，
  //    目前没有从 Python 主动推 JS 的通道（那要 evaluate_js，只在 Qt 宿主下可用）。
  //    1.5s 让用户主观上感觉"即时"，且这条链在浏览器模式下也能完整验证。
  timer = setInterval(pullPayload, 1500)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<style scoped>
/* 侧栏是被嵌进 Qt 的窄栏（约 360px），一律按窄容器排版 */
.browser-sidebar {
  height: 100vh; padding: 0; display: flex; flex-direction: column;
  background: var(--asc-bg);
}
.sb-head {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 12px 14px; border-bottom: 1px solid var(--asc-border);
  flex-shrink: 0;
}
.sb-title { display: flex; align-items: center; gap: 7px; font-size: 13.5px; font-weight: 600; color: var(--asc-text); }
.sb-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--asc-text-3); flex-shrink: 0; }
.sb-dot.on { background: #22a06b; }
.sb-host { font-size: 11.5px; color: var(--asc-text-3); }
.sb-empty {
  flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
  padding: 24px 20px; text-align: center;
}
.sb-empty-img { width: 54px; height: 54px; border-radius: 50%; opacity: .9; margin-bottom: 12px; }
.sb-empty-title { margin: 0 0 6px; font-size: 13.5px; font-weight: 500; color: var(--asc-text-2); }
.sb-empty-desc { margin: 0; font-size: 12px; line-height: 1.7; color: var(--asc-text-3); }
.sb-body { flex: 1; overflow-y: auto; padding: 14px; }
.sb-source { padding-bottom: 10px; border-bottom: 1px solid var(--asc-divider); margin-bottom: 12px; }
.sb-src-title {
  font-size: 13px; font-weight: 500; color: var(--asc-text); margin-bottom: 3px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sb-src-url {
  font-size: 11.5px; color: var(--asc-text-3);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sb-label { font-size: 11.5px; color: var(--asc-text-3); margin-bottom: 6px; }
.sb-selection {
  font-size: 13px; line-height: 1.75; color: var(--asc-text-2);
  white-space: pre-wrap; word-break: break-word;
  background: var(--asc-surface-2); border-radius: 8px; padding: 10px 12px;
}
</style>
