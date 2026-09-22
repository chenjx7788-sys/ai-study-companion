<template>
  <div class="page browser-sidebar">
    <header class="sb-head">
      <div class="sb-title">
        <span class="sb-dot" :class="{ on: hostReady }"></span>
        AI 侧栏
      </div>
      <span class="sb-host">{{ hostReady ? '浏览器视图已就绪' : '当前无浏览器视图' }}</span>
    </header>

    <!-- WP17：侧栏标签栏。⚠️ 侧栏与浏览器面板是**两个独立文档**，看不到 Qt 的 QTabBar
         → 必须自己列一遍（数据来自 /browser/tabs，浏览器模式下为空数组，是正常空态）。 -->
    <nav v-if="tabs.length" class="sb-tabs" data-role="sb-tabs">
      <button v-for="t in tabs" :key="t.id" class="sb-tab"
              :class="{ on: t.active }" data-role="sb-tab"
              :title="t.title || t.url || '新标签'"
              @click="switchTab(t)">{{ tabLabel(t) }}</button>
    </nav>

    <!-- WP15：把**当前网页**抽成正文并入库。带用户自己的登录态 → 服务端被抓 403 的站点也能取。 -->
    <!-- ⚠️ 这一块与"划选内容"是**两件事**：不依赖 selection，故不受上面的空态分支影响。 -->
    <section class="sb-fetch">
      <button class="sb-fetch-btn" data-role="sb-fetch" :disabled="busy" @click="fetchPage">
        {{ busy ? '正在读取…' : '读取当前网页' }}
      </button>
      <p v-if="fetchMsg" class="sb-fetch-msg" data-role="sb-fetch-msg">{{ fetchMsg }}</p>
      <!-- WP18 登录墙引导：只在后端判定「像登录墙」时出现；文案固定，供验收逐字断言 -->
      <p v-if="loginHint" class="sb-login-hint" data-role="sb-login-hint">
        这个站点可能需要登录：请在左侧浏览器里完成登录后，再点「读取当前网页」。
      </p>
      <div v-if="cand" class="sb-cand" data-role="sb-cand">
        <div class="sb-cand-title" :title="cand.title">{{ cand.title || '未命名网页' }}</div>
        <div class="sb-cand-meta">
          {{ cand.kindLabel }} · {{ cand.chars }} 字<template v-if="cand.via === 'dom'"> · 正文模式</template><template v-else-if="cand.images"> · {{ cand.images }} 张图</template><template v-if="cand.truncated"> · 已截断</template>
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

      <!-- WP16：三个动作。⚠️ 结果不由这里持有 —— 一律读轮询回来的 `payload.status/result`。
           前端再留一份本地副本，就等于把后端那道 gen 闸又拆了（会出现"显示上一次的答案"）。 -->
      <div class="sb-acts" data-role="sb-acts">
        <button v-for="a in actions" :key="a.key" class="sb-act"
                :class="{ on: payload.action === a.key }"
                :data-role="'sb-act-' + a.key"
                :disabled="acting || !canAct" @click="runAction(a.key)">{{ a.label }}</button>
      </div>
      <p v-if="actMsg" class="sb-act-msg" data-role="sb-act-msg">{{ actMsg }}</p>
      <div v-if="payload.status === 'running'" class="sb-running" data-role="sb-running">正在生成…</div>
      <div v-else-if="payload.status === 'error'" class="sb-error" data-role="sb-error">生成失败：{{ payload.error || '未知原因' }}</div>
      <div v-else-if="payload.result" class="sb-md sb-result" data-role="sb-result" v-html="resultHtml"></div>

      <!-- WP17 流式：边生成边渲染（渲染的仍是**盒子**里的文本，不是前端自己拼的 token）。
           ⚠️ 这是一个**独立节点**、有意不并入上面的三态链 —— 并进去等于宣告
              「running / error / result 三态互斥」这条既有判据失效（WP12 E 组在断言它）。 -->
      <div v-if="streaming" class="sb-md sb-stream" data-role="sb-stream" v-html="resultHtml"></div>
      <div v-if="streaming" class="sb-res-bar">
        <span class="sb-res-msg">正在输出…</span>
      </div>

      <!-- WP17 一键复制（A05）。放在正文**之外**的一行：正文里有链接与代码，
           把按钮浮在正文上会挡内容。 -->
      <div v-if="payload.result && payload.status !== 'running'" class="sb-res-bar">
        <span class="sb-res-msg" data-role="sb-res-msg">{{ resMsg }}</span>
        <button class="sb-mini" data-role="sb-copy" @click="copyResult">复制</button>
      </div>

      <!-- WP17 追问（A06）。只在「已有答案」时出现：没有答案时该用上面那三个动作。
           ⚠️ 历史由**前端**携带、不落库；选区一换就整体作废（旧答案对新文字是错上下文）。 -->
      <form v-if="payload.result && payload.status !== 'running'"
            class="sb-ask" data-role="sb-ask" @submit.prevent="sendAsk">
        <input v-model="askText" class="sb-ask-input" data-role="sb-ask-input" type="text"
               placeholder="就这段文字继续追问…" :disabled="asking" />
        <button class="sb-ask-send" data-role="sb-ask-send" type="submit"
                :disabled="asking || !askText.trim()">{{ asking ? '发送中' : '追问' }}</button>
      </form>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import mascot from '../assets/mascot.png'
