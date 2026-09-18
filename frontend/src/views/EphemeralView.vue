<template>
  <div class="page read-page" @mouseup="onMouseUp" @keydown.esc="closeToolbar">
    <!-- 页头 -->
    <div class="rp-head">
      <el-button text :icon="ArrowLeft" @click="goBack">返回</el-button>
      <div class="rp-title-wrap">
        <h2 class="rp-title" :title="doc.title">{{ doc.title || '临时阅读' }}</h2>
        <div class="rp-meta">
          <span v-if="doc.kind_label" class="kind-chip" :class="'k-' + (doc.kind || 'url')">{{ doc.kind_label }}</span>
          <!-- 图片消息：正文是图、文字天然少，标出来用户才不会以为抓漏了 -->
          <span v-if="doc.wx_image_post" class="rp-imgs">图片消息</span>
          <!-- 视频笔记：正文只有配文 + 封面，**视频本体没抓** —— 不说清用户必当成抓漏 -->
          <span v-if="doc.xhs_video_note" class="rp-warn">{{ XHS_VIDEO_TIP }}</span>
          <span v-if="doc.chars">{{ doc.chars }} 字 · {{ doc.chunk_count }} 段</span>
          <span v-if="doc.images" class="rp-imgs">{{ doc.images }} 张图</span>
          <span v-if="doc.truncated" class="rp-warn">正文超长，已截断</span>
          <span class="rp-ephemeral">仅本次阅读 · 不写磁盘、不进知识库</span>
        </div>
      </div>
      <div class="rp-ops">
        <el-button :icon="MagicStick" :loading="summary.loading" @click="runSummary">AI 摘要</el-button>
        <el-button :type="doc.saved ? 'default' : 'primary'" :loading="saving" @click="saveToKb">
          <el-icon v-if="doc.saved" class="el-icon--left"><CircleCheck /></el-icon>
          {{ doc.saved ? '已在知识库' : '加入知识库' }}
        </el-button>
        <el-button :icon="Clock" @click="openRecent">
          最近阅读<template v-if="recent.length">（{{ recent.length }}）</template>
        </el-button>
      </div>
    </div>

    <!-- 空/失败态 -->
    <div v-if="loadError" class="rp-empty">
      <el-alert type="warning" :closable="false" show-icon :title="loadError" />
      <div class="rp-empty-ops">
        <el-button @click="openRecent">看看最近阅读</el-button>
        <el-button type="primary" @click="goBack">返回材料库</el-button>
      </div>
    </div>

    <div v-else-if="loading" class="rp-loading" v-loading="true" element-loading-text="正在打开…" />

    <div v-else class="rp-body">
      <!-- 正文 -->
      <div class="rp-content" ref="contentEl">
        <div v-if="doc.url" class="rp-source">
          <el-icon><Link /></el-icon>
          <!-- 来源可点：仅 http(s) 渲染成 <a>（挡 javascript: 伪协议）；新窗口由浏览器/pywebview 处理 -->
          <a v-if="sourceHref" class="rp-source-url" :href="sourceHref" :title="sourceHref"
            target="_blank" rel="noopener noreferrer">{{ doc.url }}</a>
          <span v-else class="rp-source-url" :title="doc.url">{{ doc.url }}</span>
        </div>
        <div v-for="c in doc.chunks" :key="c.page_no" class="ep-block" :data-page="c.page_no"
          :id="'ep-p-' + c.page_no" v-html="renderChunk(c)"></div>
        <div v-if="!doc.chunks.length" class="rp-nobody">这篇没有可显示的正文。</div>
      </div>

      <!-- 侧栏：AI 与划线 -->
      <div class="rp-side" :class="{ collapsed: sideCollapsed }">
        <div class="side-head">
          <div class="side-tabs">
            <span class="side-tab" :class="{ on: tab === 'ai' }" @click="tab = 'ai'">AI 辅助</span>
            <span class="side-tab" :class="{ on: tab === 'marks' }" @click="tab = 'marks'">
              划线（{{ explains.length }}）
            </span>
          </div>
          <el-button text size="small" @click="sideCollapsed = !sideCollapsed">
            {{ sideCollapsed ? '展开' : '收起' }}
          </el-button>
        </div>

        <div v-show="!sideCollapsed" class="side-body">
          <!-- ── AI 摘要 ── -->
          <template v-if="tab === 'ai'">
            <div class="sec">
              <div class="sec-head">
                <span class="sec-title">全文摘要</span>
                <el-button v-if="summary.text && !summary.loading" text size="small"
                  :loading="noteBusy.summary" @click="noteFromSummary">转笔记</el-button>
              </div>
              <div v-if="summary.progress" class="sec-progress">
                正在分组处理 {{ summary.progress.done }} / {{ summary.progress.total }}
              </div>
              <!-- 诚实提示：AI 只能读文字，图片里的内容（截图/表格/流程图）它看不到。
                   图片型文章的用户最容易把"摘要很薄"误判成"功能坏了"，所以要说明。 -->
              <div v-if="doc.images" class="sec-note">
                正文含 {{ doc.images }} 张图片，AI 摘要只基于文字部分。
              </div>
              <div v-if="summary.text" class="md-preview sec-md" v-html="md.render(summary.text)"></div>
              <div v-else-if="!summary.loading" class="sec-empty">
                长文会先分组提炼再汇总，请稍候。点上方「AI 摘要」开始。
              </div>
              <div v-if="summary.loading && !summary.text" class="sec-loading">正在生成…</div>
            </div>

            <div class="sec">
              <div class="sec-head"><span class="sec-title">向这篇文章提问</span></div>
              <el-input v-model="ask.q" type="textarea" :rows="2" resize="none"
                placeholder="例如：作者的结论建立在哪些前提上？" @keydown.enter.exact.prevent="runAsk" />
              <div class="sec-ops">
                <el-button size="small" type="primary" :loading="ask.loading"
                  :disabled="!ask.q.trim()" @click="runAsk">提问</el-button>
              </div>
              <div v-for="(a, i) in ask.items" :key="i" class="ask-item">
                <div class="ask-q">{{ a.q }}</div>
                <div class="md-preview sec-md" v-html="md.render(a.a)"></div>
                <div class="ask-ops">
                  <el-button text size="small" :loading="noteBusy['ask' + i]"
                    @click="noteFromAsk(a, i)">转笔记</el-button>
                </div>
              </div>
            </div>
          </template>

          <!-- ── 划线解读 ── -->
          <template v-else>
            <div v-if="!explains.length" class="sec-empty">
              在左侧正文里选中一段文字，点浮出的「AI 解读」即可。划线与解读只存在于本次阅读，
              加入知识库后可转成笔记长期保留。
            </div>
            <div v-for="(ch, ci) in explains" :key="ch.id" class="chain">
              <div class="chain-sel" :title="ch.selected" @click="flashBlock(ch.page_no)">
                「{{ ch.selected.length > 42 ? ch.selected.slice(0, 42) + '…' : ch.selected }}」
                <span class="chain-page">P{{ ch.page_no }}</span>
              </div>
              <div v-for="(it, ii) in ch.items" :key="ii" class="chain-turn">
                <div v-if="it.q" class="chain-q">问：{{ it.q }}</div>
                <div class="md-preview sec-md" v-html="md.render(it.a || '…')"></div>
                <div v-if="!it.q" class="chain-ops">
                  <el-button text size="small" :loading="noteBusy['ex' + ci]"
                    @click="noteFromExplain(ch, ci)">转笔记</el-button>
                </div>
              </div>
              <div class="chain-ask">
                <el-input v-model="ch.q" size="small" placeholder="继续追问这段…"
                  @keydown.enter.exact.prevent="doAsk(ch, ci)" />
                <el-button size="small" :loading="ch.asking" :disabled="!(ch.q || '').trim()"
                  @click="doAsk(ch, ci)">追问</el-button>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 选中文字浮出的工具条 -->
    <div v-if="toolbar.show" class="sel-toolbar" :style="{ left: toolbar.x + 'px', top: toolbar.y + 'px' }">
      <el-button size="small" type="primary" :loading="toolbar.busy" @click="startExplain">AI 解读</el-button>
      <el-button size="small" @click="askFromSelection">用它提问</el-button>
    </div>

    <!-- 最近阅读抽屉 -->
    <el-drawer v-model="recentOpen" title="最近阅读" size="440px" direction="rtl">
      <div class="recent-note">本次使用期间打开的网页，关闭应用后清空（不写磁盘、不进知识库）。</div>
      <div v-for="it in recent" :key="it.key" class="recent-item">
        <div class="recent-main">
          <div class="recent-t" :title="it.title">
            {{ it.title }}
            <span v-if="it.saved" class="badge-saved">已在知识库</span>
          </div>
          <div class="recent-m">
            <span class="kind-chip" :class="'k-' + (it.kind || 'url')">{{ it.kind_label }}</span>
            <span>{{ it.chars }} 字</span>
            <!-- 图片张数：图片型文章"字数少"是正常的，列表里给出图数才不会让人误判成抓漏了 -->
            <span v-if="it.images" class="rp-imgs">{{ it.images }} 张图</span>
            <!-- 与单篇同口径（wx_image_post_of / xhs_video_note_of）：
                 列表以前完全不发这两个字段，导致抽屉与详情页说法不一致 -->
            <span v-if="it.wx_image_post" class="rp-imgs">图片消息</span>
            <span v-if="it.xhs_video_note" class="rp-warn">{{ XHS_VIDEO_TIP }}</span>
            <span>{{ fmtTime(it.updated_at) }}</span>
          </div>
        </div>
        <div class="recent-ops">
          <el-button size="small" text @click="openOne(it)">打开</el-button>
          <el-popconfirm title="从本次阅读列表移除？（不影响磁盘与知识库）" confirm-button-text="移除"
            cancel-button-text="取消" @confirm="removeRecent(it)">
            <template #reference><el-button size="small" text>移除</el-button></template>
          </el-popconfirm>
        </div>
      </div>
      <el-empty v-if="!recent.length" description="本次还没有打开过网页" :image-size="70" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Clock, CircleCheck, Link, MagicStick } from '@element-plus/icons-vue'
