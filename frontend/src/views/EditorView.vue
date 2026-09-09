<template>
  <div class="page editor-page" v-loading="loading">
    <div class="editor-header">
      <el-button text @click="onCancel">
        <el-icon><ArrowLeft /></el-icon>返回
      </el-button>
      <div class="editor-actions">
        <div class="ai-bar">
          <svg class="ai-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/></svg>
          <span class="ai-label">AI 辅助</span>
          <el-button size="small" :loading="ai.busy && ai.mode === 'rewrite'" :disabled="ai.busy" @click="aiTransformAll('rewrite')">改写</el-button>
          <el-button size="small" :loading="ai.busy && ai.mode === 'expand'" :disabled="ai.busy" @click="aiTransformAll('expand')">扩写</el-button>
          <el-button size="small" :loading="ai.busy && ai.mode === 'continue'" :disabled="ai.busy" @click="aiContinue">续写</el-button>
          <el-button size="small" :loading="ai.busy && ai.mode === 'summarize'" :disabled="ai.busy" @click="aiTransformAll('summarize')">总结</el-button>
        </div>
        <el-button @click="onCancel">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </div>
    </div>

    <div class="title-row">
      <el-input v-model="title" placeholder="无标题文档" class="title-input" maxlength="100" @keyup.enter="vditor?.focus()" />
      <span class="save-state" :class="'st-' + saveState">{{ saveState === 'saving' ? '保存中…' : (saveState === 'unsaved' ? '未保存' : '已保存') }}</span>
    </div>

    <div class="editor-body">
      <div id="vditor" class="vditor-wrap" />
      <div class="editor-footer">
        <span class="word-count">共 {{ wordCount }} 字</span>
      </div>
    </div>

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

    <!-- 选段加工内联结果卡（不抢焦点，选区保持，替换后光标不动） -->
    <div v-if="selBar.streaming" class="sel-result" :style="{ left: selBar.x + 'px', top: selBar.y + 'px' }">
      <div class="sel-result-head">
        <span class="sel-result-title">AI {{ TRANSFORM_LABELS[selBar.mode] }}</span>
        <span>
          <el-button size="small" text @mousedown.prevent @click="cancelSelTransform">取消</el-button>
          <el-button size="small" type="primary" :disabled="selBar.busy" @mousedown.prevent @click="adoptSelTransform">{{ selBar.mode === 'continue' ? '插入' : '替换' }}</el-button>
        </span>
      </div>
      <div class="sel-result-body">{{ selBar.result }}<span v-if="selBar.busy" class="stream-cursor">▍</span></div>
    </div>

    <!-- 整篇加工对比弹窗 -->
    <el-dialog v-model="ai.show" :title="`AI ${TRANSFORM_LABELS[ai.mode]}`" width="560px" :close-on-click-modal="false">
      <div v-if="ai.action !== 'continue'" class="polish-block">
        <div class="polish-label">原文</div>
        <div class="polish-text">{{ ai.original }}</div>
      </div>
      <div class="polish-block polish-block-new">
        <div class="polish-label">{{ ai.action === 'continue' ? '续写内容' : (TRANSFORM_LABELS[ai.mode] + '后') }}</div>
        <div class="polish-text">{{ ai.result }}<span v-if="ai.busy" class="stream-cursor">▍</span></div>
      </div>
      <template #footer>
        <el-button @click="ai.show = false">放弃</el-button>
        <el-button type="primary" :disabled="ai.busy" @click="adoptAi">{{ ai.action === 'continue' ? '插入' : '采用' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import Vditor from 'vditor'
import 'vditor/dist/index.css'
import { materialApi } from '../api'
import { errMsg } from '../api/http'
import { streamSSE } from '../utils/sse'

const route = useRoute()
const router = useRouter()

const title = ref('')
const saving = ref(false)
const loading = ref(false)
const vditor = ref(null)
const wordCount = ref(0)
const saveState = ref('saved')   // saved / unsaved / saving

const TRANSFORM_LABELS = { rewrite: '改写', expand: '扩写', summarize: '总结', continue: '续写' }
// 整篇加工弹窗（action: all=整篇覆盖 / continue=续写插入光标）
const ai = reactive({ show: false, mode: 'rewrite', original: '', result: '', busy: false, action: 'all' })
// 选段加工（工具条 + 内联结果卡）
const selBar = reactive({ show: false, x: 0, y: 0, text: '', streaming: false, mode: 'rewrite', result: '', busy: false })

let initialTitle = ''
let initialContent = ''
let editorReady = false   // Vditor 初始化完成后再标记「未保存」
let draftTimer = null
const draftKey = route.params.id ? `asc-editor-draft-${route.params.id}` : 'asc-editor-draft-new'

function getContent() { return (vditor.value?.getValue() || '').trim() }
function hasChanges() { return title.value.trim() !== initialTitle.trim() || getContent() !== initialContent }

function markUnsaved() { if (editorReady) saveState.value = 'unsaved' }

// 自动草稿：30s 定时 + 离开前落盘
function saveDraft() {
  const content = getContent()
  if (!title.value.trim() && !content) return
  try {
    localStorage.setItem(draftKey, JSON.stringify({ title: title.value, content, ts: Date.now() }))
  } catch (e) { /* 忽略容量/隐私错误 */ }
}
function loadDraft() {
  try {
    const raw = localStorage.getItem(draftKey)
    return raw ? JSON.parse(raw) : null
  } catch (e) { return null }
}
function clearDraft() { localStorage.removeItem(draftKey) }

// Ctrl/Cmd+S 保存
function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    onSave()
  }
}

