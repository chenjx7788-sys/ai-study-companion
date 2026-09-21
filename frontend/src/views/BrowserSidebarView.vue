<template>
  <div class="page browser-sidebar">
    <header class="sb-head">
      <div class="sb-title">
        <span class="sb-dot" :class="{ on: hostReady }"></span>
        AI 侧栏
      </div>
      <span class="sb-host">{{ hostReady ? '浏览器视图已就绪' : '当前无浏览器视图' }}</span>
    </header>

    <!-- WP15：把**当前网页**抽成正文并入库。带用户自己的登录态 → 服务端被抓 403 的站点也能取。 -->
    <!-- ⚠️ 这一块与"划选内容"是**两件事**：不依赖 selection，故不受上面的空态分支影响。 -->
    <section class="sb-fetch">
      <button class="sb-fetch-btn" data-role="sb-fetch" :disabled="busy" @click="fetchPage">
        {{ busy ? '正在读取…' : '读取当前网页' }}
      </button>
      <p v-if="fetchMsg" class="sb-fetch-msg" data-role="sb-fetch-msg">{{ fetchMsg }}</p>
      <div v-if="cand" class="sb-cand" data-role="sb-cand">
        <div class="sb-cand-title" :title="cand.title">{{ cand.title || '未命名网页' }}</div>
        <div class="sb-cand-meta">
          {{ cand.kindLabel }} · {{ cand.chars }} 字<template v-if="cand.images"> · {{ cand.images }} 张图</template><template v-if="cand.truncated"> · 已截断</template>
        </div>
        <button class="sb-cand-save" data-role="sb-save"
                :disabled="busy || !cand.canSave || cand.saved" @click="saveCand">
          {{ cand.saved ? '已在知识库' : '加入知识库' }}
        </button>
      </div>
    </section>

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
import { browserApi, clipApi } from '../api'

const hostReady = ref(false)
const payload = ref({ url: '', title: '', selection: '', ts: 0 })
// WP15：取源 / 入库的界面状态。source 留在内存（不回填、不落库），只在点「加入知识库」时回传。
const busy = ref(false)
const fetchMsg = ref('')
const cand = ref(null)

// ⚠️ 判「有没有内容」只看 selection：侧栏的核心是「有没有一段可交给 AI 的文本」。
//    若改判 url，会出现「只投了链接、没有正文」也被当成有内容 → 空态判据失真。
const hasContent = computed(() => !!(payload.value.selection || '').trim())

async function pullPayload() {
  try {
    const { data } = await browserApi.sidebarPayload()
    payload.value = data
  } catch { /* 静默：拿不到就保持空态，不在侧栏里弹错误 */ }
}

// ⚠️ 错误码 → 人话。后端把「环境不支持」与「真失败」分成不同 `error`，
//    界面必须**分别**给出不同的说法（合成一句"读取失败"会让用户不知道要不要去登录浏览器）。
function extractErrText(err) {
  const map = {
    host_unavailable: '当前没有可用的浏览器视图（请用桌面版打开）',
    no_panel: '请先打开 AI 浏览器面板',
    no_view: '请先打开 AI 浏览器面板',
    no_url: '当前标签还没有打开网页',
    timeout: '读取超时，请确认页面已加载完成后重试',
    superseded: '页面已切换，请重试',
    fetch_failed: '页面读取失败，请确认页面能正常打开后重试',
    empty_source: '没有取到页面内容',
  }
  return map[err] || ('读取失败：' + (err || '未知原因'))
}

// 一步做两件事：① 让后端从**页面原始源码**抽正文（带登录态，绕开服务端 403）；
// ② 把抽取结果交给既有的 clipApi.save 入库。
// ⚠️ 取源与入库必须**分成两次调用**：抽取链与入库链各有唯一实现，不能在这里合成一套。
async function fetchPage() {
  busy.value = true
  fetchMsg.value = ''
  cand.value = null
  try {
    const { data } = await browserApi.extract()
    if (!data.ok) {
      fetchMsg.value = extractErrText(data.error)
      return
    }
    const pv = data.preview || {}
    cand.value = {
      url: data.url,
      source: data.source,
      title: data.title || pv.title || '',
      kindLabel: pv.kind_label || '',
      chars: pv.chars || 0,
      images: pv.images || 0,
      truncated: !!data.truncated,
      canSave: pv.action === 'ready',
      saved: !!pv.saved,
    }
    if (pv.action !== 'ready') {
      fetchMsg.value = pv.hint || '这个页面暂时不能直接入库，可以改用划选正文。'
    } else if (data.truncated) {
      fetchMsg.value = '页面较长，已按上限截断后读取，可能不含完整正文。'
    }
  } catch (e) {
    fetchMsg.value = '读取失败，请重试'
  } finally {
    busy.value = false
  }
}

async function saveCand() {
  if (!cand.value) return
  busy.value = true
  try {
    // ⚠️ 传的是 `source`（页面源码），不是 `text`（已抽正文）—— 后端的语义不同。
    const { data } = await clipApi.save({ url: cand.value.url, source: cand.value.source })
    cand.value.saved = true
    fetchMsg.value = data && data.duplicated ? '这篇已经在知识库里了' : '已加入知识库'
  } catch (e) {
    fetchMsg.value = '入库失败，请重试'
  } finally {
    busy.value = false
  }
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
.sb-fetch {
  padding: 12px 14px; border-bottom: 1px solid var(--asc-divider); flex-shrink: 0;
}
.sb-fetch-btn {
  width: 100%; padding: 8px 10px; font-size: 13px; cursor: pointer;
  color: var(--asc-text); background: var(--asc-surface-2);
  border: 1px solid var(--asc-border); border-radius: 8px;
}
.sb-fetch-btn:disabled { opacity: .55; cursor: default; }
.sb-fetch-msg { margin: 8px 0 0; font-size: 12px; line-height: 1.6; color: var(--asc-text-3); }
.sb-cand { margin-top: 10px; padding: 10px 12px; background: var(--asc-surface-2); border-radius: 8px; }
.sb-cand-title {
  font-size: 13px; font-weight: 500; color: var(--asc-text); margin-bottom: 4px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sb-cand-meta { font-size: 11.5px; color: var(--asc-text-3); margin-bottom: 8px; }
.sb-cand-save {
  width: 100%; padding: 7px 10px; font-size: 12.5px; cursor: pointer;
  color: #fff; background: var(--asc-primary, #3a6df0);
  border: none; border-radius: 7px;
}
.sb-cand-save:disabled { opacity: .55; cursor: default; }
</style>