import { browserApi, clipApi } from '../api'
import { createMd } from '../utils/md'
// WP17：复制与流式各只有一份实现，不许在本视图里再写一遍（见 utils 内的注释）。
import { copyText } from '../utils/clipboard'
import { streamSSE } from '../utils/sse'

// 选项与 StudyView 一致（渲染 AI 生成正文：保留单换行、自动链接化）。
// ⚠️ 外链 `target=_blank` 由 `createMd` 统一加 —— **不要**在这里另起一个 `new MarkdownIt(...)`，
//    项目已经因为"9 处各写一遍"漂移过一次（只有两个视图开了 linkify）。
const md = createMd({ linkify: true, breaks: true })

// 三个动作。⚠️ `key` 必须与后端 `browser_actions.ACTION_LABELS` 逐字一致 ——
//    后端按 key 分发，写错的症状是"点了没反应"且不报错。
const actions = [
  { key: 'explain', label: '解释' },
  { key: 'summarize', label: '总结' },
  { key: 'quiz', label: '出题' },
]

const hostReady = ref(false)
// 键与后端 `browser_host._sidebar` 逐字对齐（含动作那几个）——
// 少写一个键不会报错，只会让界面永远显示空值（这类 bug 最难查）。
const payload = ref({ url: '', title: '', selection: '', ts: 0,
                      action: '', status: 'idle', result: '', error: '', gen: 0 })
// WP15：取源 / 入库的界面状态。source 留在内存（不回填、不落库），只在点「加入知识库」时回传。
const busy = ref(false)
const fetchMsg = ref('')
const cand = ref(null)
// WP18 登录墙引导：后端判定失败现场像「需要登录」时置真，前端多给一句指引。
// ⚠️ 只引导、不拦截（V3 §6.1：未登录表现与 Chrome 一致，产品不额外拦截、不伪造）。
const loginHint = ref(false)

// ⚠️ 判「有没有内容」只看 selection：侧栏的核心是「有没有一段可交给 AI 的文本」。
//    若改判 url，会出现「只投了链接、没有正文」也被当成有内容 → 空态判据失真。
const hasContent = computed(() => !!(payload.value.selection || '').trim())

// 只用来**立刻禁用按钮**（防连点）。结果态不在这里 —— 一律读 `payload.status`（后端是唯一真相）。
const acting = ref(false)
const actMsg = ref('')
const canAct = computed(() => hasContent.value)
const resultHtml = computed(() => (payload.value.result ? md.render(payload.value.result) : ''))