function hideSelBar() {
  selBar.show = false
  selBar.text = ''
  selBar.streaming = false
  selBar.result = ''
}

// 选区监听：mouseup/keyup 后检查选区，浮出/隐藏工具条
function onVditorSelect() {
  if (selBar.streaming) return   // 流式中锁定，不响应选区变化
  const el = document.getElementById('vditor')
  const sel = window.getSelection()
  if (!sel || sel.isCollapsed || !el || !sel.anchorNode || !el.contains(sel.anchorNode)) {
    if (selBar.show) hideSelBar()
    return
  }
  const text = vditor.value?.getSelection()?.trim()
  if (!text) { if (selBar.show) hideSelBar(); return }
  const rect = sel.getRangeAt(0).getBoundingClientRect()
  selBar.text = text
  selBar.x = Math.max(8, Math.min(rect.left, window.innerWidth - 300))
  selBar.y = Math.max(64, rect.top - 46)
  selBar.show = true
}

onMounted(async () => {
  let initial = ''
  if (route.params.id) {
    loading.value = true
    try {
      const { data } = await materialApi.detail(route.params.id)
      title.value = data.title
      // 读 .md 原文回填（比 chunks 拼接更无损）；no-store 强制拿最新，避免浏览器启发式缓存返回保存前的旧内容
      const resp = await fetch(materialApi.fileUrl(route.params.id), { cache: 'no-store' })
      if (resp.ok) initial = await resp.text()
    } catch (e) {
      ElMessage.error(errMsg(e, '加载文档失败'))
    } finally {
      loading.value = false
    }
  }

  // 检测上次未保存的草稿，询问是否恢复（草稿与原文一致则直接清除，避免保存后的残留草稿反复打扰）
  const draft = loadDraft()
  if (draft && draft.content && draft.content !== initial) {
    try {
      await ElMessageBox.confirm('检测到上次未保存的草稿，是否恢复？', '恢复草稿', {
        confirmButtonText: '恢复', cancelButtonText: '丢弃', type: 'info',
      })
      title.value = draft.title || title.value
      initial = draft.content
    } catch {
      clearDraft()
    }
  } else if (draft) {
    clearDraft()
  }

  initialTitle = title.value
  initialContent = initial
  wordCount.value = initial.length

  vditor.value = new Vditor('vditor', {
    mode: 'wysiwyg',
    height: Math.max(360, window.innerHeight - 200),
    cache: { enable: false },
    placeholder: '在这里开始撰写正文…',
    toolbar: ['headings', 'bold', 'italic', 'strike', '|', 'list', 'ordered-list', 'check', '|',
      'quote', 'code', 'inline-code', 'link', 'table', '|', 'line', '|',
      'undo', 'redo', '|', 'outline', 'fullscreen'],
    input: (value) => {
      wordCount.value = (value || '').length
      markUnsaved()
    },
    after: () => {
      if (initial) vditor.value.setValue(initial)
      vditor.value.focus()
      editorReady = true
    },
  })

  document.addEventListener('mouseup', onVditorSelect)
  document.addEventListener('keyup', onVditorSelect)
  document.addEventListener('keydown', onKeydown, true)
  draftTimer = setInterval(saveDraft, 30000)
  watch(title, markUnsaved)
})

