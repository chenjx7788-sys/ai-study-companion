<!--
  笔记编辑弹窗（共用组件）

  为什么存在：知识库页与学习页各内联了一份同样的弹窗；统计页「AI 周报」和
  播客页「对话脚本」转笔记后也要能「查看」，若再抄一份就是第 4 份副本。
  所以把这份能力抽成组件，任何页面都能在**当前页**弹窗打开某条笔记。

  用法：
    <NoteEditorDialog v-model="show" :note-id="id" @changed="reload" />

  - v-model：弹窗显隐
  - note-id：笔记 id（打开时按 id 拉全文，含 anchor）
  - @changed：保存 / 删除后触发，父组件应刷新自己的笔记列表（如「已转笔记」状态）
-->
<template>
  <el-dialog :model-value="modelValue" width="560px" class="note-dialog" :close-on-click-modal="false"
    @update:model-value="(v) => emit('update:modelValue', v)" @closed="onClosed">
    <template #header>
      <div class="nd-header">
        <div class="nd-title-row">
          <span class="nd-title">编辑笔记</span>
          <span class="nd-src-tag" :class="noteTagClass(note.sourceType)">
            {{ noteTagLabel(note.sourceType) }}
          </span>
        </div>
        <div class="nd-sub">
          <span v-if="note.materialTitle">{{ note.materialTitle }}</span>
          <span v-else-if="note.anchor?.page_no">第 {{ note.anchor.page_no }} 页</span>
          <span v-else>材料无关笔记（可在知识库「按笔记」查看）</span>
        </div>
      </div>
    </template>

    <div class="nd-body">
      <div class="nd-field">
        <label class="nd-label" for="nd-title-input">标题</label>
        <el-input id="nd-title-input" v-model="note.title" placeholder="给这条笔记起个标题" maxlength="80" />
      </div>

      <div class="nd-field">
        <div class="nd-field-head">
          <label class="nd-label">内容</label>
          <div class="nd-seg" role="tablist" aria-label="笔记内容编辑方式">
            <button type="button" class="nd-seg-btn" :class="{ active: note.mode === 'edit' }"
              role="tab" :aria-selected="note.mode === 'edit'" @click="note.mode = 'edit'">编辑</button>
            <button type="button" class="nd-seg-btn" :class="{ active: note.mode === 'preview' }"
              role="tab" :aria-selected="note.mode === 'preview'" @click="note.mode = 'preview'">预览</button>
          </div>
        </div>
        <textarea v-if="note.mode === 'edit'" v-model="note.content" :rows="10" class="nd-textarea"
          placeholder="支持 Markdown，可直接粘贴"
          @mouseup.stop="onNoteSelect" @keyup="onNoteSelect" @blur="hideNoteSel"></textarea>
        <div v-else class="md-preview nd-preview" v-html="renderMd(note.content)"></div>
        <div v-if="noteSel.show" class="nd-sel-bar">
          <span class="nd-sel-count">已选中 {{ noteSel.text.length }} 字</span>
          <span class="nd-sel-sep" aria-hidden="true"></span>
          <el-button text type="primary" size="small" @mousedown.prevent @click="noteTransform('rewrite', noteSel)">改写</el-button>
          <el-button text type="primary" size="small" @mousedown.prevent @click="noteTransform('expand', noteSel)">扩写</el-button>
          <el-button text type="primary" size="small" @mousedown.prevent @click="noteTransform('continue', noteSel)">续写</el-button>
          <el-button text type="primary" size="small" @mousedown.prevent @click="noteTransform('summarize', noteSel)">总结</el-button>
          <el-button text size="small" @mousedown.prevent @click="hideNoteSel">取消</el-button>
        </div>
        <div class="nd-count">{{ note.content.length }} 字</div>
      </div>
    </div>

    <div v-if="note.content.trim()" class="nd-ai">
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

    <div v-if="note.id" class="nd-shortcuts">
      <el-button v-if="note.materialId && note.anchor?.page_no" text size="small" @click="jumpToOriginal">
        跳转原文 P{{ note.anchor.page_no }}
      </el-button>
      <el-tooltip content="把这条笔记出成选择题，进复习队列定期重考（再认）" placement="top" :show-after="250">
        <el-button text type="warning" size="small" :loading="addingReview" @click="addToReview">加入复习</el-button>
      </el-tooltip>
      <el-tooltip content="出成引导题，先自己讲一遍再对照答案，练主动回忆（费曼）" placement="top" :show-after="250">
        <el-button text type="primary" size="small" :loading="addingRecall" @click="addToRecall">生成复述卡</el-button>
      </el-tooltip>
      <el-button v-if="note.content.length > 200" text type="warning" size="small"
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
          <el-button @click="emit('update:modelValue', false)">取消</el-button>
          <el-button type="primary" :loading="note.saving" @click="saveNote">保存</el-button>
        </div>
      </div>
    </template>
  </el-dialog>

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
import { ref, reactive, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { noteApi, reviewApi } from '../api'
import http, { errMsg } from '../api/http'
import { streamSSE } from '../utils/sse'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  noteId: { type: [Number, String], default: null },
  // 可选：材料标题（材料笔记用；材料无关笔记留空）
  materialTitle: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'changed'])

