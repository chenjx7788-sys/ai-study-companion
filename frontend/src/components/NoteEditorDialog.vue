<!--
  笔记编辑弹窗（共用组件 · 全屏 Vditor 版）

  为什么存在：知识库页 / 学习页 / 统计页 / 播客页都要能「查看+编辑」同一条笔记，
  抽成组件避免第 4 份副本。2026-09-22 升级为全屏 + Vditor（所见即所得），
  与材料正文编辑器（EditorView）同一内核、同一排版（.md-body 15px/1.9）。

  用法（编辑已有笔记）：
    <NoteEditorDialog v-model="show" :note-id="id" :material-title="title" @changed="reload" />

  用法（新建笔记，父组件持有 material 上下文）：
    <NoteEditorDialog v-model="show" :create-draft="{ title, content, anchor }"
      :create-handler="onCreate" @changed="reload" />

  - v-model：弹窗显隐
  - note-id：笔记 id（打开时按 id 拉全文，含 anchor）；与 create-draft 二选一
  - create-draft / create-handler：新建模式的初始内容与保存回调（父组件决定 material_id）
  - jump-handler：可选，(anchor, materialId) => void；缺省时组件内 router.push 到学习页
  - @changed：保存 / 删除后触发，父组件应刷新自己的笔记列表