onBeforeUnmount(() => {
  document.removeEventListener('mouseup', onVditorSelect)
  document.removeEventListener('keyup', onVditorSelect)
  document.removeEventListener('keydown', onKeydown, true)
  if (draftTimer) clearInterval(draftTimer)
  if (saveState.value === 'unsaved') saveDraft()   // 仅未保存时落盘（保存后 saveState=saved，不误存）
  vditor.value?.destroy()
})

// ---------- 整篇加工（顶栏按钮） ----------

async function aiTransformAll(mode) {
  const content = getContent()
  if (!content) { ElMessage.warning('正文还没有内容'); return }
  ai.mode = mode
  ai.action = 'all'
  ai.original = content
  ai.result = ''
  ai.busy = true
  ai.show = true
  try {
    await streamSSE('/api/ai/note/transform/stream',
      { content, mode, title: title.value },
      (t) => { ai.result += t },
      () => {},
      (msg) => { throw new Error(msg) },
    )
  } catch (e) {
    ElMessage.error(e.message || '加工失败')
    ai.show = false
  } finally {
    ai.busy = false
  }
}

async function aiContinue() {
  const content = getContent()
  if (!content) { ElMessage.warning('正文还没有内容'); return }
  ai.mode = 'continue'
  ai.action = 'continue'
  ai.original = content
  ai.result = ''
  ai.busy = true
  ai.show = true
  try {
    await streamSSE('/api/ai/note/transform/stream',
      { content, mode: 'continue', title: title.value },
      (t) => { ai.result += t },
      () => {},
      (msg) => { throw new Error(msg) },
    )
  } catch (e) {
    ElMessage.error(e.message || '续写失败')
    ai.show = false
  } finally {
    ai.busy = false
  }
}

function adoptAi() {
  if (!ai.result.trim()) { ElMessage.warning('结果为空，无法采用'); return }
  if (ai.action === 'continue') {
    vditor.value.insertValue(ai.result)   // 续写：插入光标处
  } else {
    vditor.value.setValue(ai.result)      // 整篇加工：覆盖全文
  }
  ai.show = false
  vditor.value.focus()
  ElMessage.success(ai.action === 'continue' ? '已续写' : '已采用，记得保存')
}

// ---------- 选段加工（工具条 → 内联结果卡，全程不抢焦点） ----------

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
}

async function startSelTransform(mode) {
  const text = selBar.text
  if (!text) { hideSelBar(); return }
  selBar.mode = mode
  selBar.result = ''
  selBar.busy = true
  selBar.streaming = true
  try {
    await streamSSE('/api/ai/note/transform/stream',
      { content: text, mode, title: title.value },
      (t) => { selBar.result += t },
      () => {},
      (msg) => { throw new Error(msg) },
    )
  } catch (e) {
    ElMessage.error(e.message || '加工失败')
    hideSelBar()
  } finally {
    selBar.busy = false
  }
}

function adoptSelTransform() {
  if (!selBar.result.trim()) { ElMessage.warning('结果为空，无法替换'); return }
  if (selBar.mode === 'continue') {
    // 续写：保留原选段，续写内容追加在其后
    vditor.value.updateValue(selBar.text + '\n\n' + selBar.result)
  } else {
    vditor.value.updateValue(selBar.result)   // 原生替换选区，光标保持在原位
  }
  hideSelBar()
  vditor.value.focus()
  ElMessage.success(selBar.mode === 'continue' ? '已续写' : '已替换')
}

function cancelSelTransform() {
  selBar.streaming = false
  selBar.result = ''
}

// ---------- 保存 / 离开 ----------

function goBack() {
  if (route.query.from === 'study') router.push(`/study/${route.params.id}`)
  else router.push('/')
}

async function onSave() {
  const content = getContent()
  if (!title.value.trim()) { ElMessage.warning('请填写标题'); return }
  if (!content) { ElMessage.warning('正文为空'); return }
  saving.value = true
  saveState.value = 'saving'
  try {
    if (route.params.id) await materialApi.updateDocument(route.params.id, { content, title: title.value.trim() })
    else await materialApi.createDocument({ title: title.value.trim(), content })
    ElMessage.success('已保存')
    initialTitle = title.value.trim()
    initialContent = content
    clearDraft()
    saveState.value = 'saved'
    goBack()
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
    saveState.value = 'unsaved'
  } finally {
    saving.value = false
  }
}

