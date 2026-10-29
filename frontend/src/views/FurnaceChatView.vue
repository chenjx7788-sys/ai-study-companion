<template>
  <div class="fc-page">
    <!-- 左：舞台卡（形象常驻陪伴，气质色随丹灵） -->
    <aside class="fc-stage" :style="stageTheme">
      <div class="stage-glow"></div>
      <div class="stage-spirit spirit-breathe" v-html="stageSvg"></div>
      <div class="stage-name">{{ app?.name || '…' }}</div>
      <div class="stage-persona">{{ (app?.persona || '').slice(0, 30) }}…</div>
      <div class="bond-chip">已聊 {{ app?.session_count || 0 }} 场</div>
      <div class="stage-mats">
        <template v-if="(app?.materials || []).length <= 2">
          <span v-for="m in app?.materials || []" :key="m.id" class="mat-chip">{{ m.title }}</span>
        </template>
        <template v-else>
          <span class="mat-chip mat-sum" @click="matsOpen = !matsOpen">
            {{ (app?.materials || []).length }} 份资料 {{ matsOpen ? '▴' : '▾' }}</span>
          <template v-if="matsOpen">
            <span v-for="m in app?.materials || []" :key="m.id" class="mat-chip">{{ m.title }}</span>
          </template>
        </template>
      </div>
      <el-button size="small" @click="$router.push(`/furnace/app/${appId}/edit`)">配置</el-button>
      <el-button size="small" text @click="$router.push('/furnace')">返回丹房</el-button>
    </aside>

    <!-- 中：会话列（按应用隔离） -->
    <aside class="fc-sess">
      <el-button type="primary" class="new-btn" @click="newSession">＋ 新会话</el-button>
      <el-input v-model="sessSearch" placeholder="搜索会话" clearable size="small" class="sess-search" />
      <template v-for="g in groupedSessions" :key="g.label">
        <div class="sess-group-label">{{ g.label }}</div>
        <div v-for="s in g.items" :key="s.id" class="sess-item" :class="{ active: s.id === currentId }" @click="openSession(s.id)">
          <div class="sess-body">
            <el-input v-if="renamingId === s.id" v-model="renameText" size="small"
              @click.stop @keyup.enter="saveRename(s)" @blur="saveRename(s)" />
            <template v-else>
              <div class="sess-title" :title="s.title + '（双击重命名）'" @dblclick.stop="startRename(s)">{{ s.title }}</div>
              <div class="sess-meta">{{ s.message_count }} 问 · {{ relTime(s.updated_at) }}</div>
              <div v-if="s.last_message" class="sess-preview">{{ s.last_message }}</div>
            </template>
          </div>
          <el-popconfirm title="删除该会话及其消息？" width="200" confirm-button-text="删除"
            confirm-button-type="danger" cancel-button-text="取消" @confirm="removeSession(s.id)">
            <template #reference>
              <el-button size="small" text type="danger" class="sess-del" title="删除" @click.stop>×</el-button>
            </template>
          </el-popconfirm>
        </div>
      </template>
      <div v-if="!sessions.length" class="sess-empty">暂无会话</div>
      <div v-else-if="!groupedSessions.length" class="sess-empty">无匹配会话</div>
    </aside>

    <!-- 右：消息流 -->
    <main class="fc-main">
      <div class="fc-msgarea" ref="msgArea">
        <div class="fc-col">
          <!-- 空会话：欢迎消息 + 建议问题（开场白由配置来，不落库） -->
          <div v-if="!messages.length" class="msg ai">
            <div class="avatar" v-html="spiritSVG(app?.spirit || 'book', 30, 'normal')"></div>
            <div class="bubble">
              {{ greetByHour() }}，{{ app?.welcome || '有什么想聊的？' }}
              <div class="q-chips" v-if="(app?.questions || []).length">
                <span v-for="(q, i) in app.questions" :key="i" class="q-chip" @click="ask(q)">{{ q }}</span>
              </div>
            </div>
          </div>

          <div v-for="m in messages" :key="m._tmp || m.id" class="msg" :class="[m.role === 'user' ? 'user' : 'ai', { miss: m.answer_kind === 'kb_miss' }]">
            <div class="avatar" v-if="m.role !== 'user'" v-html="spiritSVG(app?.spirit || 'book', 30, 'normal')"></div>
            <div class="avatar user-av" v-else>我</div>
            <div class="bubble">
              <div v-if="m.role === 'user'" class="u-text">{{ m.content }}</div>
              <div v-else class="md-body" v-html="renderContent(m)"></div>
              <div v-if="m.sources && m.sources.length" class="cites">
                <span v-for="(s, i) in m.sources" :key="i" class="cite" :title="s.snippet || ''"
                  @click="goSource(s)">[{{ i + 1 }}] {{ s.title }}<template v-if="s.page_no"> · P{{ s.page_no }}</template></span>
              </div>
              <div v-if="m.error" class="err-line">{{ m.error }}<span class="retry" @click="retryLast">重试</span></div>
              <div v-if="m.role !== 'user' && m.suggestions && m.suggestions.length && !m._streaming" class="q-chips">
                <span v-for="(q, i) in m.suggestions" :key="i" class="q-chip" @click="ask(q)">{{ q }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="fc-input-area">
        <div class="fc-input">
          <el-input v-model="question" placeholder="问它点什么……" @keydown.enter="onEnterSend" @input="armIdle" :disabled="asking" />
          <el-button v-if="!asking" type="primary" @click="send" :disabled="!question.trim()">发送</el-button>
          <el-button v-else @click="stopAsk">停止</el-button>
        </div>
        <div class="model-toolbar" v-if="models.length">
          <el-select v-model="chatModel" size="small" class="chip-sel" title="本会话临时切换模型（不写回应用配置）">
            <el-option :label="`应用默认（${appModelLabel}）`" value="" />
            <el-option v-for="m in models" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
          </el-select>
          <el-select v-model="effort" size="small" class="chip-sel" title="推理强度">
            <el-option label="快速" value="low" /><el-option label="标准" value="high" />
            <el-option label="深度" value="max" /><el-option label="关闭推理" value="" />
          </el-select>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { appsApi, chatApi, settingsApi } from '../api'
import { createMd, renderMd } from '../utils/md'
import { spiritSVG, greetByHour, spiritTheme } from '../utils/spirit'
import { enterToSend } from '../utils/keys'

const route = useRoute()
const router = useRouter()
const appId = Number(route.params.id)
const app = ref(null)
const sessions = ref([])
const messages = ref([])
const currentId = ref(null)
const question = ref('')
const asking = ref(false)
const sessSearch = ref('')
const renamingId = ref(null)
const renameText = ref('')
const models = ref([])
const chatModel = ref('')          // ''=应用默认（后端解析：app.model_id → 全局）
const effort = ref('low')
const msgArea = ref(null)
const mood = ref('normal')         // normal / think / happy / miss
let abortCtrl = null
let moodTimer = null
const md = createMd()

const stageSvg = computed(() => spiritSVG(app.value?.spirit || 'book', 116, mood.value))
const stageTheme = computed(() => {
  const t = spiritTheme(app.value?.spirit || 'book')
  return { '--app-body': t.body, '--app-edge': t.edge }
})
const matsOpen = ref(false)
const appModelLabel = computed(() => {
  if (!app.value?.model_id) return '跟随全局'
  const m = models.value.find(x => x.id === app.value.model_id)
  return m ? m.name : '跟随全局'
})

// 会话按天分组（沿用主问答视觉：今天 / 昨天 / 更早）
const groupedSessions = computed(() => {
  const q = sessSearch.value.trim()
  const list = q ? sessions.value.filter(s => (s.title || '').includes(q)) : sessions.value
  const now = new Date()
  const today = now.toDateString()
  const yesterday = new Date(now.getTime() - 864e5).toDateString()
  const groups = [['今天', []], ['昨天', []], ['更早', []]]
  for (const s of list) {
    const d = s.updated_at ? new Date(s.updated_at.replace(' ', 'T')) : null
    const ds = d ? d.toDateString() : ''
    if (ds === today) groups[0][1].push(s)
    else if (ds === yesterday) groups[1][1].push(s)
    else groups[2][1].push(s)
  }
  return groups.filter(g => g[1].length).map(([label, items]) => ({ label, items }))
})

function relTime(ts) {
  if (!ts) return ''
  const d = new Date(ts.replace(' ', 'T'))
  const now = new Date()
  if (d.toDateString() === now.toDateString()) return ts.slice(11, 16) || '今天'
  if (d.toDateString() === new Date(now.getTime() - 864e5).toDateString()) return '昨天'
  return ts.slice(5, 10)
}

function renderContent(m) {
  return renderMd(md, m.content || '')
}

function scrollBottom(force) {
  nextTick(() => {
    const el = msgArea.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function setMood(m, backMs = 0) {
  mood.value = m
  clearTimeout(moodTimer)
  if (backMs) moodTimer = setTimeout(() => { mood.value = 'normal' }, backMs)
}

// ---- 闲置主动搭话（P1）：页面可见 + 已有对话 + 空闲 45s + 每会话页内最多 1 次 ----
// 打扰度红线：生成中不搭话、页面隐藏不搭话、输入即重置、空会话不搭话（欢迎块已承担开场）。
// 搭话消息 _idle 标记、不落库——刷新即消失，不污染历史。
// ?idle_ms= 仅供探针/调试覆写倒计时（生产不带此参数，恒 45s）。
const IDLE_MS = Number(new URLSearchParams(location.search).get('idle_ms')) || 45000
let idleTimer = null
const nudgedSessions = new Set()

function clearIdle() { clearTimeout(idleTimer); idleTimer = null }

function armIdle() {
  clearIdle()
  if (!app.value || asking.value) return
  const key = currentId.value || '__new__'
  if (nudgedSessions.has(key)) return
  idleTimer = setTimeout(fireNudge, IDLE_MS)
}

async function fireNudge() {
  idleTimer = null
  if (asking.value || document.hidden || !messages.value.length) return
  const key = currentId.value || '__new__'
  if (nudgedSessions.has(key)) return
  nudgedSessions.add(key)
  try {
    const { data } = await appsApi.nudge(appId, currentId.value)
    if (!data?.text || asking.value || document.hidden) return
    messages.value.push(reactive({
      _tmp: 'n' + Date.now(), role: 'assistant', content: data.text,
      kb_hit: true, answer_kind: '', sources: [],
      suggestions: data.questions || [], _streaming: false, error: '', _idle: true,
    }))
    setMood('happy', 2600)
    scrollBottom(true)
  } catch { /* 静默：搭话是锦上添花，失败当没发生 */ }
}

async function loadApp() {
  const { data } = await appsApi.get(appId)
  app.value = data
}
async function loadSessions() {
  const { data } = await chatApi.sessions(appId)
  sessions.value = data
}
async function openSession(id) {
  if (id === currentId.value) return
  stopAsk()
  currentId.value = id
  const { data } = await chatApi.messages(id)
  messages.value = data.map(m => ({ ...m, _streaming: false }))
  scrollBottom(true)
  armIdle()
}
function newSession() {
  stopAsk()
  currentId.value = null      // 不落库：首个提问才由后端建会话（沿用主问答契约）
  messages.value = []
  clearIdle()
}

function startRename(s) { renamingId.value = s.id; renameText.value = s.title }
async function saveRename(s) {
  const title = renameText.value.trim()
  renamingId.value = null
  if (!title || title === s.title) return
  await chatApi.renameSession(s.id, title)
  s.title = title
  ElMessage.success('已重命名')
}
async function removeSession(id) {
  await chatApi.removeSession(id)
  sessions.value = sessions.value.filter(x => x.id !== id)
  if (currentId.value === id) newSession()
  loadApp()   // 默契度（已聊 N 场）联动
}

function ask(q) { question.value = q; send() }

const onEnterSend = enterToSend(send)

async function send() {
  const q = question.value.trim()
  if (!q || asking.value) return
  clearIdle()                  // 开口即取消搭话倒计时
  question.value = ''
  asking.value = true
  setMood('think')

  // 本问是否会新建会话：中止路径也要据此回读（原来声明在 try 内部，catch 里拿不到）
  const wasNewSession = !currentId.value
  const userMsg = { _tmp: 'u' + Date.now(), role: 'user', content: q }
  messages.value.push(userMsg)
  const aiMsg = reactive({ _tmp: 'a' + Date.now(), role: 'assistant', content: '', kb_hit: true, answer_kind: '', sources: [], suggestions: [], _streaming: true, error: '' })
  messages.value.push(aiMsg)
  scrollBottom(true)

  try {
    abortCtrl = new AbortController()
    const resp = await fetch('/api/chat/ask/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: abortCtrl.signal,
      body: JSON.stringify({
        question: q,
        session_id: currentId.value,
        app_id: appId,
        mode: 'kb',
        model_id: chatModel.value || null,
        reasoning_effort: effort.value || null,
      }),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.detail || `请求失败 ${resp.status}`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop()
      for (const evt of events) {
        const lines = evt.split('\n')
        const ev = lines.find(l => l.startsWith('event:'))?.slice(6).trim()
        const dataLine = lines.find(l => l.startsWith('data:'))?.slice(5)
        if (!ev || !dataLine) continue
        const payload = JSON.parse(dataLine)
        if (ev === 'sources') {
          currentId.value = payload.session_id
          userMsg.id = payload.user_message_id
          aiMsg.kb_hit = payload.kb_hit
          aiMsg.answer_kind = payload.answer_kind || ''
          aiMsg.sources = payload.sources || []
        } else if (ev === 'token') {
          aiMsg.content += payload.t
          scrollBottom()
        } else if (ev === 'done') {
          aiMsg.id = payload.message_id
          aiMsg._streaming = false
          // 情绪闭环：kb_miss → 挠头；命中 → 开心（2.6s 回常态）
          setMood(aiMsg.answer_kind === 'kb_miss' ? 'miss' : 'happy', 2600)
        } else if (ev === 'suggestions') {
          aiMsg.suggestions = payload.questions || []
          scrollBottom()
        } else if (ev === 'error') {
          aiMsg.error = payload.message || '生成失败'
          if (payload.message_id) aiMsg.id = payload.message_id
          aiMsg._streaming = false
          setMood('normal')
        }
      }
    }
    loadSessions()
    if (wasNewSession) loadApp()   // 新会话落库后刷新默契度
  } catch (e) {
    if (e && e.name === 'AbortError') {
      // 用户主动停止：后端已落半截回答 → 必须回读会话列与默契度。
      // ⚠️ try 尾部的 loadSessions/loadApp 在异常路径到不了（2026-10-29 修）：
      //    表现为点「停止」后侧栏少一条刚建的会话、「已聊 N 场」停在旧值。
      loadSessions()
      if (wasNewSession) loadApp()
    }
    else {
      aiMsg.error = (e && e.message) || '请求失败'
      aiMsg._streaming = false
    }
    setMood('normal')
  } finally {
    asking.value = false
    abortCtrl = null
    armIdle()                  // 一轮结束后重新进入闲置观察
  }
}

function stopAsk() { abortCtrl && abortCtrl.abort() }
async function retryLast() {
  // 应用侧重试：重发最后一个用户问题（旧错误回答留在历史上，与主问答「重试」同口径前的简化）
  const lastUser = [...messages.value].reverse().find(m => m.role === 'user')
  if (lastUser) ask(lastUser.content)
}

function goSource(s) {
  // ⚠️ 站内链接一律走 router（2026-10-29）：window.open 在桌面外壳里会跳出应用，
  //    与 md.js「只有外链才 target=_blank」的既有约定相反。
  if (!s.material_id) return
  router.push(`/study/${s.material_id}${s.page_no ? `?page=${s.page_no}` : ''}`)
}

onMounted(async () => {
  try {
    await loadApp()
    await loadSessions()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '应用加载失败')
  }
  try {
    const { data } = await settingsApi.get()
    models.value = data.llm_models || []
  } catch (e) { /* 模型清单拉取失败 → 隐藏工具行 */ }
})
onUnmounted(() => { stopAsk(); clearTimeout(moodTimer); clearIdle() })
</script>

<style scoped>
.fc-page { display: grid; grid-template-columns: 260px 230px 1fr; height: 100%; min-height: 0; }
/* 舞台卡 */
.fc-stage { position: relative; overflow-y: auto; text-align: center; padding: 30px 16px; border-right: 1px solid var(--asc-border);
  background: linear-gradient(180deg, color-mix(in srgb, var(--app-body) 52%, #fff) 0%, #fff 46%); }
.stage-glow { width: 170px; height: 170px; border-radius: 50%; margin: 0 auto -140px;
  background: radial-gradient(circle, var(--app-body) 0%, transparent 68%); opacity: .8; }
.stage-spirit { height: 128px; display: flex; align-items: flex-end; justify-content: center; }
.stage-name { font-size: 16px; font-weight: 600; margin-top: 10px; }
.stage-persona { font-size: 12px; color: var(--asc-text-2); margin: 6px 0 10px; line-height: 1.6; }
.bond-chip { display: inline-block; background: var(--asc-primary-soft); color: var(--asc-primary); font-size: 12px; border-radius: 999px; padding: 3px 12px; margin-bottom: 12px; }
.stage-mats { margin-bottom: 14px; }
.mat-sum { cursor: pointer; color: var(--app-edge, var(--asc-primary)); font-weight: 500; }
.mat-sum:hover { background: var(--app-body, var(--asc-surface-2)); }
.mat-chip { display: inline-block; background: var(--asc-surface-2); border-radius: 6px; padding: 3px 10px; font-size: 12px; color: var(--asc-text-2); margin: 0 4px 6px; }
.spirit-breathe :deep(svg) { animation: breathe 3.2s ease-in-out infinite; transform-origin: 50% 88%; }
@keyframes breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.035); } }
/* 会话列 */
.fc-sess { background: #edeef2; padding: 18px 12px; overflow-y: auto; }
.new-btn { width: 100%; margin-bottom: 12px; border-radius: 12px; height: 40px; font-weight: 500; }
.sess-search { margin-bottom: 6px; }
.sess-group-label { font-size: 11px; color: #666; padding: 14px 8px 5px; letter-spacing: .5px; font-weight: 500; }
.sess-item { position: relative; display: flex; align-items: flex-start; gap: 6px; padding: 9px 12px; border-radius: 10px; cursor: pointer; color: var(--asc-text-2); transition: background .16s ease; margin-bottom: 2px; }
.sess-item:hover { background: var(--asc-card); }
.sess-item.active { background: rgba(124, 92, 252, .12); }
.sess-item.active .sess-title { color: var(--asc-primary); font-weight: 600; }
.sess-body { flex: 1; min-width: 0; }
.sess-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; color: var(--asc-text); font-weight: 500; line-height: 1.4; }
.sess-meta { font-size: 11px; color: #666; margin-top: 2px; }
.sess-preview { font-size: 11.5px; color: #666; margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-del { visibility: hidden; flex-shrink: 0; }
.sess-item:hover .sess-del { visibility: visible; }
.sess-empty { font-size: 12px; color: #666; text-align: center; padding: 28px 0; }
/* 消息流 */
.fc-main { display: flex; flex-direction: column; min-width: 0; background: var(--asc-bg); }
.fc-msgarea { flex: 1; overflow-y: auto; padding: 26px 30px 18px; }
.fc-col { max-width: 760px; margin: 0 auto; }
.msg { display: flex; gap: 10px; margin: 14px 0; animation: msgIn .3s ease; }
.msg.user { flex-direction: row-reverse; }
@keyframes msgIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: var(--asc-primary-soft); display: flex; align-items: center; justify-content: center; font-size: 13px; flex-shrink: 0; overflow: hidden; }
.user-av { background: var(--asc-primary); color: #fff; font-size: 12px; }
.bubble { max-width: 78%; padding: 10px 14px; border-radius: 12px; line-height: 1.8; font-size: 14px; }
.msg.user .bubble { background: var(--asc-primary); color: #fff; border-top-right-radius: 4px; }
.msg.ai .bubble { background: var(--asc-card); border: 1px solid var(--asc-border); border-top-left-radius: 4px; }
.msg.ai.miss .bubble { background: #fdf6ec; border-color: #faecd8; }
.u-text { white-space: pre-wrap; }
.cites { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.cite { display: inline-flex; align-items: center; font-size: 12px; color: var(--asc-primary); background: var(--asc-primary-soft); border-radius: 4px; padding: 1px 7px; cursor: pointer; }
.cite:hover { background: rgba(124, 92, 252, .2); }
.err-line { margin-top: 8px; font-size: 12px; color: #b91c1c; }
.retry { margin-left: 8px; color: var(--asc-primary); cursor: pointer; }
.q-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.q-chip { border: 1px solid var(--asc-border); background: var(--asc-surface-2); border-radius: 999px; padding: 5px 12px; font-size: 12.5px; color: var(--asc-text-2); cursor: pointer; transition: all .15s ease; }
.q-chip:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
/* 输入区 */
.fc-input-area { padding: 10px 30px 16px; }
.fc-input { max-width: 760px; margin: 0 auto; display: flex; gap: 10px; background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 12px; padding: 8px 10px; align-items: center; }
.fc-input:focus-within { box-shadow: 0 0 0 1.5px var(--asc-primary) inset; }
.fc-input :deep(.el-input__wrapper) { box-shadow: none !important; background: transparent !important; }
.model-toolbar { max-width: 760px; margin: 6px auto 0; display: flex; gap: 4px; }
.chip-sel { width: 200px; }
@media (max-width: 1100px) { .fc-page { grid-template-columns: 1fr; } .fc-stage, .fc-sess { display: none; } }
@media (prefers-reduced-motion: reduce) { .spirit-breathe :deep(svg), .msg { animation: none; } }
</style>
