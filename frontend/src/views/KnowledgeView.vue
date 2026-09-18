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

    <!-- 工具条：tabs 行右侧放搜索框，一行搞定 -->
    <div class="kb-tabs-wrap">
      <el-tabs v-model="tab" class="kb-tabs">
      <!-- 按材料 -->
      <el-tab-pane label="按材料" name="materials">
        <div class="kb-table-card"><el-table :data="data.materials" v-loading="loading">
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
              <span class="kb-status" :class="statusOf(row).cls">
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
          <el-table-column label="" width="110" align="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary" :disabled="row.parsed_status !== 'success'"
                :loading="rebuilding === row.material_id" @click="rebuild(row)">重建索引</el-button>
            </template>
          </el-table-column>
        </el-table></div>
        <el-empty v-if="!loading && data.materials.length === 0" :image="mascot" :image-size="120"
          description="知识库为空，先去材料库上传学习内容">
          <el-button type="primary" @click="$router.push('/')">去材料库上传</el-button>
        </el-empty>
      </el-tab-pane>

      <!-- 按笔记 -->
      <el-tab-pane label="按笔记" name="notes">
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
      </el-tabs>
      <el-input v-model="search" class="kb-search" placeholder="搜索材料或笔记" clearable
        :prefix-icon="Search" @input="load" />
    </div>

    <!-- 孤儿笔记弹窗（材料已删除，无学习页可跳）：与资料详情页笔记弹窗一致 -->
    <el-dialog v-model="noteDialog.show" width="560px" class="note-dialog" :close-on-click-modal="false">
      <template #header>
        <div class="nd-header">
          <div class="nd-title-row">
            <span class="nd-title">编辑笔记</span>
            <span class="nd-src-tag" :class="noteTagClass(noteDialog.sourceType)">
              {{ noteTagLabel(noteDialog.sourceType) }}
            </span>
          </div>
          <div class="nd-sub">{{ noteDialog.materialTitle }}</div>
        </div>
      </template>

      <div class="nd-body">
        <div class="nd-field">
          <label class="nd-label" for="kb-nd-title">标题</label>
          <el-input id="kb-nd-title" v-model="noteDialog.title" placeholder="给这条笔记起个标题" maxlength="80" />
        </div>

        <div class="nd-field">
          <div class="nd-field-head">
            <label class="nd-label">内容</label>
            <div class="nd-seg" role="tablist" aria-label="笔记内容编辑方式">
              <button type="button" class="nd-seg-btn" :class="{ active: noteDialog.mode === 'edit' }"
                role="tab" :aria-selected="noteDialog.mode === 'edit'" @click="noteDialog.mode = 'edit'">编辑</button>
              <button type="button" class="nd-seg-btn" :class="{ active: noteDialog.mode === 'preview' }"
                role="tab" :aria-selected="noteDialog.mode === 'preview'" @click="noteDialog.mode = 'preview'">预览</button>
            </div>
          </div>
          <textarea v-if="noteDialog.mode === 'edit'" v-model="noteDialog.content" :rows="10" class="nd-textarea"
            placeholder="支持 Markdown，可直接粘贴"
            @mouseup.stop="onNoteSelect" @keyup="onNoteSelect" @blur="hideNoteSel"></textarea>
          <div v-else class="md-preview nd-preview" v-html="renderMd(noteDialog.content)"></div>
          <div v-if="noteSel.show" class="nd-sel-bar">
            <span class="nd-sel-count">已选中 {{ noteSel.text.length }} 字</span>
            <span class="nd-sel-sep" aria-hidden="true"></span>
            <el-button text type="primary" size="small" @mousedown.prevent @click="noteTransform('rewrite', noteSel)">改写</el-button>
            <el-button text type="primary" size="small" @mousedown.prevent @click="noteTransform('expand', noteSel)">扩写</el-button>
            <el-button text type="primary" size="small" @mousedown.prevent @click="noteTransform('continue', noteSel)">续写</el-button>
            <el-button text type="primary" size="small" @mousedown.prevent @click="noteTransform('summarize', noteSel)">总结</el-button>
            <el-button text size="small" @mousedown.prevent @click="hideNoteSel">取消</el-button>
          </div>
          <div class="nd-count">{{ noteDialog.content.length }} 字</div>
        </div>
      </div>

      <div v-if="noteDialog.content.trim()" class="nd-ai">
        <span class="nd-ai-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor">
            <path d="M12 2l1.7 5.3L19 9l-5.3 1.7L12 16l-1.7-5.3L5 9l5.3-1.7L12 2z"/>
            <path d="M19 14l.9 2.6 2.6.9-2.6.9L19 21l-.9-2.6-2.6-.9 2.6-.9L19 14z"/>
          </svg>
        </span>
        <span class="nd-ai-label">AI 加工</span>
        <span class="nd-ai-sep" aria-hidden="true"></span>
        <el-tooltip content="换个说法，保持原意与事实" placement="top" :show-after="250">
          <el-button text type="primary" size="small" @click="noteTransform('rewrite')">改写</el-button>
        </el-tooltip>
        <el-tooltip content="补充细节、例子与解释，让笔记更充实" placement="top" :show-after="250">
          <el-button text type="primary" size="small" @click="noteTransform('expand')">扩写</el-button>
        </el-tooltip>
        <el-tooltip content="接着已有内容往下续写，补全或延伸思路" placement="top" :show-after="250">
          <el-button text type="primary" size="small" @click="noteTransform('continue')">续写</el-button>
        </el-tooltip>
        <el-tooltip content="压缩成精炼要点，突出核心结论" placement="top" :show-after="250">
          <el-button text type="primary" size="small" @click="noteTransform('summarize')">总结</el-button>
        </el-tooltip>
      </div>

      <div class="nd-shortcuts">
        <el-button v-if="noteDialog.materialId && noteDialog.materialTitle !== '（材料已删除）' && noteDialog.anchor?.page_no"
          text size="small" @click="jumpToOriginal">跳转原文 P{{ noteDialog.anchor.page_no }}</el-button>
        <el-tooltip content="把这条笔记出成选择题，进复习队列定期重考（再认）" placement="top" :show-after="250">
          <el-button text type="warning" size="small" :loading="addingReview" @click="addToReview">加入复习</el-button>
        </el-tooltip>
        <el-tooltip content="出成引导题，先自己讲一遍再对照答案，练主动回忆（费曼）" placement="top" :show-after="250">
          <el-button text type="primary" size="small" :loading="addingRecall" @click="addToRecall">生成复述卡</el-button>
        </el-tooltip>
        <el-button v-if="noteDialog.content.length > 200" text type="warning" size="small"
          :loading="splittingReview" @click="splitToReview">拆成多卡</el-button>
        <span class="nd-sep"></span>
        <el-popconfirm title="删除这条笔记？不可恢复"
          confirm-button-text="删除" confirm-button-type="danger" cancel-button-text="取消"
          @confirm="deleteNote">
          <template #reference>
            <el-button type="danger" text size="small">删除</el-button>
          </template>
        </el-popconfirm>
      </div>

      <template #footer>
        <div class="nd-footer">
          <div class="nd-actions">
            <el-button @click="noteDialog.show = false">取消</el-button>
            <el-button type="primary" :loading="noteDialog.saving" @click="saveNote">保存</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 笔记加工结果弹窗（原文 vs 结果，采用/放弃） -->
    <el-dialog v-model="transformDialog.show" :title="noteTransformTitle" width="560px" class="note-dialog">
      <div class="polish-block">
        <div class="polish-label">原文</div>
        <div class="polish-text">{{ transformDialog.original }}</div>
      </div>
      <div class="polish-block polish-block-new">
        <div class="polish-label">{{ transformDialog.mode === 'continue' ? '续写内容' : (TRANSFORM_LABELS[transformDialog.mode] + '后') }}</div>
        <div class="polish-text">{{ transformDialog.result }}<span v-if="transformDialog.streaming" class="stream-cursor">▍</span></div>
      </div>
      <template #footer>
        <el-button @click="transformDialog.show = false">放弃</el-button>
        <el-button type="primary" :disabled="transformDialog.streaming" @click="adoptTransform">{{ transformDialog.mode === 'continue' ? '插入' : '采用' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, Document, Picture, Headset, VideoPlay, Reading } from '@element-plus/icons-vue'
import MarkdownIt from 'markdown-it'
import mascot from '../assets/mascot.png'
import { kbApi, noteApi, reviewApi, settingsApi } from '../api'
import http, { errMsg } from '../api/http'
// ⚠️ SSE 统一走共享 util：内联副本曾硬编码 127.0.0.1:8000（手机访问时打到手机自己）
// 且不带令牌头（远程模式必 401）—— 方案 §8.2 缺陷 #1 的同类实现。
import { streamSSE } from '../utils/sse'

const router = useRouter()
const route = useRoute()
const md = new MarkdownIt({ breaks: true })
const renderMd = (t) => md.render(t || '')
const noteDialog = reactive({
  show: false, id: null, title: '', content: '', materialTitle: '',
  materialId: null, sourceType: 'manual', anchor: null, saving: false, mode: 'edit',
})

// 笔记加工（改写/扩写/续写/总结）
const TRANSFORM_LABELS = { rewrite: '改写', expand: '扩写', continue: '续写', summarize: '总结' }
const transformDialog = reactive({ show: false, mode: 'rewrite', original: '', result: '', streaming: false, sel: null })
// 笔记内容选区：选中指定文字后浮出加工工具（sel 为 null=整条笔记）
const noteSel = reactive({ show: false, text: '', start: 0, end: 0 })
const noteTransformTitle = computed(() => {
  const act = TRANSFORM_LABELS[transformDialog.mode] || '加工'
  return transformDialog.sel ? `${act}选中文字` : `${act}笔记`
})

// SSE 流式读取（与资料详情页 streamSSE 一致）

async function openNote(n) {
  noteDialog.id = n.id
  noteDialog.title = n.title
  noteDialog.content = n.content
  noteDialog.materialTitle = n.material_title
  noteDialog.materialId = n.material_id
  noteDialog.sourceType = n.source_type || 'manual'
  noteDialog.mode = 'edit'
  noteDialog.show = true
  // 列表 content 截断过，弹窗拉全文（含 anchor / material_id）
  try {
    const { data } = await http.get(`/notes/${n.id}`)
    noteDialog.content = data.content
    noteDialog.anchor = data.anchor || null
  } catch { /* 拉全文失败则用列表截断内容 */ }
}

// 从别处跳转过来直接打开某条笔记（统计页 / 播客页的「已转笔记 · 查看」）。
// 这些笔记是材料无关的，列表里能查到来源标签；查不到（如刚转存、列表未刷新）则留空。
async function openNoteById(id) {
  // ⚠️ 这里的响应变量绝不能叫 data —— 外层 `const data = reactive({...})` 是笔记列表，
  // 用 const { data } 解构会把它整个遮蔽掉。
  const item = (data.notes || []).find(n => n.id === Number(id))
  try {
    const res = await http.get(`/notes/${id}`)
    const d = res.data
    noteDialog.id = d.id
    noteDialog.title = d.title
    noteDialog.content = d.content
    noteDialog.sourceType = d.source_type || 'manual'
    noteDialog.materialId = d.material_id
    noteDialog.materialTitle = item?.material_title || ''
    noteDialog.anchor = d.anchor || null
    noteDialog.mode = 'edit'
    noteDialog.show = true
  } catch {
    ElMessage.error('笔记打开失败，可能已被删除')
  }
}

function jumpToOriginal() {
  const anchor = noteDialog.anchor
  if (!noteDialog.materialId || !anchor?.page_no) return
  const q = { page: anchor.page_no }
  if (anchor.selected_text) q.hl = anchor.selected_text
  router.push({ path: `/study/${noteDialog.materialId}`, query: q })
}

async function saveNote() {
  noteDialog.saving = true
  try {
    const { data } = await noteApi.update(noteDialog.id, { title: noteDialog.title, content: noteDialog.content })
    noteDialog.show = false
    await load()
    if (data.reindex_warning) ElMessage.warning(data.reindex_warning)
    else ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    noteDialog.saving = false
  }
}

async function deleteNote() {
  await noteApi.remove(noteDialog.id)
  noteDialog.show = false
  await load()
  ElMessage.success('已删除')
}

const addingReview = ref(false)
async function addToReview() {
  addingReview.value = true
  try {
    const { data } = await reviewApi.createCard(noteDialog.id)
    ElMessage.success(data.created ? `已加入复习：「${data.question.slice(0, 24)}…」` : '该笔记已在复习队列中')
  } catch (e) {
    ElMessage.error(errMsg(e, '加入复习失败'))
  } finally {
    addingReview.value = false
  }
}

const addingRecall = ref(false)
async function addToRecall() {
  addingRecall.value = true
  try {
    const { data } = await reviewApi.createRecallCard(noteDialog.id)
    ElMessage.success(data.created ? `已生成复述卡：「${data.question.slice(0, 24)}…」` : '该笔记已有复述卡')
  } catch (e) {
    ElMessage.error(errMsg(e, '生成复述卡失败'))
  } finally {
    addingRecall.value = false
  }
}

const splittingReview = ref(false)
async function splitToReview() {
  splittingReview.value = true
  try {
    const { data } = await reviewApi.split(noteDialog.id)
    ElMessage.success(`已拆成 ${data.created} 张复习卡`)
  } catch (e) {
    ElMessage.error(errMsg(e, '拆卡失败'))
  } finally {
    splittingReview.value = false
  }
}

// ---------- 笔记加工：改写/扩写/续写/总结 ----------
// 笔记内容选区：选中文字后浮出加工工具
function onNoteSelect(e) {
  const el = e.target
  if (!el || typeof el.selectionStart !== 'number') return
  const start = el.selectionStart, end = el.selectionEnd
  const text = el.value.substring(start, end)
  if (!text.trim()) { noteSel.show = false; noteSel.text = ''; return }
  noteSel.text = text
  noteSel.start = start
  noteSel.end = end
  noteSel.show = true
}
function hideNoteSel() { noteSel.show = false; noteSel.text = '' }

async function noteTransform(mode, sel) {
  const content = sel ? sel.text : noteDialog.content?.trim()
  if (!content) { ElMessage.warning(sel ? '请先选中文字' : '笔记还没有内容'); return }
  transformDialog.mode = mode
  transformDialog.original = content
  transformDialog.result = ''
  transformDialog.sel = sel ? { start: sel.start, end: sel.end } : null
  transformDialog.streaming = true
  transformDialog.show = true
  noteSel.show = false
  try {
    await streamSSE('/api/ai/note/transform/stream',
      { content, mode, title: noteDialog.title },
      (t) => { transformDialog.result += t },
      () => {},
      (msg) => { throw new Error(msg) },
    )
  } catch (e) {
    ElMessage.error(e.message || '加工失败')
    transformDialog.show = false
  } finally {
    transformDialog.streaming = false
  }
}

function adoptTransform() {
  if (!transformDialog.result.trim()) { ElMessage.warning('结果为空，无法采用'); return }
  if (transformDialog.sel) {
    const { start, end } = transformDialog.sel
    if (transformDialog.mode === 'continue') {
      // 续写：保留原选段，续写内容追加在选区之后
      noteDialog.content = noteDialog.content.substring(0, end) + '\n\n' + transformDialog.result + noteDialog.content.substring(end)
    } else {
      noteDialog.content = noteDialog.content.substring(0, start) + transformDialog.result + noteDialog.content.substring(end)
    }
  } else if (transformDialog.mode === 'continue') {
    // 整条续写：原文保留，续写内容追加到末尾
    noteDialog.content = noteDialog.content.trimEnd() + '\n\n' + transformDialog.result
  } else {
    noteDialog.content = transformDialog.result
  }
  transformDialog.show = false
  ElMessage.success(transformDialog.mode === 'continue' ? '已续写，记得保存' : '已采用，记得保存')
}

const tab = ref('materials')
const search = ref('')
const loading = ref(false)
const rebuilding = ref(null)
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
const NOTE_TAG = {
  chat: '问答笔记', ai_asset: 'AI 生成',
  weekly_report: 'AI 周报', podcast_script: 'AI 播客脚本',
  podcast_brief: 'AI 播客简报',
}
const noteTagLabel = (t) => NOTE_TAG[t] || '手动'
const noteTagClass = (t) => ({
  'is-ai': t === 'ai_asset', 'is-chat': t === 'chat',
  'is-report': t === 'weekly_report', 'is-podcast': t === 'podcast_script',
  'is-brief': t === 'podcast_brief',
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

/* ===== 工具条：tabs + 右侧搜索 ===== */
.kb-tabs-wrap { position: relative; }
.kb-search { position: absolute; right: 0; top: 1px; width: 220px; z-index: 2; }
.kb-tabs :deep(.el-tabs__item) { font-size: 14px; padding: 0 18px; }
.kb-tabs :deep(.el-tabs__content) { padding-top: 16px; }

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
  font-size: 12px; font-weight: 500; line-height: 1;
  padding: 5px 10px; border-radius: 999px;
}
.kb-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.kb-status.ok { color: #0f6e56; background: rgba(15, 110, 86, .08); }
.kb-status.warn { color: #ba7517; background: rgba(186, 117, 23, .09); }
.kb-status.muted { color: var(--asc-text-3); background: var(--asc-surface-2); }

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

/* 空状态图标圆角 */
:deep(.el-empty__image) { border-radius: 16px; }

/* ===== 卡片错峰入场（尊重 reduced-motion） ===== */
@keyframes kb-card-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
.card-in { animation: kb-card-in .32s ease backwards; }
@media (prefers-reduced-motion: reduce) {
  .card-in { animation: none; }
  .kb-note-card, .kb-stat.pending, .kb-note-card::before { transition: none; }
}

/* ===== 笔记弹窗（与资料详情页 note-dialog 一致） ===== */
.note-dialog :deep(.el-dialog__header) { padding: 20px 24px 14px; margin: 0; }
.note-dialog :deep(.el-dialog__body) { padding: 6px 24px 4px; }
.note-dialog :deep(.el-dialog__footer) { padding: 14px 24px 20px; border-top: 1px solid var(--asc-divider); }

.nd-header { display: flex; flex-direction: column; gap: 6px; }
.nd-title-row { display: flex; align-items: center; gap: 10px; }
.nd-title { font-size: 17px; font-weight: 600; color: var(--asc-text); }
.nd-src-tag {
  font-size: 11px; font-weight: 500; line-height: 1; padding: 4px 8px; border-radius: 6px;
  background: var(--asc-surface-2); color: var(--asc-text-2); letter-spacing: .5px;
}
.nd-src-tag.is-ai { background: var(--asc-primary-soft); color: var(--asc-primary); }
.nd-src-tag.is-chat { background: rgba(28, 145, 138, .12); color: #12837c; }
.nd-src-tag.is-report { background: rgba(217, 119, 6, .12); color: #b45309; }
.nd-src-tag.is-podcast { background: rgba(74, 114, 212, .12); color: #3f63c4; }
.nd-src-tag.is-brief { background: rgba(162, 28, 175, .12); color: #a21caf; }
.nd-sub { font-size: 12px; color: var(--asc-text-3); }

.nd-body { display: flex; flex-direction: column; gap: 16px; }
.nd-field { display: flex; flex-direction: column; gap: 8px; }
.nd-label { font-size: 13px; font-weight: 500; color: var(--asc-text-2); }
.nd-field-head { display: flex; align-items: center; justify-content: space-between; }

.nd-seg {
  display: inline-flex; padding: 3px; gap: 2px;
  background: var(--asc-surface-2); border-radius: 8px;
}
.nd-seg-btn {
  border: none; background: transparent; cursor: pointer;
  font-size: 12px; font-weight: 500; color: var(--asc-text-2);
  padding: 5px 14px; border-radius: 6px; line-height: 1.4;
  transition: all .16s ease;
}
.nd-seg-btn:hover { color: var(--asc-text); }
.nd-seg-btn.active {
  background: var(--asc-card); color: var(--asc-primary);
  box-shadow: 0 1px 4px rgba(0, 0, 0, .08);
}

.nd-preview {
  background: var(--asc-surface-2); border-radius: 8px;
  padding: 14px 16px; min-height: 220px; max-height: 360px; overflow-y: auto;
  font-size: 14px;
}
.nd-count {
  align-self: flex-end; font-size: 11px; color: var(--asc-text-3);
  margin-top: 2px; font-variant-numeric: tabular-nums;
}

/* AI 加工面板：浅紫卡片，图标 + 标签 + 三动作 */
.nd-ai {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin-top: 8px;
  padding: 9px 12px;
  background: var(--asc-primary-soft);
  border: 1px solid rgba(124, 92, 252, .14);
  border-radius: 10px;
}
.nd-ai-icon { display: inline-flex; color: var(--asc-primary); line-height: 0; }
.nd-ai-label { font-size: 12px; font-weight: 500; color: var(--asc-primary); }
.nd-ai-sep { width: 1px; height: 12px; background: rgba(124, 92, 252, .22); margin: 0 2px; }

/* 原生 textarea 复刻 el-textarea 视觉（选区捕获需原生元素） */
.nd-textarea {
  width: 100%; border: none; outline: none; resize: vertical;
  background: var(--asc-card); box-shadow: 0 0 0 1px var(--asc-border) inset;
  border-radius: 6px; padding: 8px 12px;
  font-family: inherit; font-size: 14px; line-height: 1.6; color: var(--asc-text);
  transition: box-shadow .18s ease;
}
.nd-textarea:focus { box-shadow: 0 0 0 1.5px var(--asc-primary) inset; }

/* 选区加工工具条：选中文字后浮出 */
.nd-sel-bar {
  display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
  padding: 7px 10px;
  background: var(--asc-surface-2); border-radius: 8px;
}
.nd-sel-count { font-size: 12px; color: var(--asc-text-2); }
.nd-sel-sep { width: 1px; height: 12px; background: var(--asc-border); margin: 0 2px; }

.nd-shortcuts {
  display: flex; align-items: center; gap: 2px; flex-wrap: wrap;
  margin-top: 12px; padding: 12px 0 0; border-top: 1px dashed var(--asc-divider);
}
.nd-sep { width: 1px; height: 14px; background: var(--asc-divider); margin: 0 6px; flex-shrink: 0; }

/* 加工结果对比弹窗（与资料详情页一致） */
.polish-block { margin-bottom: 14px; }
.polish-label {
  font-size: 11px; font-weight: 600; letter-spacing: 1.5px;
  color: var(--asc-text-3); margin-bottom: 6px;
}
.polish-block-new .polish-label { color: #0f6e56; }
.polish-text {
  font-size: 14px; line-height: 1.75; color: var(--asc-text);
  background: var(--asc-surface-2); border-radius: 8px;
  padding: 12px 14px; white-space: pre-wrap;
  max-height: 240px; overflow-y: auto;
}
.polish-block-new .polish-text { background: rgba(15, 110, 86, .07); }
.stream-cursor { color: var(--asc-primary); animation: cursor-blink 1s step-end infinite; }
@keyframes cursor-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

.nd-footer { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
.nd-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
</style>