function onCancel() {
  goBack()
}

onBeforeRouteLeave(async () => {
  if (!hasChanges()) return true
  try {
    await ElMessageBox.confirm('内容尚未保存，确定离开吗？', '提示', {
      confirmButtonText: '离开', cancelButtonText: '留下', type: 'warning',
    })
    return true
  } catch {
    return false
  }
})
</script>

<style scoped>
.editor-page {
  display: flex; flex-direction: column; min-height: 100%;
  background:
    radial-gradient(1200px 600px at 12% -15%, rgba(124, 92, 252, .08), transparent 60%),
    radial-gradient(1000px 520px at 88% -8%, rgba(143, 123, 255, .06), transparent 55%),
    var(--asc-bg);
}
.editor-header { display: flex; align-items: center; justify-content: space-between; margin: 0 auto 8px; width: 100%; max-width: 900px; }
.editor-actions { display: flex; align-items: center; gap: 10px; }
.title-row { position: relative; display: flex; align-items: center; gap: 12px; width: 100%; max-width: 900px; margin: 0 auto 18px; }
.title-row::after {
  content: ''; position: absolute; left: 6px; right: 0; bottom: -2px; height: 2px;
  background: linear-gradient(90deg, var(--asc-primary), rgba(124, 92, 252, .18) 55%, transparent);
  border-radius: 2px;
}
.title-input { flex: 1; width: auto; }
.save-state { flex-shrink: 0; font-size: 12px; color: var(--asc-text-3); white-space: nowrap; }
.save-state.st-unsaved { color: #ba7517; }
.title-input :deep(.el-input__wrapper) {
  box-shadow: none !important;
  background: transparent !important;
  padding: 2px 6px;
}
.title-input :deep(.el-input__inner) {
  font-size: 28px; font-weight: 700; height: 50px; color: var(--asc-text); letter-spacing: -0.3px;
}
.title-input :deep(.el-input__inner)::placeholder {
  color: var(--asc-text-3); font-weight: 500;
}
.ai-bar { display: flex; align-items: center; gap: 2px; padding: 3px; background: var(--asc-surface-2); border: 1px solid var(--asc-border); border-radius: 8px; }
.ai-icon { width: 14px; height: 14px; color: var(--asc-primary); margin: 0 2px 0 6px; }
.ai-label { font-size: 12px; color: var(--asc-text-2); font-weight: 500; margin-right: 6px; }
.editor-body { flex: 1; display: flex; flex-direction: column; width: 100%; max-width: 900px; margin: 0 auto; }
.vditor-wrap {
  flex: 1;
  background: var(--asc-card);
  border: 1px solid var(--asc-border);
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .04), 0 12px 32px rgba(0, 0, 0, .07);
}
.editor-body :deep(.vditor-content) { padding: 24px 28px; }
.editor-footer { display: flex; justify-content: flex-end; padding: 8px 4px 0; }
.word-count { font-size: 12px; color: var(--asc-text-3); }

.sel-bar {
  position: fixed; z-index: 100;
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

.sel-result {
  position: fixed; z-index: 100; width: 320px;
  background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, .14); overflow: hidden;
}
.sel-result-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-bottom: 1px solid var(--asc-divider);
}
.sel-result-title { font-size: 13px; font-weight: 500; color: var(--asc-text-2); }
.sel-result-body {
  font-size: 13px; line-height: 1.7; color: var(--asc-text-2);
  max-height: 240px; overflow-y: auto; padding: 12px;
  white-space: pre-wrap; word-break: break-word;
}

.polish-block { margin-bottom: 14px; }
.polish-label { font-size: 12px; color: var(--asc-text-3); margin-bottom: 6px; }
.polish-text {
  font-size: 13px; line-height: 1.7; color: var(--asc-text-2);
  white-space: pre-wrap; word-break: break-word; max-height: 220px; overflow-y: auto;
  background: var(--asc-surface-2); border-radius: 8px; padding: 12px;
}
.polish-block-new .polish-text { background: var(--asc-primary-soft); }
.stream-cursor { animation: asc-blink 1s step-end infinite; color: var(--asc-primary); }
@keyframes asc-blink { 50% { opacity: 0; } }
</style>