-->
<template>
  <el-dialog :model-value="modelValue" fullscreen class="note-editor-full" :close-on-click-modal="false" :show-close="false"
    :before-close="(done) => guardClose(done)"
    @update:model-value="(v) => emit('update:modelValue', v)" @open="onOpen" @closed="onClosed">
    <template #header>
      <div class="ne-header">
        <span class="ne-title">{{ note.id ? '编辑笔记' : '新建笔记' }}</span>
        <span class="nd-src-tag" :class="noteTagClass(note.sourceType)">
          {{ noteTagLabel(note.sourceType) }}
        </span>
        <span class="ne-save-state" :class="'st-' + saveState">
          <i class="ne-dot" aria-hidden="true"></i>{{ saveState === 'saving' ? '保存中…' : (saveState === 'unsaved' ? '未保存' : '已保存') }}
        </span>
        <div v-if="note.id" class="ne-shortcuts">
          <el-button v-if="note.materialId && note.anchor?.page_no" text size="small" @click="jumpToOriginal">
            跳转原文 P{{ note.anchor.page_no }}
          </el-button>
          <el-tooltip content="把这条笔记出成选择题，进复习队列定期重考（再认）" placement="bottom" :show-after="250">
            <el-button text type="warning" size="small" :loading="addingReview" @click="addToReview">加入复习</el-button>
          </el-tooltip>
          <el-tooltip content="出成引导题，先自己讲一遍再对照答案，练主动回忆（费曼）" placement="bottom" :show-after="250">
            <el-button text type="primary" size="small" :loading="addingRecall" @click="addToRecall">生成复述卡</el-button>
          </el-tooltip>
          <el-button v-if="note.content.length > 200" text type="warning" size="small"
            :loading="splittingReview" @click="splitToReview">拆成多卡</el-button>
          <el-popconfirm title="删除这条笔记？不可恢复"
            confirm-button-text="删除" confirm-button-type="danger" cancel-button-text="取消"
            @confirm="deleteNote">
            <template #reference>
              <el-button type="danger" text size="small">删除</el-button>
            </template>
          </el-popconfirm>
        </div>
        <div class="ne-actions-top">
          <el-button @click="onCancel">取消</el-button>
          <el-button type="primary" :loading="note.saving" @click="saveNote">保存</el-button>
        </div>
      </div>
    </template>

    <div class="ne-body">
      <el-input v-model="note.title" placeholder="给这条笔记起个标题" maxlength="80" class="ne-title-input" />
      <div v-if="note.materialTitle || note.anchor?.page_no || note.id" class="ne-source">
        <template v-if="note.materialTitle">来自《{{ note.materialTitle }}》</template>
        <template v-else-if="note.id">材料无关笔记</template>
        <template v-if="note.anchor?.page_no"> · 第 {{ note.anchor.page_no }} 页</template>
      </div>

      <div class="ne-air">
        <div class="ne-ai">
          <svg class="ne-ai-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 2l1.7 5.3L19 9l-5.3 1.7L12 16l-1.7-5.3L5 9l5.3-1.7L12 2z"/>
            <path d="M19 14l.9 2.6 2.6.9-2.6.9L19 21l-.9-2.6-2.6-.9 2.6-.9L19 14z"/>
          </svg>
          <span class="ne-ai-label">AI 加工</span>
          <el-tooltip content="换个说法，保持原意与事实" placement="bottom" :show-after="250">
            <el-button text size="small" @click="noteTransform('rewrite')">改写</el-button>
          </el-tooltip>
          <el-tooltip content="补充细节、例子与解释，让笔记更充实" placement="bottom" :show-after="250">
            <el-button text size="small" @click="noteTransform('expand')">扩写</el-button>
          </el-tooltip>
          <el-tooltip content="接着已有内容往下续写，补全或延伸思路" placement="bottom" :show-after="250">
            <el-button text size="small" @click="noteTransform('continue')">续写</el-button>
          </el-tooltip>
          <el-tooltip content="压缩成精炼要点，突出核心结论" placement="bottom" :show-after="250">
            <el-button text size="small" @click="noteTransform('summarize')">总结</el-button>
          </el-tooltip>
        </div>
      </div>

      <div ref="editorEl" class="ne-vditor"></div>

      <div class="ne-meta">
        <span class="ne-count">{{ wordCount }} 字</span>
      </div>
    </div>
  </el-dialog>

  <!-- 选段工具条：选中文字后浮出（格式 + AI 加工） -->
  <div v-if="selBar.show && !selBar.streaming" class="sel-bar" :style="{ left: selBar.x + 'px', top: selBar.y + 'px' }">
    <button class="fmt-btn" title="加粗" @mousedown.prevent @click="applyFormat('bold')"><b>B</b></button>
    <button class="fmt-btn" title="斜体" @mousedown.prevent @click="applyFormat('italic')"><i>I</i></button>
    <button class="fmt-btn" title="删除线" @mousedown.prevent @click="applyFormat('strike')"><s>S</s></button>
    <button class="fmt-btn" title="行内代码" @mousedown.prevent @click="applyFormat('code')">`</button>
    <button class="fmt-btn" title="标题" @mousedown.prevent @click="applyFormat('heading')">H</button>
    <button class="fmt-btn" title="引用" @mousedown.prevent @click="applyFormat('quote')">&ldquo;</button>
    <button class="fmt-btn" title="列表" @mousedown.prevent @click="applyFormat('list')">&bull;</button>
    <span class="fmt-sep"></span>
    <el-button size="small" type="primary" @mousedown.prevent @click="startSelTransform('rewrite')">改写</el-button>
    <el-button size="small" type="primary" @mousedown.prevent @click="startSelTransform('expand')">扩写</el-button>
    <el-button size="small" type="primary" @mousedown.prevent @click="startSelTransform('continue')">续写</el-button>
    <el-button size="small" type="primary" @mousedown.prevent @click="startSelTransform('summarize')">总结</el-button>
  </div>

  <!-- 笔记加工结果弹窗（原文 vs 结果，采用/放弃） -->
  <el-dialog v-model="transformDialog.show" :title="transformTitle" width="560px" class="note-dialog">
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
      <el-button type="primary" :disabled="transformDialog.streaming" @click="adoptTransform">
        {{ transformDialog.mode === 'continue' ? '插入' : '采用' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import Vditor from 'vditor'
import 'vditor/dist/index.css'
import { noteApi, reviewApi } from '../api'
import http, { errMsg } from '../api/http'
import { streamSSE } from '../utils/sse'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  noteId: { type: [Number, String], default: null },
  // 可选：材料标题（材料笔记用；材料无关笔记留空）
  materialTitle: { type: String, default: '' },
  // 新建模式：初始内容（与 noteId 二选一）
  createDraft: { type: Object, default: null },
  // 新建模式：保存回调 async ({ title, content }) —— 父组件决定 material_id / source_type
  createHandler: { type: Function, default: null },
  // 可选：自定义「跳转原文」行为（如学习页内定位）；缺省 router.push 到学习页
  jumpHandler: { type: Function, default: null },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const router = useRouter()

const note = reactive({
  id: null, title: '', content: '', materialTitle: '',
  materialId: null, sourceType: 'manual', anchor: null, saving: false,
})
const editorEl = ref(null)
const vditor = ref(null)
const wordCount = ref(0)
const saveState = ref('saved')   // saved / unsaved / saving

let initialTitle = ''
let initialContent = ''
let editorReady = false   // Vditor 初始化完成后再标记「未保存」
let draftTimer = null

// ---------- 来源标签（与知识库页同一套文案/配色） ----------
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

// ---------- 草稿（localStorage）：30s 定时 + 关闭时落盘 ----------
const draftKey = computed(() =>
  note.id ? `asc-note-draft-${note.id}` : `asc-note-draft-new-${note.materialId || props.createDraft?.materialId || 'global'}`)
function getContent() { return (vditor.value?.getValue() || '').trim() }
function hasChanges() { return note.title.trim() !== initialTitle.trim() || getContent() !== initialContent }
function markUnsaved() { if (editorReady) saveState.value = 'unsaved' }
function saveDraft() {
  const content = getContent()
  if (!note.title.trim() && !content) return
  try {
    localStorage.setItem(draftKey.value, JSON.stringify({ title: note.title, content, ts: Date.now() }))
  } catch (e) { /* 忽略容量/隐私错误 */ }
}
function loadDraft() {
  try {
    const raw = localStorage.getItem(draftKey.value)
    return raw ? JSON.parse(raw) : null
  } catch (e) { return null }
}
function clearDraft() { localStorage.removeItem(draftKey.value) }

// ---------- 打开 / 关闭 ----------
watch(() => props.modelValue, (v) => {
  if (!v) return
  // 先清空再拉，避免闪现上一条笔记的内容
  note.id = props.noteId != null ? Number(props.noteId) : null
  note.title = props.noteId == null ? (props.createDraft?.title || '') : ''
  note.content = ''
  note.materialTitle = props.materialTitle || ''
  note.materialId = props.createDraft?.materialId || null
  note.sourceType = props.createDraft?.sourceType || 'manual'
  note.anchor = props.createDraft?.anchor || null
  saveState.value = 'saved'
  if (props.noteId != null) loadNote(props.noteId)
})

async function loadNote(id) {
  try {
    // ⚠️ 响应变量不要叫 data（避免与父组件作用域语义混淆，且本项目 axios 无拦截器）
    const res = await http.get(`/notes/${id}`)
    const d = res.data
    note.id = d.id
    note.title = d.title
    note.content = d.content
    note.sourceType = d.source_type || 'manual'
    note.materialId = d.material_id
    note.materialTitle = props.materialTitle || ''
    note.anchor = d.anchor || null
    // 回填编辑器：不依赖 editorReady 时序（见 onOpen 竞态说明）。
    // Vditor 可能还没初始化完（此时 after 会用 note.content 兜底），
    // 也可能已就绪（此时直接 setValue 覆盖 after 里的空 initial）。
    if (vditor.value) {
      setInitial(d.content)
      try { vditor.value.setValue(d.content || '') } catch { /* 未就绪时忽略，after 兜底 */ }
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '笔记打开失败，可能已被删除'))
    emit('update:modelValue', false)
  }
}

function setInitial(content) {
  initialTitle = note.title
  initialContent = (content || '').trim()
  wordCount.value = (content || '').length
}

// el-dialog @open：DOM 已渲染，此时初始化 Vditor（每次打开重建，closed 时销毁）
async function onOpen() {
  await nextTick()
  if (vditor.value) { vditor.value.destroy(); vditor.value = null }
  editorReady = false

  // 初始内容：编辑模式等 loadNote 回填（若已返回则用已有）；新建模式用 createDraft
  let initial = props.noteId == null ? (props.createDraft?.content || '') : note.content

  // 检测上次未保存的草稿，询问是否恢复（草稿与原文一致则直接清除）
  const draft = loadDraft()
  if (draft && draft.content && draft.content !== initial) {
    try {
      await ElMessageBox.confirm('检测到上次未保存的草稿，是否恢复？', '恢复草稿', {
        confirmButtonText: '恢复', cancelButtonText: '丢弃', type: 'info',
      })
      note.title = draft.title || note.title
      initial = draft.content
    } catch {
      clearDraft()
    }
  } else if (draft) {
    clearDraft()
  }

  setInitial(initial)
  if (props.noteId == null) initialTitle = note.title

  vditor.value = new Vditor(editorEl.value, {
    mode: 'wysiwyg',
    // ⚠️⚠️ 必须显式指定 cdn（同 EditorView）：默认会运行时从 unpkg 拉资源，离线即失效
    cdn: '/vditor',
    height: Math.max(360, window.innerHeight - 300),
    cache: { enable: false },
    placeholder: '支持 Markdown，可直接粘贴…',
    // 全屏弹窗里 outline/fullscreen 冗余（弹窗本身已全屏）→ 不启用
    toolbar: ['headings', 'bold', 'italic', 'strike', '|', 'list', 'ordered-list', 'check', '|',
      'quote', 'code', 'inline-code', 'link', 'table', '|', 'line', '|',
      'undo', 'redo'],
    input: (value) => {
      wordCount.value = (value || '').length
      markUnsaved()
    },
    after: () => {
      // ⚠️ 竞态修复：编辑模式 loadNote 是异步的，可能在 after 之后才返回。
      //    旧实现闭包捕获 initial（此时还是空串），导致「打开没内容，需重开才有」。
      //    这里优先读 note.content（reactive 最新值）——loadNote 若已返回即为全文；
      //    若尚未返回，loadNote 内部会再 setValue 覆盖（见 loadNote 的无条件回填）。
      const content = (props.noteId == null ? initial : (note.content || initial || ''))
      if (content) vditor.value.setValue(content)
      vditor.value.focus()
      editorReady = true
    },
  })

  document.addEventListener('mouseup', onVditorSelect)
  document.addEventListener('keyup', onVditorSelect)
  document.addEventListener('keydown', onKeydown, true)
  draftTimer = setInterval(saveDraft, 30000)
}

function onClosed() {
  document.removeEventListener('mouseup', onVditorSelect)
  document.removeEventListener('keyup', onVditorSelect)
  document.removeEventListener('keydown', onKeydown, true)
  if (draftTimer) { clearInterval(draftTimer); draftTimer = null }
  if (saveState.value === 'unsaved') saveDraft()   // 仅未保存时落盘（保存后 saveState=saved，不误存）
  hideSelBar()
  transformDialog.show = false
  editorReady = false
  if (vditor.value) { vditor.value.destroy(); vditor.value = null }
}

onBeforeUnmount(() => { if (vditor.value) { vditor.value.destroy(); vditor.value = null } })

// 未保存关闭守卫：确认后存草稿再关（标题栏 X / Esc / 遮罩与「取消」按钮共用）
function guardClose(done) {
  if (!hasChanges()) { done(); return }
  ElMessageBox.confirm('内容尚未保存，关闭后将存入本地草稿，下次打开可恢复。', '关闭笔记？', {
    confirmButtonText: '存草稿并关闭', cancelButtonText: '继续编辑', type: 'warning',
  }).then(() => { saveDraft(); done() }).catch(() => {})
}
function onCancel() { guardClose(() => emit('update:modelValue', false)) }

// Ctrl/Cmd+S 保存
function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    saveNote()
  }
}

// ---------- 保存 / 删除 ----------
async function saveNote() {
  const content = getContent()
  if (!note.title.trim()) { ElMessage.warning('请填写标题'); return }
  if (!content) { ElMessage.warning('正文为空'); return }
  note.saving = true
  saveState.value = 'saving'
  try {
    if (note.id) {
      const res = await noteApi.update(note.id, { title: note.title.trim(), content })
      note.content = content
      if (res.data?.reindex_warning) ElMessage.warning(res.data.reindex_warning)
      else ElMessage.success('已保存')
    } else if (props.createHandler) {
      await props.createHandler({ title: note.title.trim(), content })
      note.content = content
      ElMessage.success('已保存')
    } else {
      ElMessage.warning('当前环境不支持新建笔记')
      return
    }
    initialTitle = note.title.trim()
    initialContent = content
    clearDraft()
    saveState.value = 'saved'
    emit('update:modelValue', false)
    emit('changed')
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
    saveState.value = 'unsaved'
  } finally {
    note.saving = false
  }
}

async function deleteNote() {
  try {
    await noteApi.remove(note.id)
    clearDraft()
    emit('update:modelValue', false)
    emit('changed')
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  }
}

// ---------- 快捷操作 ----------
function jumpToOriginal() {
  const anchor = note.anchor
  if (!note.materialId || !anchor?.page_no) return
  emit('update:modelValue', false)
  if (props.jumpHandler) { props.jumpHandler(anchor, note.materialId); return }
  const q = { page: anchor.page_no }
  if (anchor.selected_text) q.hl = anchor.selected_text
  router.push({ path: `/study/${note.materialId}`, query: q })
}

const addingReview = ref(false)
const addingRecall = ref(false)
const splittingReview = ref(false)
async function addToReview() {
  addingReview.value = true
  try {
    const res = await reviewApi.createCard(note.id)
    ElMessage.success(res.data.created ? `已加入复习：「${res.data.question.slice(0, 24)}…」` : '该笔记已在复习队列中')
  } catch (e) {
    ElMessage.error(errMsg(e, '加入复习失败'))
  } finally { addingReview.value = false }
}
async function addToRecall() {
  addingRecall.value = true
  try {
    const res = await reviewApi.createRecallCard(note.id)
    ElMessage.success(res.data.created ? `已生成复述卡：「${res.data.question.slice(0, 24)}…」` : '该笔记已有复述卡')
  } catch (e) {
    ElMessage.error(errMsg(e, '生成复述卡失败'))
  } finally { addingRecall.value = false }
}
async function splitToReview() {
  splittingReview.value = true
  try {
    const res = await reviewApi.split(note.id)
    ElMessage.success(`已拆成 ${res.data.created} 张复习卡`)
  } catch (e) {
    ElMessage.error(errMsg(e, '拆卡失败'))
  } finally { splittingReview.value = false }
}

// ---------- 选区（Vditor 内）浮动工具条 ----------
const selBar = reactive({ show: false, x: 0, y: 0, text: '', streaming: false })
function hideSelBar() { selBar.show = false; selBar.text = ''; selBar.streaming = false }

function onVditorSelect() {
  if (selBar.streaming || transformDialog.show) return
  const el = editorEl.value
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || !el || !sel.anchorNode || !el.contains(sel.anchorNode)) {
    if (selBar.show) hideSelBar()
    return
  }
  const text = vditor.value?.getSelection()?.trim()
  if (!text) { if (selBar.show) hideSelBar(); return }
  const rect = sel.getRangeAt(0).getBoundingClientRect()
  selBar.text = text
  selBar.x = Math.max(8, Math.min(rect.left, window.innerWidth - 340))
  selBar.y = Math.max(64, rect.top - 46)
  selBar.show = true
}

// 选中文字应用行内格式：用 markdown 语法包装后 updateValue 原地替换（光标不动）
function applyFormat(type) {
  const text = selBar.text
  if (!text) return
  let out = text
  switch (type) {
    case 'bold': out = `**${text}**`; break
    case 'italic': out = `*${text}*`; break
    case 'strike': out = `~~${text}~~`; break
    case 'code': out = '`' + text + '`'; break
    case 'heading': out = `\n## ${text}\n`; break
    case 'quote': out = text.split('\n').map(l => `> ${l}`).join('\n'); break
    case 'list': out = text.split('\n').map(l => `- ${l}`).join('\n'); break
  }
  vditor.value.updateValue(out)
  hideSelBar()
  vditor.value.focus()
  markUnsaved()   // 程序化替换不依赖 input 回调 → 显式标记
}