async function runAction(key) {
  if (!canAct.value || acting.value) return
  acting.value = true
  actMsg.value = ''
  try {
    // ⚠️ 有意**不传** selection：让后端取"当前选区"。
    //    这与网页里点浮动工具栏走的是同一条路（那边也不带 payload）——
    //    传了本地副本就等于多出一份可能过期的选区（用户可能在两次轮询之间又划了别的）。
    const { data } = await browserApi.selectionAction({ action: key })
    if (data && data.ok === false) actMsg.value = actionErrText(data.error)
  } catch {
    actMsg.value = '请求失败，请重试'
  } finally {
    acting.value = false
    pullPayload()
  }
}

// ⚠️ 与 `extractErrText` 同款分工：`bad_action` / `empty_selection` 是**调用方问题**（要提示），
//    「生成失败」是模型/配置侧的问题（走 `payload.status === 'error'`）。两者不要合成一句。
function actionErrText(err) {
  const map = {
    bad_action: '不支持的动作',
    empty_selection: '请先在网页里选中一段文字',
  }
  return map[err] || ('发起失败：' + (err || '未知原因'))
}

// ---------- WP17：流式 / 复制 / 追问 / 标签栏 ----------

// 流式区 = 「正在生成**且**盒子里已有文本」。
// ⚠️⚠️ 前端**绝不**自己把 token 拼成答案：那会多出第二份真相，一旦与后端盒子不一致，
//    症状是「看着流出来的」与「最后停住的」不是同一段文字，且无从复现。
//    所以这里读的仍是 `payload.result` —— 订阅只负责**尽快去取**（见 schedulePull）。
const streaming = computed(() => payload.value.status === 'running' && !!payload.value.result)

const resMsg = ref('')
const askText = ref('')
const asking = ref(false)
const tabs = ref([])

// 追问历史（A06）：由**前端**携带、服务端无状态 → 刷新即丢。这是**有意**的
// （N04：浏览内容不落库），不是缺陷。上限与后端 MAX_HISTORY_MSGS 对齐。
const MAX_HISTORY = 12
const history = ref([])

// 选区一换，历史整体作废：旧答案对新文字是**错上下文** ——
// 而这类错的症状是「答得挺顺但答偏了」，比报错难查得多。
const selKey = computed(() => payload.value.url + '\n' + payload.value.selection)
watch(selKey, () => {
  history.value = []
  askText.value = ''
  resMsg.value = ''
})

// 一次动作跑完（running → 其它）就把答案喂进历史，这样「解释完接着追问」才连贯。
// ⚠️ 判据用 status 的**跃迁**而不是「有 result」：流式期间 result 一直在变，
//    按「有 result」会在输出过程中反复 push 同一段答案。
watch(() => payload.value.status, (now, before) => {
  if (before === 'running' && now !== 'running' && payload.value.result) {
    history.value.push({ role: 'assistant', content: payload.value.result })
    if (history.value.length > MAX_HISTORY) history.value = history.value.slice(-MAX_HISTORY)
  }
})

async function copyResult() {
  const ok = await copyText(payload.value.result || '')
  // ⚠️ 失败必须如实说：一律弹「已复制」会让用户粘贴时才发现是空的。
  resMsg.value = ok ? '已复制' : '复制失败，请手动选择文本'
  setTimeout(() => { resMsg.value = '' }, 1800)
}

async function sendAsk() {
  const q = askText.value.trim()
  if (!q || asking.value || !canAct.value) return
  asking.value = true
  actMsg.value = ''
  try {
    // ⚠️ 与三个动作同款：**不传** selection —— 让后端取「当前选区」。
    //    传本地副本就等于多出一份可能过期的选区（用户可能又划了别的文字）。
    const { data } = await browserApi.selectionAsk({ question: q, history: history.value })
    if (data && data.ok === false) { actMsg.value = actionErrText(data.error); return }
    history.value.push({ role: 'user', content: q })
    askText.value = ''
  } catch {
    actMsg.value = '请求失败，请重试'
  } finally {
    asking.value = false
    pullPayload()
  }
}