import { createMd } from '../utils/md'
import { clipApi, ephemeralApi, noteApi } from '../api'
import { streamSSE } from '../utils/sse'
import { useReadingStore } from '../stores/reading'

const route = useRoute()
const router = useRouter()
const reading = useReadingStore()

const md = createMd({ linkify: true, breaks: false })
const contentEl = ref(null)

// 「视频笔记」提示文案。⚠️ 与 components/CandidateList.vue 里那处必须**逐字一致**
// （两处各写一份是为了不给一条文案新建模块；一致性由验收脚本断言兜住）
const XHS_VIDEO_TIP = '视频笔记 · 仅抓配文与封面'


const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const sideCollapsed = ref(false)
const tab = ref('ai')

const doc = reactive({
  // `url` = 归一化地址（展示 / 溯源用，不含凭证）；`input_url` = 用户原始输入（**再抓取用**）。
  // ⚠️ 小红书只有后者能打开 —— 详情页「加入知识库」必须用它，否则必现「页面不见了」。
  key: '', url: '', input_url: '', title: '', text: '', chunks: [], kind: 'url', kind_label: '',
  chars: 0, chunk_count: 0, images: 0, wx_image_post: false, xhs_video_note: false,
  truncated: false, saved: false, material_id: null
})

// 来源行可点的版本：只放行 http(s)（`doc.url` 可能来自路由 query，不能直接当 href）
const sourceHref = computed(() => (/^https?:\/\//i.test(doc.url || '') ? doc.url : ''))

const summary = reactive({ text: '', loading: false, progress: null })
const ask = reactive({ q: '', items: [], loading: false })
const explains = ref([])
const noteBusy = reactive({})
const recent = ref([])
const recentOpen = ref(false)

const toolbar = reactive({ show: false, x: 0, y: 0, text: '', page: 1, busy: false })

// ───────────────────────── 打开 ─────────────────────────

function renderChunk(c) { return md.render(c.content || '') }

function applyDoc(d) {
  Object.assign(doc, {
    key: d.key || '', url: d.url || '', input_url: d.input_url || d.url || '',
    title: d.title || '未命名网页',
    text: d.text || '', chunks: d.chunks || [], kind: d.kind || 'url',
    kind_label: d.kind_label || '',
    chars: d.chars ?? (d.text || '').length,
    chunk_count: d.chunk_count ?? (d.chunks || []).length,
    images: d.images ?? 0,
    wx_image_post: !!d.wx_image_post,
    xhs_video_note: !!d.xhs_video_note,
    truncated: !!d.truncated, saved: !!d.saved, material_id: d.material_id ?? null
  })
}

async function bootstrap() {
  loading.value = true
  loadError.value = ''
  try {
    // ① 从候选列表交接过来（已在 open 时抓好，零二次请求）
    const hand = reading.takeHandoff()
    if (hand) { applyDoc(hand); return }
    // ② 带 key 打开（最近阅读点击）
    if (route.query.key) {
      const { data } = await ephemeralApi.recentOne(route.query.key)
      applyDoc(data)
      return
    }
    // ③ 带 url 直接打开（外部链接分享/刷新）
    if (route.query.url) {
      const { data } = await ephemeralApi.open({
        url: route.query.url, title: route.query.title || undefined
      })
      if (!data.key && !data.text) {
        loadError.value = data.hint || '这个链接暂时读不了，可以回材料库用「粘贴网页链接」手动粘贴正文。'
        return
      }
      applyDoc(data)
      return
    }
    loadError.value = '没有指定要阅读的内容。'
  } catch (e) {
    loadError.value = errText(e)
  } finally {
    loading.value = false
  }
}

function goBack() { router.push('/') }

// ───────────────────────── 划线 / 工具条 ─────────────────────────

function closestBlock(node) {
  let el = node?.nodeType === 3 ? node.parentElement : node
  while (el && el !== contentEl.value) {
    if (el.classList && el.classList.contains('ep-block')) return el
    el = el.parentElement
  }
  return null
}

function onMouseUp() {
  const sel = window.getSelection()
  const text = (sel?.toString() || '').trim()
  if (!text || text.length < 2 || !sel.rangeCount) { toolbar.show = false; return }
  const range = sel.getRangeAt(0)
  const block = closestBlock(range.startContainer)
  if (!block) { toolbar.show = false; return }
  const rect = range.getBoundingClientRect()
  toolbar.text = text
  toolbar.page = Number(block.dataset.page) || 1
  toolbar.x = Math.max(12, Math.min(rect.left + rect.width / 2 - 90, window.innerWidth - 200))
  toolbar.y = Math.max(12, rect.top - 44)
  toolbar.show = true
}

function closeToolbar() { toolbar.show = false }
function flashBlock(page) {
  const el = document.getElementById('ep-p-' + page)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('flash')
  setTimeout(() => el.classList.remove('flash'), 1200)
}

// ───────────────────────── AI：摘要 ─────────────────────────

function runSummary() {
  if (summary.loading) return
  summary.loading = true
  summary.text = ''
  summary.progress = null
  streamSSE(
    ephemeralApi.summaryStreamUrl,
    refPayload({ instruction: undefined }),
    (t) => { summary.text += t },
    () => { summary.loading = false; summary.progress = null },
    (msg) => { summary.loading = false; summary.progress = null; ElMessage.error(msg || '摘要失败') },
    null,
    (p) => { summary.progress = p }
  ).catch((e) => { summary.loading = false; ElMessage.error(errText(e)) })
}

// ───────────────────────── AI：提问 ─────────────────────────

function runAsk() {
  const q = ask.q.trim()
  if (!q || ask.loading) return
  ask.loading = true
  const item = reactive({ q, a: '' })
  ask.items.push(item)
  ask.q = ''
  streamSSE(
    ephemeralApi.askStreamUrl,
    refPayload({ question: q }),
    (t) => { item.a += t },
    () => { ask.loading = false },
    (msg) => { ask.loading = false; ElMessage.error(msg || '回答失败') }
  ).catch((e) => { ask.loading = false; ElMessage.error(errText(e)) })
}

function askFromSelection() {
  ask.q = `请结合上下文解释：「${toolbar.text.slice(0, 80)}」`
  toolbar.show = false
  tab.value = 'ai'
}

// ───────────────────────── AI：解读 / 追问 ─────────────────────────

function startExplain() {
  if (toolbar.busy) return
  const selected = toolbar.text
  const page = toolbar.page
  toolbar.busy = true
  toolbar.show = false
  tab.value = 'marks'
  const chain = reactive({ id: Date.now() + Math.random(), selected, page_no: page, items: [], q: '', asking: false })
  explains.value.push(chain)
  ephemeralApi.explain({ ...refPayload({}), selected_text: selected, anchor: { page_no: page } })
    .then(({ data }) => { chain.items.push({ q: '', a: data.content || '' }) })
    .catch((e) => {
      chain.items.push({ q: '', a: `解读失败：${errText(e)}` })
    })
    .finally(() => { toolbar.busy = false })
}

function doAsk(chain, ci) {
  const q = (chain.q || '').trim()
  if (!q || chain.asking) return
  chain.asking = true
  const turn = reactive({ q, a: '' })
  chain.items.push(turn)
  chain.q = ''
  const history = []
  for (const it of chain.items) {
    if (it === turn) break
    if (it.q) history.push({ role: 'user', content: it.q })
    if (it.a) history.push({ role: 'assistant', content: it.a })
  }
  ephemeralApi.explain({
    ...refPayload({}),
    selected_text: chain.selected,
    anchor: { page_no: chain.page_no },
    question: q,
    history
  })
    .then(({ data }) => { turn.a = data.content || '' })
    .catch((e) => { turn.a = `追问失败：${errText(e)}` })
    .finally(() => { chain.asking = false })
}

// ───────────────────────── 沉淀：入库 / 转笔记 ─────────────────────────

async function saveToKb() {
  if (doc.saved) {
    ElMessage.info('这篇已在知识库中，可直接打开学习页')
    return true
  }
  saving.value = true
  try {
    // ⚠️ 必须用 `doc.input_url`：`doc.url` 是归一化地址，小红书会被剥掉 xsec_token，
    //    拿它去抓必然是「你访问的页面不见了」（HTTP 200 的假成功页，极难发现）。
    const { data } = await clipApi.save({
      url: doc.input_url || doc.url, text: doc.text || undefined, title: doc.title || undefined
    })
    doc.saved = true
    doc.material_id = data.id
    ElMessage.success(data.duplicated ? '已在知识库中' : '已加入知识库，正在建立索引')
    return true
  } catch (e) {
    ElMessage.error(errText(e))
    return false
  } finally {
    saving.value = false
  }
}

/**
 * 转笔记。
 *
 * ⚠️ 两条关键约定：
 * 1. **必须先入库**：`POST /notes` 要求 material_id 且校验材料存在（挂材料的笔记链路）。
 *    方案 §5.4 写的是 `/notes/from-ai`，但那个接口硬编码 `material_id=None`（只服务周报/
 *    播客脚本这类材料无关产物）—— 用它会让笔记脱离材料，学习页里看不到。这里改用
 *    `POST /notes`，并在需要时先落库（确认框里明说会同时加入知识库）。
 * 2. **anchor.kind 必须避开 `summary`**：学习页用 `anchor.kind === 'summary'` 认领
 *    "这条材料的摘要笔记"并做版本比对；临时阅读的摘要若也叫 summary，会被它认领 →
 *    因缺 version 而误显示「摘要已更新」。故统一用 `clip_*` 前缀。
 */
async function transferToNote({ title, content, kind, anchor }) {
  if (!content || !content.trim()) { ElMessage.warning('内容为空，无法转笔记'); return }
  if (!doc.saved || !doc.material_id) {
    try {
      await ElMessageBox.confirm(
        '转笔记需要先把这篇文章加入知识库（会建立索引，之后可在学习页继续阅读）。是否继续？',
        '转为笔记', { confirmButtonText: '加入并转笔记', cancelButtonText: '取消', type: 'info' }
      )
    } catch { return }
    const ok = await saveToKb()
    if (!ok) return
  }
  try {
    await noteApi.create({
      material_id: doc.material_id,
      title: (title || '').slice(0, 100) || '未命名笔记',
      content: content.trim(),
      source_type: 'ai_asset',
      anchor: { ...anchor, kind, url: doc.url, origin: doc.kind }
    })
    ElMessage.success('已转为笔记，可在学习页与知识库查看')
  } catch (e) {
    ElMessage.error(errText(e))
  }
}

async function noteFromSummary() {
  noteBusy.summary = true
  try {
    await transferToNote({
      title: `${doc.title}｜摘要`,
      content: summary.text,
      kind: 'clip_summary',
      anchor: { title: doc.title }
    })
  } finally { noteBusy.summary = false }
}

function noteFromExplain(chain, ci) {
  const first = chain.items.find(i => !i.q)
  if (!first) return
  noteBusy['ex' + ci] = true
  transferToNote({
    title: chain.selected.slice(0, 40),
    content: `> ${chain.selected}\n\n${first.a}`,
    kind: 'clip_explain',
    anchor: { page_no: chain.page_no, selected_text: chain.selected }
  }).finally(() => { noteBusy['ex' + ci] = false })
}

function noteFromAsk(a, i) {
  noteBusy['ask' + i] = true
  transferToNote({
    title: `提问：${a.q.slice(0, 40)}`,
    content: `**问**：${a.q}\n\n**答**：\n\n${a.a}`,
    kind: 'clip_ask',
    anchor: { question: a.q }
  }).finally(() => { noteBusy['ask' + i] = false })
}

// ───────────────────────── 最近阅读 ─────────────────────────

async function loadRecent() {
  try {
    const { data } = await ephemeralApi.recent()
    recent.value = data.items || []
  } catch { /* 列表失败不打扰阅读 */ }
}

function openRecent() {
  recentOpen.value = true
  loadRecent()
}

async function openOne(it) {
  recentOpen.value = false
  try {
    const { data } = await ephemeralApi.recentOne(it.key)
    applyDoc(data)
    resetSide()
  } catch (e) { ElMessage.error(errText(e)) }
}

async function removeRecent(it) {
  try {
    await ephemeralApi.removeRecent(it.key)
    recent.value = recent.value.filter(x => x.key !== it.key)
  } catch (e) { ElMessage.error(errText(e)) }
}

function resetSide() {
  summary.text = ''
  summary.progress = null
  ask.items = []
  ask.q = ''
  explains.value = []
  tab.value = 'ai'
  window.scrollTo({ top: 0 })
}

// ───────────────────────── 工具 ─────────────────────────

/** 定位参数：key 优先、text 兜底（快照被 LRU 淘汰或重启后仍可用，见方案 §5.2）
 *
 * ⚠️ N13：**有 key 就不下发 text**。正文动辄几万字（实测 6.8 万字 ≈ 200KB 请求体），
 *    而后端 `_resolve` 在 key 命中时**根本不看 text** —— 每次摘要 / 提问 / 解读
 *    都白传一遍全文（`ASK_CONTEXT_MAX=8000` 只截前 8000 字）。
 *    代价（已知、接受）：key 失效时（应用重启 / LRU 淘汰）后端会回退到 text，
 *    而这里不再带 text → 退化成诚实的 404「请重新打开该网页」。
 *    该场景实际不可达：`doc.key` 的三个入口（交接包 / `/recent/{key}` 命中 /
 *    带 url 重开）在**打开那一刻**都验证过 key 有效，且阅读页内没有任何操作会往
 *    快照 put（不新增 → 不触发 LRU 淘汰）。
 *    ⚠️ key 为空时才用 text —— 「现场分块」那条通道（`_resolve` 的另一半）必须留着。
 */
function refPayload(extra) {
  return {
    key: doc.key || undefined,
    text: doc.key ? undefined : (doc.text || undefined),
    title: doc.title || undefined,
    ...extra
  }
}

function fmtTime(s) {
  if (!s) return ''
  const d = new Date(s)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function errText(e) {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.hint || d.reason || '操作失败'
  return e?.message || '操作失败'
}

onMounted(async () => {
  await bootstrap()
  loadRecent()
  document.addEventListener('mousedown', onDocDown)
})
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown))

function onDocDown(e) {
  if (toolbar.show && !e.target.closest?.('.sel-toolbar')) toolbar.show = false
}

const CHUNK_HINT = computed(() => doc.chunk_count || 0)
</script>

<style scoped>
.read-page { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

.rp-head { display: flex; align-items: flex-start; gap: 12px; padding-bottom: 14px; flex-shrink: 0; }
.rp-title-wrap { flex: 1; min-width: 0; }
.rp-title {
  font-size: 18px; font-weight: 600; margin: 0 0 4px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rp-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 12px; color: var(--asc-text-2); }
.rp-ephemeral { color: #8a5a00; background: #fdf6e3; border-radius: 4px; padding: 1px 6px; }
.rp-warn { color: #8a5a00; }
.rp-imgs { color: #6b5bd2; }
.sec-note {
  font-size: 12px; line-height: 1.6; color: #8a5a00;
  background: #fdf6e3; border-radius: 6px; padding: 6px 9px; margin-bottom: 8px;
}
.rp-ops { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.kind-chip { font-size: 11px; font-weight: 600; border-radius: 4px; padding: 1px 6px; }
.k-url { color: #2563eb; background: #e8f0fd; }
.k-wx { color: #15803d; background: #e8f6ed; }
.k-weread { color: #15803d; background: #e8f6ed; }
.k-local { color: #6e6e6e; background: var(--asc-surface-2); }

.rp-empty { padding: 40px 0; display: flex; flex-direction: column; gap: 14px; align-items: flex-start; }
.rp-empty-ops { display: flex; gap: 8px; }
.rp-loading { flex: 1; min-height: 200px; }

.rp-body { flex: 1; display: flex; gap: 18px; min-height: 0; }

.rp-content {
  flex: 1; min-width: 0; overflow-y: auto; padding-right: 8px;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius); padding: 22px 26px;
}
.rp-source {
  display: flex; align-items: center; gap: 6px; font-size: 12px;
  color: var(--asc-text-2); padding-bottom: 12px; margin-bottom: 14px;
  border-bottom: 1px dashed var(--asc-divider);
}
.rp-source-url { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; max-width: 100%; }
a.rp-source-url { color: var(--asc-primary); text-decoration: none; border-bottom: 1px solid rgba(124, 92, 252, .35); }
a.rp-source-url:hover { border-bottom-color: var(--asc-primary); }
.rp-nobody { color: var(--asc-text-2); font-size: 13px; }

.ep-block { font-size: 14.5px; line-height: 1.9; color: var(--asc-text); }
.ep-block :deep(h1), .ep-block :deep(h2), .ep-block :deep(h3) { font-weight: 600; line-height: 1.5; margin: 20px 0 8px; }
.ep-block :deep(h1) { font-size: 19px; }
.ep-block :deep(h2) { font-size: 16px; padding-left: 10px; border-left: 3px solid var(--asc-primary); }
.ep-block :deep(h3) { font-size: 15px; }
.ep-block :deep(p) { margin: 10px 0; }
.ep-block :deep(ul), .ep-block :deep(ol) { padding-left: 20px; margin: 10px 0; }
.ep-block :deep(li) { margin: 5px 0; }
.ep-block :deep(code) { background: var(--asc-surface-2); padding: 2px 5px; border-radius: 4px; font-size: 12.5px; }
.ep-block :deep(pre) { background: var(--asc-surface-2); padding: 12px; border-radius: 8px; overflow-x: auto; }
.ep-block :deep(blockquote) {
  margin: 12px 0; padding: 8px 14px; border-left: 3px solid var(--asc-border);
  background: var(--asc-surface-2); border-radius: 0 8px 8px 0; color: var(--asc-text-2);
}
.ep-block :deep(img) { max-width: 100%; border-radius: 8px; }
.ep-block :deep(table) { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }
.ep-block :deep(th), .ep-block :deep(td) { border: 1px solid var(--asc-divider); padding: 6px 9px; }
.ep-block :deep(a) { color: var(--asc-primary); }
.ep-block.flash { animation: ep-flash 1.2s ease; }
@keyframes ep-flash {
  0% { background: var(--asc-primary-soft); }
  100% { background: transparent; }
}

/* 侧栏 */
.rp-side {
  width: 340px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius); overflow: hidden;
}
.rp-side.collapsed { width: 132px; }
.side-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 9px 10px; border-bottom: 1px solid var(--asc-divider); flex-shrink: 0;
}
.side-tabs { display: flex; gap: 12px; }
.side-tab { font-size: 13px; color: var(--asc-text-2); cursor: pointer; padding-bottom: 2px; }
.side-tab.on { color: var(--asc-primary); font-weight: 600; border-bottom: 2px solid var(--asc-primary); }
.side-body { flex: 1; overflow-y: auto; padding: 12px; }

.sec { margin-bottom: 18px; }
.sec-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.sec-title { font-size: 13px; font-weight: 600; }
.sec-md { font-size: 13px; }
.sec-md :deep(p) { margin: 6px 0; line-height: 1.8; }
.sec-md :deep(h2) { font-size: 14px; }
.sec-empty { font-size: 12px; color: var(--asc-text-2); line-height: 1.7; }
.sec-loading { font-size: 12px; color: var(--asc-text-2); }
.sec-progress { font-size: 12px; color: var(--asc-primary); margin-bottom: 4px; }
.sec-ops { display: flex; justify-content: flex-end; margin-top: 6px; }

.ask-item { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--asc-divider); }
.ask-q { font-size: 12.5px; font-weight: 600; color: var(--asc-text-2); margin-bottom: 4px; }
.ask-ops { display: flex; justify-content: flex-end; }

.chain { padding: 10px 0; border-bottom: 1px dashed var(--asc-divider); }
.chain-sel {
  font-size: 12px; color: var(--asc-primary); background: var(--asc-primary-soft);
  border-radius: 6px; padding: 5px 8px; cursor: pointer; line-height: 1.6;
}
.chain-page { color: var(--asc-text-2); margin-left: 6px; }
.chain-turn { margin-top: 8px; }
.chain-q { font-size: 12.5px; font-weight: 600; color: var(--asc-text-2); margin-bottom: 3px; }
.chain-ops { display: flex; justify-content: flex-end; margin-top: 2px; }
.chain-ask { display: flex; gap: 6px; margin-top: 8px; }

/* 选中工具条 */
.sel-toolbar {
  position: fixed; z-index: 3000; display: flex; gap: 6px;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 8px; padding: 5px; box-shadow: 0 6px 20px rgba(0, 0, 0, .12);
}

/* 最近阅读 */
.recent-note {
  font-size: 12px; color: var(--asc-text-2); background: var(--asc-surface-2);
  border-radius: 6px; padding: 8px 10px; margin-bottom: 12px; line-height: 1.65;
}
.recent-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 0; border-bottom: 1px solid var(--asc-divider);
}
.recent-main { flex: 1; min-width: 0; }
.recent-t {
  font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 6px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.recent-m { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--asc-text-2); margin-top: 3px; }
.badge-saved {
  flex-shrink: 0; font-size: 11px; font-weight: 500; color: #15803d;
  background: #e8f6ed; border-radius: 4px; padding: 1px 6px;
}
.recent-ops { display: flex; align-items: center; flex-shrink: 0; }
</style>