const router = useRouter()
const md = new MarkdownIt({ breaks: true })
const renderMd = (t) => md.render(t || '')

const note = reactive({
  id: null, title: '', content: '', materialTitle: '',
  materialId: null, sourceType: 'manual', anchor: null, saving: false, mode: 'edit',
})

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

// ---------- 打开：按 id 拉全文 ----------
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
    note.mode = 'edit'
  } catch (e) {
    ElMessage.error(errMsg(e, '笔记打开失败，可能已被删除'))
    emit('update:modelValue', false)
  }
}

watch(() => props.modelValue, (v) => {
  if (v && props.noteId != null) {
    // 先清空再拉，避免闪现上一条笔记的内容
    note.id = Number(props.noteId)
    note.title = ''
    note.content = ''
    note.materialTitle = props.materialTitle || ''
    note.materialId = null
    note.anchor = null
    note.mode = 'edit'
    loadNote(props.noteId)
  }
})

function onClosed() {
  hideNoteSel()
  transformDialog.show = false
}

// ---------- 保存 / 删除 ----------
async function saveNote() {
  note.saving = true
  try {
    const res = await noteApi.update(note.id, { title: note.title, content: note.content })
    emit('update:modelValue', false)
    emit('changed')
    if (res.data?.reindex_warning) ElMessage.warning(res.data.reindex_warning)
    else ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    note.saving = false
  }
}

async function deleteNote() {
  try {
    await noteApi.remove(note.id)
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

// ---------- 笔记加工：改写/扩写/续写/总结 ----------
const TRANSFORM_LABELS = { rewrite: '改写', expand: '扩写', continue: '续写', summarize: '总结' }
const transformDialog = reactive({ show: false, mode: 'rewrite', original: '', result: '', streaming: false, sel: null })
const noteSel = reactive({ show: false, text: '', start: 0, end: 0 })
const transformTitle = computed(() => {
  const act = TRANSFORM_LABELS[transformDialog.mode] || '加工'
  return transformDialog.sel ? `${act}选中文字` : `${act}笔记`
})

// 选区：选中指定文字后浮出加工工具（sel 为 null = 整条笔记）
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
  const content = sel ? sel.text : note.content?.trim()
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

function adoptTransform() {
  if (!transformDialog.result.trim()) { ElMessage.warning('结果为空，无法采用'); return }
  if (transformDialog.sel) {
    const { start, end } = transformDialog.sel
    if (transformDialog.mode === 'continue') {
      // 续写：保留原选段，续写内容追加在选区之后
      note.content = note.content.substring(0, end) + '\n\n' + transformDialog.result + note.content.substring(end)
    } else {
      note.content = note.content.substring(0, start) + transformDialog.result + note.content.substring(end)
    }
  } else if (transformDialog.mode === 'continue') {
    // 整条续写：原文保留，续写内容追加到末尾
    note.content = note.content.trimEnd() + '\n\n' + transformDialog.result
  } else {
    note.content = transformDialog.result
  }
  transformDialog.show = false
  ElMessage.success(transformDialog.mode === 'continue' ? '已续写，记得保存' : '已采用，记得保存')
}
</script>

<style scoped>
/* ===== 笔记弹窗（与知识库页 / 资料详情页 note-dialog 一致） ===== */
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
.nd-sub { font-size: 12px; color: var(--asc-text-2); }

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
  align-self: flex-end; font-size: 11px; color: var(--asc-text-2);
  margin-top: 2px; font-variant-numeric: tabular-nums;
}

/* AI 加工面板：浅紫卡片，图标 + 标签 + 四动作 */
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

/* 加工结果对比弹窗 */
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

.nd-footer { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
.nd-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
</style>