// ---------- 笔记加工：改写/扩写/续写/总结 ----------
const TRANSFORM_LABELS = { rewrite: '改写', expand: '扩写', continue: '续写', summarize: '总结' }
const transformDialog = reactive({ show: false, mode: 'rewrite', original: '', result: '', streaming: false, sel: null })
const transformTitle = computed(() => {
  const act = TRANSFORM_LABELS[transformDialog.mode] || '加工'
  return transformDialog.sel ? `${act}选中文字` : `${act}笔记`
})

async function noteTransform(mode) {
  const content = getContent()
  if (!content) { ElMessage.warning('笔记还没有内容'); return }
  transformDialog.mode = mode
  transformDialog.original = content
  transformDialog.result = ''
  transformDialog.sel = null
  transformDialog.streaming = true
  transformDialog.show = true
  try {
    await streamSSE('/api/ai/note/transform/stream',
      { content, mode, title: note.title },
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

async function startSelTransform(mode) {
  const text = selBar.text
  if (!text) { hideSelBar(); return }
  transformDialog.mode = mode
  transformDialog.original = text
  transformDialog.result = ''
  transformDialog.sel = { text }
  transformDialog.streaming = true
  transformDialog.show = true
  hideSelBar()
  try {
    await streamSSE('/api/ai/note/transform/stream',
      { content: text, mode, title: note.title },
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
    if (transformDialog.mode === 'continue') {
      // 续写：保留原选段，续写内容追加在其后
      vditor.value.updateValue(transformDialog.sel.text + '\n\n' + transformDialog.result)
    } else {
      vditor.value.updateValue(transformDialog.result)   // 原生替换选区，光标保持在原位
    }
  } else if (transformDialog.mode === 'continue') {
    vditor.value.insertValue(transformDialog.result)   // 整条续写：插入光标处
  } else {
    vditor.value.setValue(transformDialog.result)      // 整篇加工：覆盖全文
  }
  transformDialog.show = false
  vditor.value.focus()
  markUnsaved()   // setValue/insertValue 不触发 input 回调 → 必须显式标记，否则直接关闭即丢
  ElMessage.success(transformDialog.mode === 'continue' ? '已续写，记得保存' : '已采用，记得保存')
}
</script>

<style scoped>
/* ===== 全屏笔记编辑器：纸面感 · 去框感 · 层级靠留白 ===== */
/* 弹窗底色用页面灰，编辑区一张白卡浮在上面 */
.note-editor-full :deep(.el-dialog) { display: flex; flex-direction: column; background: var(--asc-bg); }
.note-editor-full :deep(.el-dialog__header) { padding: 16px 28px 0; margin: 0; }
.note-editor-full :deep(.el-dialog__body) {
  padding: 0 28px; flex: 1; display: flex; flex-direction: column; overflow: hidden;
}

/* 顶栏：一行收完（名称 + 标签 + 保存状态 | 快捷操作 | 取消/保存）；无右上角 X（:show-close=false），Esc/取消走同一关闭守卫 */
.ne-header { display: flex; align-items: center; gap: 10px; min-height: 32px; }
.ne-title { font-size: 14px; font-weight: 600; color: var(--asc-text-2); letter-spacing: .2px; }
.nd-src-tag {
  font-size: 11px; font-weight: 500; line-height: 1; padding: 3px 7px; border-radius: 999px;
  background: var(--asc-surface-2); color: var(--asc-text-3); letter-spacing: .3px;
}
.nd-src-tag.is-ai { background: var(--asc-primary-soft); color: var(--asc-primary); }
.nd-src-tag.is-chat { background: rgba(28, 145, 138, .12); color: #12837c; }
.nd-src-tag.is-report { background: rgba(217, 119, 6, .12); color: #b45309; }
.nd-src-tag.is-podcast { background: rgba(74, 114, 212, .12); color: #3f63c4; }
.nd-src-tag.is-brief { background: rgba(162, 28, 175, .12); color: #a21caf; }
.ne-save-state {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--asc-text-3); white-space: nowrap;
}
.ne-dot { width: 6px; height: 6px; border-radius: 50%; background: #0f6e56; flex-shrink: 0; }
.ne-save-state.st-unsaved { color: #ba7517; }
.ne-save-state.st-unsaved .ne-dot { background: #ba7517; }
.ne-save-state.st-saving .ne-dot { background: var(--asc-primary); animation: ne-pulse 1s ease infinite; }
@keyframes ne-pulse { 50% { opacity: .35; } }
/* 快捷操作：固定在顶栏（保存状态之后、取消/保存之前） */
.ne-shortcuts { margin-left: auto; display: flex; align-items: center; gap: 2px; flex-wrap: nowrap; flex-shrink: 0; }
.ne-actions-top { display: flex; align-items: center; gap: 8px; flex-shrink: 0; margin-left: 12px; }

/* 内容栏：居中 920px；标题/来源与编辑器卡片左边缘严格对齐 */
.ne-body {
  flex: 1; display: flex; flex-direction: column;
  width: 100%; max-width: 920px; margin: 0 auto; min-height: 0;
  padding: 10px 0 16px;
}
.ne-title-input :deep(.el-input__wrapper) {
  box-shadow: none !important; background: transparent !important;
  padding: 0; border-radius: 8px;
  transition: background .18s ease;
}
.ne-title-input :deep(.el-input__wrapper:hover) { background: var(--asc-surface-2) !important; }
.ne-title-input :deep(.el-input__inner) {
  font-size: 26px; font-weight: 700; height: 52px; color: var(--asc-text); letter-spacing: -0.3px;
}
.ne-title-input :deep(.el-input__inner)::placeholder { color: var(--asc-text-3); font-weight: 500; }

/* 来源信息：紧跟标题下方，弱化，与编辑器左边缘对齐 */
.ne-source { padding: 0 0 4px; font-size: 12px; color: var(--asc-text-3); }

/* AI 加工：低调文字按钮组，右对齐，不抢注意力（原紫色横幅已降噪） */
.ne-air { display: flex; justify-content: flex-end; padding: 2px 8px 8px; }
.ne-ai { display: flex; align-items: center; gap: 0; }
.ne-ai-icon { width: 12px; height: 12px; color: var(--asc-primary); margin-right: 2px; }
.ne-ai-label { font-size: 12px; font-weight: 500; color: var(--asc-text-3); margin-right: 4px; }
.ne-ai :deep(.el-button) { color: var(--asc-text-2); }
.ne-ai :deep(.el-button:hover) { color: var(--asc-primary); }

/* 编辑器白卡：单层细边，聚焦时主色边；阴影去掉（纸面感来自底色差，不靠投影） */
.ne-vditor {
  flex: 1; min-height: 0;
  background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 12px;
  overflow: hidden;
  transition: border-color .18s ease;
}
.ne-vditor:focus-within { border-color: rgba(124, 92, 252, .45); }
.ne-vditor :deep(.vditor-toolbar) {
  border-bottom: 1px solid var(--asc-divider); background: var(--asc-card);
  padding-left: 12px !important; padding-right: 12px !important;
}

/* ===== Vditor 正文排版：向 .md-body（材料正文）看齐（15px/1.9） ===== */
.ne-vditor :deep(.vditor-reset) {
  font-size: 15px; line-height: 1.9; color: #363632;
  padding: 20px 28px;
}
.ne-vditor :deep(.vditor-reset h1) { font-size: 22px; font-weight: 600; margin: 24px 0 12px; line-height: 1.4; }
.ne-vditor :deep(.vditor-reset h2) {
  font-size: 18px; font-weight: 600; margin: 22px 0 10px;
  padding-left: 12px; border-left: 3px solid var(--asc-primary); line-height: 1.4;
}
.ne-vditor :deep(.vditor-reset h3) { font-size: 16px; font-weight: 600; margin: 18px 0 8px; line-height: 1.4; }
.ne-vditor :deep(.vditor-reset p) { margin: 10px 0; }
.ne-vditor :deep(.vditor-reset ul), .ne-vditor :deep(.vditor-reset ol) { padding-left: 24px; margin: 10px 0; }
.ne-vditor :deep(.vditor-reset li) { margin: 5px 0; }
.ne-vditor :deep(.vditor-reset li::marker) { color: var(--asc-primary); }
.ne-vditor :deep(.vditor-reset strong) { font-weight: 600; }
.ne-vditor :deep(.vditor-reset code) { background: var(--asc-surface-2); padding: 2px 6px; border-radius: 4px; font-size: 13px; }
.ne-vditor :deep(.vditor-reset pre) { background: var(--asc-surface-2); padding: 14px 16px; border-radius: 8px; overflow-x: auto; }
.ne-vditor :deep(.vditor-reset pre code) { background: transparent; padding: 0; }
.ne-vditor :deep(.vditor-reset blockquote) {
  margin: 12px 0; padding: 8px 14px; border-left: 3px solid var(--asc-border);
  background: var(--asc-surface-2); border-radius: 0 8px 8px 0; color: var(--asc-text-2);
}
.ne-vditor :deep(.vditor-reset hr) { border: none; border-top: 1px solid var(--asc-divider); margin: 20px 0; }
.ne-vditor :deep(.vditor-reset a) {
  color: var(--asc-primary); text-decoration: none;
  border-bottom: 1px solid rgba(124, 92, 252, .38); word-break: break-all;
}
.ne-vditor :deep(.vditor-reset a:hover) { border-bottom-color: var(--asc-primary); }
.ne-vditor :deep(.vditor-reset img) { max-width: 100%; height: auto; border-radius: 8px; }

/* 编辑器下缘一行：字数靠右（快捷操作已固定在顶栏） */
.ne-meta { display: flex; align-items: center; justify-content: flex-end; padding: 10px 4px 0; }
.ne-count { font-size: 12px; color: var(--asc-text-3); font-variant-numeric: tabular-nums; flex-shrink: 0; }

/* ===== 选段浮动工具条（与 EditorView 同款） ===== */
.sel-bar {
  position: fixed; z-index: 3000;
  display: flex; align-items: center; gap: 4px;
  background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 8px;
  padding: 6px 10px; box-shadow: 0 4px 16px rgba(0, 0, 0, .12);
}
.fmt-btn {
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  border: none; background: transparent; border-radius: 6px;
  font-size: 15px; color: var(--asc-text-2);
  cursor: pointer; transition: all .15s;
}
.fmt-btn:hover { background: var(--asc-surface-2); color: var(--asc-text); }
.fmt-sep { width: 1px; height: 18px; background: var(--asc-divider); margin: 0 4px; }

/* ===== 加工结果对比弹窗 ===== */
.polish-block { margin-bottom: 14px; }
.polish-label {
  font-size: 11px; font-weight: 600; letter-spacing: 1.5px;
  color: var(--asc-text-2); margin-bottom: 6px;
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
</style>