// ---------- 侧栏标签栏（WP17） ----------
// ⚠️ 走的是与真机同一个数据面 /browser/tabs；浏览器模式下返回空数组（正常空态），
//    所以这里**不弹错、不显示占位**，只是标签栏不出现。
async function pullTabs() {
  try {
    const { data } = await browserApi.tabs()
    tabs.value = Array.isArray(data && data.tabs) ? data.tabs : []
  } catch { /* 静默 */ }
}

function tabLabel(t) {
  const s = String((t && t.title) || '').trim()
  if (s) return s.length > 14 ? s.slice(0, 14) + '…' : s
  const u = String((t && t.url) || '').trim()
  if (!u) return '新标签'
  try { return new URL(u).hostname.replace(/^www\./, '') } catch { return u.slice(0, 14) }
}

async function switchTab(t) {
  if (!t || t.active) return
  try { await browserApi.tabSwitch({ tab_id: t.id }) } catch { /* 无宿主时是正常约束 */ }
  pullTabs()
}

// ---------- WP17 流式订阅（SSE） ----------
// ⚠️ 订阅是**加速通道**，不是取代轮询：断开 / 后端没重启 / 端点 404 时轮询照旧在跑。
//    因此这里的任何失败都**不弹错、不停轮询**，只按退避重连。
let subAc = null
let stopped = false
let backoff = 1000
let pullScheduled = false

function schedulePull() {
  // 把一串 token 合并成 ≤10 次/秒的取数：token 频率远高于人眼需要的刷新率，
  // 且每次取的都是**整个**盒子（不是增量），不做合并会白刷几十倍请求。
  if (pullScheduled) return
  pullScheduled = true
  setTimeout(() => { pullScheduled = false; pullPayload() }, 100)
}

