<template>
  <div class="page kb-page">
    <!-- 页头：图标锚点 + 标题 + 权重提示（原长段说明收进 tooltip，降低噪音） -->
    <div class="kb-head">
      <div class="kb-head-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
          stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
        </svg>
      </div>
      <div class="header-title">
        <h2>个人知识库</h2>
        <p class="header-sub">原文与笔记自动归档、向量化 · AI 问答优先从这里取答案</p>
      </div>
      <!-- 笔记权重：可修改，检索打分即时生效（默认 1.5） -->
      <el-popover v-model:visible="weightPop" placement="bottom-end" :width="288" trigger="click"
        popper-class="kb-w-pop" :show-after="60">
        <template #reference>
          <button type="button" class="kb-weight-pill"
            :aria-label="`笔记检索权重，当前 ×${wFmt(noteWeight)}，点击修改`">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true">
              <path d="M12 2l1.7 5.3L19 9l-5.3 1.7L12 16l-1.7-5.3L5 9l5.3-1.7L12 2z" />
            </svg>
            笔记权重 ×{{ wFmt(noteWeight) }}
            <svg class="kb-weight-chev" viewBox="0 0 24 24" width="11" height="11" fill="none"
              stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </template>
        <div class="nw-title">笔记检索权重</div>
        <p class="nw-desc">笔记相对原文的检索优先级：越高，AI 问答越倾向先取笔记作答。修改即时生效，无需重建索引。</p>
        <div class="nw-input-row">
          <el-input-number v-model="noteWeight" :min="0.5" :max="3" :step="0.1"
            :precision="1" size="small" controls-position="right" />
          <span class="nw-hint">建议 0.5 – 3.0</span>
        </div>
        <div class="nw-actions">
          <el-button text size="small" :disabled="Math.abs(noteWeight - 1.5) < 0.001"
            @click="noteWeight = 1.5">恢复默认 1.5</el-button>
          <el-button type="primary" size="small" :loading="savingWeight" @click="saveWeight">保存</el-button>
        </div>
      </el-popover>
    </div>

    <!-- 概览统计（与复习页 stat-chip 同一视觉语言） -->
    <div v-if="!loading && (data.materials.length || data.notes.length)" class="kb-stats">
      <div class="kb-stat"><b>{{ data.materials.length }}</b><i>材料</i></div>
      <div class="kb-stat"><b>{{ indexedCount }}</b><i>已入库</i></div>
      <div class="kb-stat"><b>{{ chunkTotal }}</b><i>向量块</i></div>
      <div class="kb-stat"><b>{{ data.notes.length }}</b><i>笔记<em v-if="aiNoteCount"> · AI {{ aiNoteCount }}</em></i></div>
      <div v-if="pendingCount" class="kb-stat pending" @click="tab = 'materials'">
        <b>{{ pendingCount }}</b><i>待入库 ›</i>
      </div>
    </div>

    <!-- 工具条：tabs 行（吸顶，滚动时切 tab 不下沉）。
         搜索框已下移到各 tab 内容区顶部：它只服务「按材料 / 按笔记」，
         留在 tab 栏右侧会让「知识图谱」也挂着一个用不上的输入框。 -->
    <div class="kb-tabs-wrap">
      <el-tabs v-model="tab" class="kb-tabs">
      <!-- 按材料 -->
      <el-tab-pane label="按材料" name="materials">
        <!-- 顶部行：左文件类型筛选 chips + 右搜索框（与「按笔记」同一结构，见 .kb-pane-head）。
             搜索框独立于数据状态（表空时也要能搜），chips 则只在有材料时出现 -->
        <div class="kb-pane-head">
          <div v-if="data.materials.length" class="kb-type-chips" role="group" aria-label="按文件类型筛选">
            <button type="button" class="kb-type-chip" :class="{ active: fmtFilter === 'all' }"
              :aria-pressed="fmtFilter === 'all'" @click="fmtFilter = 'all'">
              全部<b>{{ data.materials.length }}</b>
            </button>
            <button v-for="g in visibleFmtFilters" :key="g" type="button"
              class="kb-type-chip" :class="{ active: fmtFilter === g }"
              :aria-pressed="fmtFilter === g" @click="fmtFilter = g">
              {{ fmtGroupLabel(g) }}<b>{{ fmtCount(g) }}</b>
            </button>
          </div>
          <el-input v-model="search" class="kb-search" placeholder="搜索材料" clearable
            :prefix-icon="Search" @input="load" />
        </div>
        <div class="kb-table-card"><el-table :data="filteredMaterials" v-loading="loading">
          <el-table-column label="材料" min-width="240">
            <template #default="{ row }">
              <div class="kb-m-cell">
                <span class="kb-fmt" :class="'fmt-' + fmtGroup(row.format)" :title="fmtLabel(row.format)">
                  <el-icon :size="15"><component :is="fmtIcon(row.format)" /></el-icon>
                </span>
                <el-link type="primary" class="kb-m-title" :underline="false"
                  @click="$router.push(`/study/${row.material_id}`)">{{ row.title }}</el-link>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="入库状态" width="150">
            <template #default="{ row }">
              <button v-if="row.indexed" type="button" class="kb-status kb-status-btn" :class="statusOf(row).cls"
                :aria-label="`查看 ${row.title} 的 ${row.chunk_count} 个切块内容`"
                @click="openChunks(row)">
                <i class="kb-dot" aria-hidden="true"></i>{{ statusOf(row).text }}
                <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor"
                  stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M9 6l6 6-6 6" />
                </svg>
              </button>
              <span v-else class="kb-status" :class="statusOf(row).cls">
                <i class="kb-dot" aria-hidden="true"></i>{{ statusOf(row).text }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="笔记" width="80" align="right">
            <template #default="{ row }">
              <span class="kb-num" :class="{ zero: !row.note_count }">{{ row.note_count || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="导入时间" width="110">
            <template #default="{ row }"><span class="kb-date">{{ shortDate(row.created_at) }}</span></template>
          </el-table-column>
          <el-table-column label="" width="200" align="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" :disabled="row.parsed_status !== 'success'"
                :loading="rebuilding === row.material_id" @click="rebuild(row)">重建索引</el-button>
              <el-button size="small" text :type="row.entity_count ? 'success' : 'primary'"
                :disabled="row.parsed_status !== 'success'"
                :loading="extracting === row.material_id"
                :title="row.entity_count ? '查看该材料的知识图谱' : '为该材料生成知识图谱（调用大模型，消耗少量 token）'"
                @click="row.entity_count ? viewGraph(row) : extractGraph(row)">
                {{ row.entity_count ? `图谱 · ${row.entity_count}` : '生成图谱' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table></div>
        <el-empty v-if="!loading && data.materials.length === 0" :image="mascot" :image-size="120"
          description="知识库为空，先去材料库上传学习内容">
          <el-button type="primary" @click="$router.push('/')">去材料库上传</el-button>
        </el-empty>
        <!-- 筛选后为空（材料本身有，只是该类型没有）→ 与「按笔记」同一套提示 -->
        <div v-else-if="!loading && !filteredMaterials.length" class="kb-filter-empty">
          <span>该类型下暂无材料</span>
          <el-button text type="primary" size="small" @click="fmtFilter = 'all'">查看全部 {{ data.materials.length }} 条</el-button>
        </div>
      </el-tab-pane>

      <!-- 按笔记 -->
      <el-tab-pane label="按笔记" name="notes">
        <div class="kb-pane-head">
          <div v-if="data.notes.length" class="kb-type-chips" role="group" aria-label="按笔记类型筛选">
            <button type="button" class="kb-type-chip" :class="{ active: typeFilter === 'all' }"
              :aria-pressed="typeFilter === 'all'" @click="typeFilter = 'all'">
              全部<b>{{ data.notes.length }}</b>
            </button>
            <button v-for="t in visibleTypeFilters" :key="t.key" type="button"
              class="kb-type-chip" :class="{ active: typeFilter === t.key }"
              :aria-pressed="typeFilter === t.key" @click="typeFilter = t.key">
              {{ t.label }}<b>{{ typeCount(t.key) }}</b>
            </button>
          </div>
          <!-- 搜索框与筛选 chips 同行靠右；chips 为空（暂无笔记）时它仍要在 -->
          <el-input v-model="search" class="kb-search" placeholder="搜索笔记" clearable
            :prefix-icon="Search" @input="load" />
        </div>
        <div class="note-grid">
          <div v-for="(n, i) in filteredNotes" :key="n.id" class="kb-note-card card-in"
            :style="{ animationDelay: Math.min(i, 11) * 35 + 'ms' }" @click="openNote(n)">
            <div class="kb-note-head">
              <span class="kb-note-title">{{ n.title }}</span>
              <span class="kb-src-pill" :class="srcPillClass(n.source_type)">
                {{ srcPillLabel(n.source_type) }}
              </span>
            </div>
            <div class="kb-note-content">{{ n.content }}</div>
            <div class="kb-note-foot">
              <span class="kb-note-src" :title="n.material_title">{{ n.material_title }}</span>
              <span class="kb-note-date">{{ shortDate(n.updated_at) }}</span>
              <span class="kb-note-idx" :class="{ ok: n.indexed }">
                <i aria-hidden="true"></i>{{ n.indexed ? '已索引' : '未索引' }}
              </span>
            </div>
          </div>
        </div>
        <el-empty v-if="!loading && data.notes.length === 0" :image="mascot" :image-size="120"
          description="暂无笔记，在学习页划线或转 AI 内容，或在 AI 问答页把回答转存为笔记" />
        <div v-else-if="!loading && !filteredNotes.length" class="kb-filter-empty">
          <span>该类型下暂无笔记</span>
          <el-button text type="primary" size="small" @click="typeFilter = 'all'">查看全部 {{ data.notes.length }} 条</el-button>
        </div>
      </el-tab-pane>
      <!-- 知识图谱：实体 + 关系可视化（只读，数据来自后台异步抽取） -->
      <el-tab-pane label="知识图谱" name="graph">
        <KnowledgeGraph :focus-material="graphFocusMaterial" />
      </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 切块抽屉：点「已入库 · N 块」查看该材料的解析切块（只读）。
         复用学习页文本视图同一接口 GET /materials/{id}/chunks，按页码顺序列出。 -->
    <el-drawer v-model="chunksDrawer.show" class="kb-chunks-drawer" direction="rtl" :size="440"
      :with-header="false" destroy-on-close>
      <div class="kb-chunks">
        <div class="kb-chunks-head">
          <div class="kb-chunks-title" :title="chunksDrawer.title">{{ chunksDrawer.title }}</div>
          <button type="button" class="kb-chunks-close" aria-label="关闭" @click="chunksDrawer.show = false">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        <div class="kb-chunks-meta">
          <span v-if="chunksLoading">正在加载切块…</span>
          <span v-else>共 {{ chunksDrawer.list.length }} 块 · 按页码顺序</span>
        </div>
        <div v-loading="chunksLoading" class="kb-chunks-body">
          <div v-if="!chunksLoading && !chunksDrawer.list.length" class="kb-chunks-empty">
            该材料暂无解析文本，可到材料库「重建索引」后重试。
          </div>
          <div v-for="c in chunksDrawer.list" :key="c.id" class="kb-chunk-item">
            <div class="kb-chunk-head">
              <span class="kb-chunk-page">第 {{ c.page_no }} 页</span>
              <span v-if="c.section_path" class="kb-chunk-section">{{ c.section_path }}</span>
            </div>
            <div class="kb-chunk-content">{{ c.content }}</div>
          </div>
        </div>
      </div>
    </el-drawer>

    <!-- 笔记弹窗（全屏 Vditor，共用组件：与学习页/统计/播客页同一内核；
         孤儿笔记材料已删除也无妨——「跳转原文」按钮仅在 materialId 存在时出现） -->
    <NoteEditorDialog v-model="noteDialog.show" :note-id="noteDialog.id"
      :material-title="noteDialog.materialTitle" @changed="load" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, Document, Picture, Headset, VideoPlay, Reading } from '@element-plus/icons-vue'
import mascot from '../assets/mascot.png'
import { kbApi, settingsApi, materialApi } from '../api'
import { errMsg } from '../api/http'
import NoteEditorDialog from '../components/NoteEditorDialog.vue'
import KnowledgeGraph from '../components/KnowledgeGraph.vue'

const router = useRouter()
const route = useRoute()
const noteDialog = reactive({ show: false, id: null, materialTitle: '' })
// 切块抽屉：点「已入库 · N 块」查看该材料的解析切块（只读）
const chunksDrawer = reactive({ show: false, title: '', list: [] })
const chunksLoading = ref(false)

async function openChunks(row) {
  chunksDrawer.title = row.title
  chunksDrawer.list = []
  chunksDrawer.show = true
  chunksLoading.value = true
  try {
    const { data } = await materialApi.chunks(row.material_id)
    chunksDrawer.list = data || []
  } catch (e) {
    ElMessage.error(errMsg(e, '切块加载失败'))
  } finally {
    chunksLoading.value = false
  }
}

function openNote(n) {
  noteDialog.id = n.id
  noteDialog.materialTitle = n.material_title
  noteDialog.show = true
}

// 从别处跳转过来直接打开某条笔记（统计页 / 播客页的「已转笔记 · 查看」）。
// 这些笔记是材料无关的，列表里能查到来源标签；查不到（如刚转存、列表未刷新）则留空。
// 正文由 NoteEditorDialog 按 id 自行拉全文，这里只负责定位来源标签。
function openNoteById(id) {
  const item = (data.notes || []).find(n => n.id === Number(id))
  noteDialog.id = Number(id)
  noteDialog.materialTitle = item?.material_title || ''
  noteDialog.show = true
}

const tab = ref('materials')
const search = ref('')
const loading = ref(false)
const rebuilding = ref(null)
const extracting = ref(null)   // 生成知识图谱中的材料 id
const data = reactive({ materials: [], notes: [] })

const shortDate = (iso) => iso ? iso.slice(0, 10) : ''

// ---------- 笔记权重（可修改，默认 1.5，检索打分时读取 → 即时生效） ----------
const noteWeight = ref(1.5)
const weightPop = ref(false)
const savingWeight = ref(false)
// 显示格式：整数去小数尾（1.5 → "1.5"，2.0 → "2"）
const wFmt = (v) => {
  const n = Number(v)
  if (!Number.isFinite(n)) return '1.5'
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10)
}
async function loadNoteWeight() {
  try {
    const { data } = await settingsApi.get()
    noteWeight.value = Number(data.note_weight) || 1.5
  } catch { /* 读取失败时维持默认 1.5 */ }
}
async function saveWeight() {
  savingWeight.value = true
  try {
    await settingsApi.update({ note_weight: Number(noteWeight.value) })
    weightPop.value = false
    ElMessage.success(`笔记权重已设为 ×${wFmt(noteWeight.value)}，检索即时生效`)
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    savingWeight.value = false
  }
}

// ---------- 概览统计 ----------
const indexedCount = computed(() => data.materials.filter(m => m.indexed).length)
const chunkTotal = computed(() => data.materials.reduce((s, m) => s + (m.chunk_count || 0), 0))
const aiNoteCount = computed(() => data.notes.filter(n => n.source_type === 'ai_asset').length)
// 待入库 = 已解析成功但未建索引（可通过「重建索引」修复；扫描件/未解析不算）
const pendingCount = computed(() => data.materials.filter(m => !m.indexed && m.parsed_status === 'success').length)

// ---------- 笔记类型筛选（问答 chat / AI ai_asset / 手动 manual；客户端即时过滤，与搜索联动） ----------
const NOTE_TYPE_FILTERS = [
  { key: 'chat', label: '问答' },
  { key: 'ai_asset', label: 'AI' },
  { key: 'weekly_report', label: '周报' },
  { key: 'podcast_script', label: '播客' },
  { key: 'podcast_brief', label: '简报' },
  { key: 'manual', label: '手动' },
]
const typeFilter = ref('all')
// 计数为 0 的类型不占位（新增产物类型后，用户不会看到一排「周报 0」）
const visibleTypeFilters = computed(() => NOTE_TYPE_FILTERS.filter(t => typeCount(t.key) > 0))

// 来源徽章：卡片上短标签 / 弹窗里长标签，共用一份映射，避免两处口径漂移
const SRC_PILL = { chat: '问答', ai_asset: 'AI', weekly_report: '周报', podcast_script: '播客', podcast_brief: '简报' }
const srcPillLabel = (t) => SRC_PILL[t] || '手动'
const srcPillClass = (t) => ({
  ai: t === 'ai_asset', chat: t === 'chat',
  report: t === 'weekly_report', podcast: t === 'podcast_script',
  brief: t === 'podcast_brief',
})
const filteredNotes = computed(() =>
  typeFilter.value === 'all'
    ? data.notes
    : data.notes.filter(n => n.source_type === typeFilter.value))
const typeCount = (key) => data.notes.filter(n => n.source_type === key).length

// ---------- 格式徽章（与材料库卡片同一配色体系） ----------
const fmtLabel = (f) => ({ pdf: 'PDF', ppt: 'PPT', pptx: 'PPT', doc: 'WORD', docx: 'WORD', epub: 'EPUB', md: 'MD', markdown: 'MD', mp3: '音频', wav: '音频', m4a: '音频', mp4: '视频', jpg: '图片', jpeg: '图片', png: '图片', webp: '图片', bmp: '图片' }[f] || (f || '').toUpperCase())
const fmtGroup = (f) => {
  if (['jpg', 'jpeg', 'png', 'webp', 'bmp'].includes(f)) return 'img'
  if (['mp3', 'wav', 'm4a'].includes(f)) return 'audio'
  if (f === 'mp4') return 'video'
  if (['ppt', 'pptx'].includes(f)) return 'ppt'
  if (['doc', 'docx'].includes(f)) return 'doc'
  if (['md', 'markdown'].includes(f)) return 'md'
  if (f === 'epub') return 'epub'
  return 'pdf'
}
const fmtIcon = (f) => ({ img: Picture, audio: Headset, video: VideoPlay, epub: Reading }[fmtGroup(f)] || Document)

// ---------- 材料文件类型筛选（客户端即时过滤，与搜索联动；分组口径复用 fmtGroup） ----------
// chips 排列顺序：文档类在前（最常见），媒体类在后；计数为 0 的不占位
const FMT_ORDER = ['pdf', 'ppt', 'doc', 'md', 'epub', 'img', 'audio', 'video']
const FMT_GROUP_LABEL = { pdf: 'PDF', ppt: 'PPT', doc: 'WORD', md: 'MD', epub: 'EPUB', img: '图片', audio: '音频', video: '视频' }
const fmtGroupLabel = (g) => FMT_GROUP_LABEL[g] || String(g || '').toUpperCase()
const fmtFilter = ref('all')
const fmtCount = (g) => data.materials.filter(m => fmtGroup(m.format) === g).length
// 同「按笔记」：没有的类型不出现 —— 用户不会看到一排「图片 0」
const visibleFmtFilters = computed(() => FMT_ORDER.filter(g => fmtCount(g) > 0))
const filteredMaterials = computed(() =>
  fmtFilter.value === 'all'
    ? data.materials
    : data.materials.filter(m => fmtGroup(m.format) === fmtFilter.value))

// ---------- 入库状态（dot pill，弱化标签噪音） ----------
function statusOf(row) {
  if (row.indexed) return { cls: 'ok', text: `已入库 · ${row.chunk_count} 块` }
  if (row.parsed_status === 'scanned') return { cls: 'muted', text: '扫描件' }
  if (row.parsed_status !== 'success') return { cls: 'warn', text: '未解析' }
  return { cls: 'warn', text: '未入库' }
}

async function load() {
  loading.value = true
  try {
    const { data: d } = await kbApi.overview({ search: search.value || undefined })
    data.materials = d.materials
    data.notes = d.notes
  } finally {
    loading.value = false
  }
}

async function rebuild(row) {
  rebuilding.value = row.material_id
  try {
    const { data: r } = await kbApi.rebuild(row.material_id)
    ElMessage.success(`索引重建完成，入库 ${r.chunk_count} 块`)
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重建失败')
  } finally {
    rebuilding.value = null
  }
}

// 生成知识图谱：后台异步抽取实体（约需几分钟，取决于材料块数）；
// 内容未变时服务端会跳过 LLM 调用直接复用（指纹去重），点击后稍等刷新列表即可看到实体数。
async function extractGraph(row) {
  extracting.value = row.material_id
  try {
    await kbApi.extractEntities(row.material_id)
    ElMessage.success(`已开始为「${row.title}」生成知识图谱，完成后实体数会显示在按钮上`)
    // 后台抽取需要时间，延迟刷新一次列表（短材料约 1-2 分钟）
    setTimeout(() => load(), 30000)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '生成失败')
  } finally {
    extracting.value = null
  }
}

// 已生成图谱的材料：点击跳到「知识图谱」tab，并自动选中该材料的子图
const graphFocusMaterial = ref(null)
function viewGraph(row) {
  graphFocusMaterial.value = row.material_id
  tab.value = 'graph'
}

onMounted(async () => {
  await Promise.all([load(), loadNoteWeight()])
  // 从「已转笔记 · 查看」跳转过来：加载完列表再打开，这样能拿到来源标签
  const qid = route.query.note
  if (qid) {
    await openNoteById(qid)
    router.replace({ path: '/knowledge' })   // 清掉 query，避免刷新页面又弹一次
  }
})
</script>

<style scoped>
/* ===== 页头：图标锚点 + 标题 + 权重提示 ===== */
.kb-head { display: flex; align-items: center; gap: 14px; margin-bottom: 18px; }
.kb-head-icon {
  width: 44px; height: 44px; border-radius: 12px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  color: var(--asc-primary);
  background: linear-gradient(150deg, rgba(124, 92, 252, .16), rgba(124, 92, 252, .06));
  border: 1px solid rgba(124, 92, 252, .22);
}
.header-title { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.kb-page h2 { margin: 0; font-size: 20px; font-weight: 600; letter-spacing: .2px; }
.header-sub { margin: 0; font-size: 12.5px; color: var(--asc-text-3); }
.kb-weight-pill {
  display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0;
  font: inherit; font-size: 12px; font-weight: 500; color: var(--asc-primary);
  background: var(--asc-primary-soft); border: 1px solid rgba(124, 92, 252, .18);
  padding: 6px 11px; border-radius: 999px; cursor: pointer;
  transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease;
}
.kb-weight-pill:hover { border-color: rgba(124, 92, 252, .45); box-shadow: var(--asc-shadow-hover); }
.kb-weight-pill:active { transform: translateY(1px); }
.kb-weight-chev { opacity: .75; }

/* 权重编辑弹层 */
.nw-title { font-size: 14px; font-weight: 600; color: var(--asc-text); margin-bottom: 6px; }
.nw-desc { margin: 0 0 12px; font-size: 12px; line-height: 1.65; color: var(--asc-text-2); }
.nw-input-row { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.nw-hint { font-size: 11px; color: var(--asc-text-3); }
.nw-actions { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--asc-divider); padding-top: 10px; }

/* ===== 概览统计 chips ===== */
.kb-stats { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
.kb-stat {
  background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 12px;
  padding: 10px 16px; display: flex; flex-direction: column; gap: 2px; min-width: 88px;
}
.kb-stat b { font-size: 19px; font-weight: 700; line-height: 1.25; font-variant-numeric: tabular-nums; }
.kb-stat i { font-style: normal; font-size: 11px; color: var(--asc-text-3); }
.kb-stat.pending {
  cursor: pointer;
  background: linear-gradient(150deg, rgba(124, 92, 252, .12), rgba(124, 92, 252, .04));
  border-color: rgba(124, 92, 252, .28);
  transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
}
.kb-stat.pending b { color: var(--asc-primary); }
.kb-stat.pending:hover { transform: translateY(-2px); border-color: rgba(124, 92, 252, .45); box-shadow: var(--asc-shadow-hover); }

/* ===== 工具条：tabs（吸顶） ===== */
.kb-tabs-wrap { position: relative; }
/* 搜索框：已从 tab 栏右侧下移到各 tab 内容区顶部（见 .kb-pane-head），
   因此不再 sticky —— 它只服务「按材料 / 按笔记」，切到知识图谱就不该出现。 */
.kb-search { width: 240px; flex-shrink: 0; }
/* 「按材料 / 按笔记」内容区顶部：左筛选 chips + 右搜索框，两端对齐；
   chips 为空（暂无数据）时搜索框仍靠右（margin-left:auto 兜底）；
   窄屏时搜索框自动折到下一行。—— 两个 tab 共用同一结构，改一处两边同步。 */
.kb-pane-head {
  display: flex; flex-wrap: wrap; align-items: flex-start; gap: 12px;
  justify-content: space-between; margin-bottom: 14px;
}
.kb-pane-head .kb-type-chips { flex: 1; min-width: 0; margin-bottom: 0; }
.kb-pane-head .kb-search { margin-left: auto; }
.kb-tabs :deep(.el-tabs__header) {
  position: sticky; top: 0; z-index: 6;
  margin: 0; padding: 1px 0 0 0;
  background: var(--asc-bg);
  /* 吸顶时用投影替代下边距，既分隔滚动上来的内容又不留透明缝 */
  box-shadow: 0 1px 0 var(--asc-border);
}
.kb-tabs :deep(.el-tabs__item) { font-size: 14px; padding: 0 18px; }
.kb-tabs :deep(.el-tabs__content) { padding-top: 16px; }
/* ⚠️ el-tabs 默认 content overflow:hidden 会建立滚动上下文，打断内部（知识图谱头）的
   position:sticky → 改为 visible（活动 pane 用 v-show 隐藏，无需靠 overflow 裁剪）。 */
.kb-tabs :deep(.el-tabs__content) { overflow: visible; }

/* ===== 按材料：卡片化表格 ===== */
.kb-table-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 14px; padding: 4px 12px; overflow: hidden;
}
.kb-table-card :deep(.el-table) { --el-table-header-bg-color: transparent; }
.kb-table-card :deep(.el-table th.el-table__cell) { font-size: 12px; color: var(--asc-text-3); }
.kb-table-card :deep(.el-table td.el-table__cell) { padding: 11px 0; }
.kb-m-cell { display: flex; align-items: center; gap: 10px; min-width: 0; }
.kb-fmt {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
}
.fmt-pdf { background: #fdeee7; color: #d85a30; }
.fmt-ppt { background: #fdf1dd; color: #ba7517; }
.fmt-doc { background: #e9f1fc; color: #378add; }
.fmt-md { background: #eef1f5; color: #64748b; }
.fmt-audio { background: #e5f6f3; color: #0f9488; }
.fmt-video { background: #f1ebfd; color: #7c5cfc; }
.fmt-img { background: #e6f7fb; color: #0891b2; }
.fmt-epub { background: #e7f5ec; color: #15803d; }
.kb-m-title { font-size: 14px; font-weight: 500; min-width: 0; }
.kb-m-title :deep(.el-link__inner) {
  display: inline-block; max-width: 46vw;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.kb-num { font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.kb-num.zero { color: var(--asc-text-3); font-weight: 400; }
.kb-date { font-size: 12px; color: var(--asc-text-2); font-variant-numeric: tabular-nums; }
.kb-status {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 8px; font-weight: 500; line-height: 1;
  padding: 5px 10px; border-radius: 999px;
  white-space: nowrap; flex-shrink: 0;
}
.kb-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.kb-status.ok { color: #0f6e56; background: rgba(15, 110, 86, .08); }
.kb-status.warn { color: #ba7517; background: rgba(186, 117, 23, .09); }
.kb-status.muted { color: var(--asc-text-3); background: var(--asc-surface-2); }
/* 可点击的「已入库」pill：按钮化，加 hover 反馈与右侧箭头 */
.kb-status-btn {
  font: inherit; border: none; cursor: pointer;
  transition: box-shadow .16s ease, transform .16s ease, filter .16s ease;
}
.kb-status-btn:hover { filter: brightness(.93); box-shadow: var(--asc-shadow-hover); }
.kb-status-btn:active { transform: translateY(1px); }
.kb-status-btn svg { margin-left: 1px; opacity: .8; }

/* ===== 按笔记：类型筛选 chips ===== */
.kb-type-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }
.kb-type-chip {
  display: inline-flex; align-items: center; gap: 6px;
  font: inherit; font-size: 12px; font-weight: 500; line-height: 1;
  padding: 7px 12px; border-radius: 999px; cursor: pointer;
  background: var(--asc-card); color: var(--asc-text-2);
  border: 1px solid var(--asc-border);
  transition: color .16s ease, border-color .16s ease, background .16s ease;
}
.kb-type-chip b { font-size: 11px; font-weight: 600; color: var(--asc-text-3); font-variant-numeric: tabular-nums; transition: color .16s ease; }
.kb-type-chip:hover { color: var(--asc-text); border-color: rgba(124, 92, 252, .4); }
.kb-type-chip.active { background: var(--asc-primary-soft); border-color: rgba(124, 92, 252, .35); color: var(--asc-primary); }
.kb-type-chip.active b { color: var(--asc-primary); }
.kb-filter-empty {
  display: flex; align-items: center; justify-content: center; gap: 4px;
  padding: 40px 0 8px; font-size: 13px; color: var(--asc-text-3);
}

/* ===== 按笔记：卡片网格 ===== */
.note-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.kb-note-card {
  position: relative; display: flex; flex-direction: column;
  background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 14px;
  padding: 16px 18px 12px; cursor: pointer;
  transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}
.kb-note-card::before {
  content: ''; position: absolute; top: 0; left: 18px; right: 18px; height: 2px;
  border-radius: 2px; opacity: 0; transition: opacity .18s ease;
  background: linear-gradient(90deg, var(--asc-primary), rgba(124, 92, 252, .25));
}
.kb-note-card:hover {
  transform: translateY(-3px); border-color: rgba(124, 92, 252, .35);
  box-shadow: 0 8px 24px rgba(31, 24, 68, .08);
}
.kb-note-card:hover::before { opacity: 1; }
.kb-note-head { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.kb-note-title { font-size: 14px; font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-src-pill {
  font-size: 11px; font-weight: 500; line-height: 1; padding: 4px 8px;
  border-radius: 999px; background: var(--asc-surface-2); color: var(--asc-text-2); flex-shrink: 0;
}
.kb-src-pill.ai { background: var(--asc-primary-soft); color: var(--asc-primary); }
.kb-src-pill.chat { background: rgba(28, 145, 138, .12); color: #12837c; }
.kb-src-pill.report { background: rgba(217, 119, 6, .12); color: #b45309; }
.kb-src-pill.podcast { background: rgba(74, 114, 212, .12); color: #3f63c4; }
/* 简报：与「播客脚本」同属播客产物但必须能一眼分开 → 用品红（白底 5.3:1） */
.kb-src-pill.brief { background: rgba(162, 28, 175, .12); color: #a21caf; }
.kb-note-content {
  flex: 1; font-size: 13px; color: var(--asc-text-2); line-height: 1.7; margin-bottom: 12px;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.kb-note-foot {
  display: flex; gap: 10px; align-items: center; font-size: 11px; color: var(--asc-text-3);
  border-top: 1px solid var(--asc-divider); padding-top: 10px;
}
.kb-note-src { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-note-date { flex-shrink: 0; font-variant-numeric: tabular-nums; }
.kb-note-idx { display: inline-flex; align-items: center; gap: 5px; flex-shrink: 0; }
.kb-note-idx i { width: 5px; height: 5px; border-radius: 50%; background: #d9a13b; }
.kb-note-idx.ok i { background: #0f6e56; }

/* ===== 切块抽屉 ===== */
.kb-chunks-drawer :deep(.el-drawer__body) { padding: 0; display: flex; flex-direction: column; }
.kb-chunks { display: flex; flex-direction: column; height: 100%; }
.kb-chunks-head {
  display: flex; align-items: center; gap: 10px;
  padding: 16px 18px 12px; border-bottom: 1px solid var(--asc-divider);
}
.kb-chunks-title { flex: 1; min-width: 0; font-size: 15px; font-weight: 600; }
/* 标题过长仍显示两行，保留可读性 */
.kb-chunks-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-chunks-close {
  flex-shrink: 0; width: 30px; height: 30px; border-radius: 8px;
  display: inline-flex; align-items: center; justify-content: center;
  border: none; background: transparent; color: var(--asc-text-2); cursor: pointer;
  transition: background .16s ease, color .16s ease;
}
.kb-chunks-close:hover { background: var(--asc-surface-2); color: var(--asc-text); }
.kb-chunks-meta { padding: 8px 18px 0; font-size: 12px; color: var(--asc-text-3); }
.kb-chunks-body { flex: 1; overflow-y: auto; padding: 12px 18px 20px; min-height: 120px; }
.kb-chunks-empty { padding: 40px 0; text-align: center; font-size: 13px; color: var(--asc-text-3); }
.kb-chunk-item {
  background: var(--asc-surface-2); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 12px 14px; margin-bottom: 12px;
}
.kb-chunk-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.kb-chunk-page {
  flex-shrink: 0; white-space: nowrap;
  font-size: 11px; font-weight: 600; color: var(--asc-primary); line-height: 1;
  padding: 4px 9px; border-radius: 6px; background: var(--asc-primary-soft);
}
.kb-chunk-section {
  flex: 1 1 auto; min-width: 0;
  font-size: 11px; color: var(--asc-text-3);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.kb-chunk-content {
  font-size: 13px; color: var(--asc-text-2); line-height: 1.75;
  white-space: pre-wrap; word-break: break-word;
}

/* 空状态图标圆角 */
:deep(.el-empty__image) { border-radius: 16px; }

/* ===== 卡片错峰入场（尊重 reduced-motion） ===== */
@keyframes kb-card-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
.card-in { animation: kb-card-in .32s ease backwards; }
@media (prefers-reduced-motion: reduce) {
  .card-in { animation: none; }
  .kb-note-card, .kb-stat.pending, .kb-note-card::before { transition: none; }
}
</style>