async function subscribeLoop() {
  while (!stopped) {
    subAc = new AbortController()
    try {
      await streamSSE('/browser/sidebar/stream', {},
        () => schedulePull(),      // token：尽快取一次盒子
        () => pullPayload(),       // done：立刻取最终态
        () => pullPayload(),       // error：错误文案的真相在盒子里 → 立刻拉，不另造一套
        () => { backoff = 1000 },  // meta：连上了 → 重置退避
        null, subAc.signal)
    } catch { /* AbortError / 连不上：都走下面的退避重连 */ }
    if (stopped) break
    await new Promise(r => setTimeout(r, backoff))
    backoff = Math.min(backoff * 2, 15000)
  }
}

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
    // WP18 取源时序：跳转后立即取源会拿到上一页/半加载页（实测知乎 ~35s 才稳定），
    // 必须单独一句 —— 笼统的「读取失败」会让用户去重试一个必然失败的时机。
    page_loading: '页面还在加载中，请等加载完成后重试',
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
  loginHint.value = false
  try {
    const { data } = await browserApi.extract()
    if (!data.ok) {
      fetchMsg.value = extractErrText(data.error)
      loginHint.value = !!data.login_hint
      return
    }
    const pv = data.preview || {}
    cand.value = {
      url: data.url,
      source: data.source,
      // WP19：DOM 兜底（动态页面）时后端给的是 `text`（可见正文），不是 source
      text: data.text || '',
      via: data.via || 'source',
      title: data.title || pv.title || '',
      kindLabel: pv.kind_label || '',
      chars: pv.chars || 0,
      images: pv.images || 0,
      truncated: !!data.truncated,
      canSave: pv.action === 'ready',
      saved: !!pv.saved,
    }
    if (data.via === 'dom') {
      fetchMsg.value = pv.hint || '动态页面：已按页面可见正文读取（不含图片）。'
    } else if (pv.action !== 'ready') {
      fetchMsg.value = pv.hint || '这个页面暂时不能直接入库，可以改用划选正文。'
      loginHint.value = !!data.login_hint
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
    // ⚠️ 两条入库路径：源码路径传 `source`（页面源码，后端先抽取）；
    //    WP19 DOM 兜底传 `text`（已是可见正文，passthrough 落库）—— 两者语义不同，别混。
    const payload = cand.value.text
      ? { url: cand.value.url, text: cand.value.text }
      : { url: cand.value.url, source: cand.value.source }
    const { data } = await clipApi.save(payload)
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
let tabsTimer = null
onMounted(() => {
  pullHost()
  pullPayload()
  pullTabs()
  // ⚠️ 轮询**保留**（不是被流式取代）：投递方（WP16 的注入脚本经后端）与侧栏是
  //    两个**独立文档**，没有从 Python 主动推 JS 的通道（那只在 Qt 宿主下可用）。
  //    1.5s 让用户主观上感觉"即时"，且这条链在浏览器模式下也能完整验证。
  timer = setInterval(pullPayload, 1500)
  // 标签是宿主侧的（浏览器模式下恒为空），变化不快，3s 足够。
  tabsTimer = setInterval(pullTabs, 3000)
  subscribeLoop()
})
onUnmounted(() => {
  timer && clearInterval(timer)
  tabsTimer && clearInterval(tabsTimer)
  // ⚠️⚠️ 必须**同时**置 stopped 与 abort：只置标志会在断网时把这条连接留在后端，
  //     只 abort 则 while 会立刻重连 —— 两者缺一都会泄漏订阅者。
  stopped = true
  if (subAc) { try { subAc.abort() } catch { /* 已自行结束 */ } }
})
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
.sb-login-hint { margin: 6px 0 0; font-size: 12px; line-height: 1.6; color: #b26a00; }
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
/* ---------- WP16：三动作与结果 ---------- */
.sb-acts { display: flex; gap: 6px; margin-top: 14px; }
.sb-act {
  flex: 1; padding: 7px 4px; font-size: 12.5px; cursor: pointer;
  color: var(--asc-text); background: var(--asc-surface-2);
  border: 1px solid var(--asc-border); border-radius: 7px;
}
.sb-act.on { color: #fff; background: var(--asc-primary, #3a6df0); border-color: transparent; }
.sb-act:disabled { opacity: .55; cursor: default; }
.sb-act-msg { margin: 8px 0 0; font-size: 12px; color: var(--asc-text-3); }
.sb-running { margin-top: 12px; font-size: 12.5px; color: var(--asc-text-3); }
.sb-error {
  margin-top: 12px; padding: 10px 12px; font-size: 12.5px; line-height: 1.7;
  color: #c0392b; background: var(--asc-surface-2); border-radius: 8px; word-break: break-word;
}
/* ---------- WP17：正文容器抽成公共类 `.sb-md` ----------
   ⚠️ 流式区与结果区**必须共用同一套排版**：两处各写一遍必然漂移，
      症状是「生成中」到「生成完」跳一次版（字号/行高突变）。 */
.sb-md {
  margin-top: 12px; padding: 10px 12px; font-size: 13px; line-height: 1.8;
  color: var(--asc-text-2); background: var(--asc-surface-2); border-radius: 8px;
  word-break: break-word; overflow-wrap: anywhere;
}
/* 生成中：虚线边框做视觉区分，且**不改变布局尺寸**（否则完成瞬间会跳动） */
.sb-stream { border: 1px dashed var(--asc-border); }
/* ⚠️ `v-html` 注入的节点在**当前作用域之外**，必须 `:deep()` 才能命中
   （scoped 样式默认加不上子节点 —— 症状是"内容出来了但完全没有排版"）。 */
.sb-md :deep(p) { margin: 0 0 8px; }
.sb-md :deep(p:last-child) { margin-bottom: 0; }
.sb-md :deep(ul), .sb-md :deep(ol) { margin: 6px 0 8px; padding-left: 20px; }
.sb-md :deep(li) { margin: 3px 0; }
.sb-md :deep(h1), .sb-md :deep(h2), .sb-md :deep(h3), .sb-md :deep(h4) {
  margin: 12px 0 6px; font-size: 13.5px; color: var(--asc-text);
}
.sb-md :deep(code) { padding: 1px 4px; font-size: 12px; border-radius: 4px; background: var(--asc-bg); }
.sb-md :deep(pre) {
  margin: 8px 0; padding: 8px 10px; border-radius: 6px; background: var(--asc-bg); overflow-x: auto;
}
.sb-md :deep(pre code) { padding: 0; background: none; }
.sb-md :deep(blockquote) {
  margin: 8px 0; padding: 2px 0 2px 10px; color: var(--asc-text-3);
  border-left: 3px solid var(--asc-border);
}
/* ⚠️ 正文图片必须限宽：模型输出的外链大图会撑破 360px 的侧栏、产生横向滚动。 */
.sb-md :deep(img) { max-width: 100%; height: auto; border-radius: 6px; }
.sb-md :deep(a) { color: var(--asc-primary, #3a6df0); }
.sb-md :deep(table) { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.sb-md :deep(th), .sb-md :deep(td) { padding: 4px 6px; border: 1px solid var(--asc-border); }

/* ---------- WP17：复制 / 追问 / 标签栏 ---------- */
.sb-res-bar {
  display: flex; align-items: center; gap: 8px; margin-top: 6px;
  min-height: 22px;   /* 恒定高度：不然「正在输出…」消失的瞬间按钮会跳一行 */
}
.sb-res-msg { margin-right: auto; font-size: 11.5px; color: var(--asc-text-3); }
.sb-mini {
  padding: 3px 10px; font-size: 11.5px; cursor: pointer;
  color: var(--asc-text-2); background: var(--asc-bg);
  border: 1px solid var(--asc-border); border-radius: 6px;
}
.sb-mini:hover { color: var(--asc-text); }
.sb-ask { display: flex; gap: 6px; margin-top: 10px; }
.sb-ask-input {
  flex: 1; min-width: 0; padding: 7px 10px; font-size: 12.5px;
  color: var(--asc-text); background: var(--asc-bg);
  border: 1px solid var(--asc-border); border-radius: 8px;
}
.sb-ask-input:disabled { opacity: .6; }
.sb-ask-send {
  flex-shrink: 0; padding: 7px 12px; font-size: 12.5px; cursor: pointer;
  color: #fff; background: var(--asc-primary, #3a6df0);
  border: none; border-radius: 8px;
}
.sb-ask-send:disabled { opacity: .55; cursor: default; }
.sb-tabs {
  display: flex; gap: 4px; padding: 8px 12px; flex-shrink: 0;
  overflow-x: auto; border-bottom: 1px solid var(--asc-divider);
}
.sb-tab {
  flex-shrink: 0; max-width: 132px; padding: 4px 10px; font-size: 12px; cursor: pointer;
  color: var(--asc-text-2); background: var(--asc-surface-2);
  border: 1px solid var(--asc-border); border-radius: 999px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sb-tab.on { color: #fff; background: var(--asc-primary, #3a6df0); border-color: transparent; }

/* ---------- WP17：深色（**只作用于侧栏作用域**） ----------
   ⚠️ 为什么不在 global.css 的 `:root` 上做：那会改造**全应用**十几个视图，
      而它们大量硬编码浅色（#fff / 颜色字面量）→ 工作量与风险都远超本包。
      侧栏是**独立文档**，在这里覆盖同名令牌（`--asc-*`）即可自动跟随系统。 */
@media (prefers-color-scheme: dark) {
  .browser-sidebar {
    --asc-bg: #1e1e20;
    --asc-card: #26262a;
    --asc-surface-2: #2a2a2e;
    --asc-border: #3a3a40;
    --asc-divider: #323236;
    --asc-text: #e9e9ec;
    --asc-text-2: #b4b4bb;
    --asc-text-3: #8a8a92;   /* 深色底上次要文字下限：约 5.0:1 */
    --asc-primary: #8f7bff;
  }
  /* ⚠️ `.sb-error` 的红色是硬编码的：深色底上对比度不足，必须单独覆盖 */
  .sb-error { color: #ff8a7a; }
  /* WP18 登录墙引导的琥珀色同理（浅色 #b26a00 在深色底上偏暗） */
  .sb-login-hint { color: #f0b35c; }
  /* 主按钮的白字在浅紫底上仍可读，但深色下把紫色提亮后要保证仍是白字 */
  .sb-act.on, .sb-tab.on, .sb-ask-send, .sb-cand-save { color: #16161a; }
}
</style>
