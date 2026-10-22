<template>
  <div class="docx-origin" @scroll.passive="e => emit('scroll', e)">
    <div v-if="loading" class="docx-state">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>正在还原原文版面…</span>
    </div>
    <div v-else-if="error" class="docx-state docx-state-error">
      <div class="docx-state-title">原文渲染失败</div>
      <div class="docx-state-msg">{{ error }}</div>
      <div class="docx-state-sub">
        可切回「文本视图」阅读已解析的内容，或用右上角「下载文件」在 Word / WPS 中打开原文。
      </div>
    </div>
    <!-- 宿主容器需保持可见（display:none 会影响 docx-preview 的分页计算） -->
    <div ref="host" class="docx-host"></div>
  </div>
</template>

<script setup>
/**
 * Word「原文视图」：用 docx-preview 在浏览器端还原 .docx 版面
 * （表格、图片、字体样式、分页、页眉页脚），作为文本视图的兜底呈现。
 *
 * 说明：
 * - 采用动态 import，docx-preview + jszip 单独打包成一个 chunk，不拖累首屏
 * - styleContainer 传 undefined → 文档样式注入到宿主容器内，不污染全局样式
 * - 仅支持 OOXML（.docx）；旧版 .doc 无法解析，由父组件控制入口是否出现
 * - 该视图为只读呈现，不参与划线；阅读进度仍以文本视图为准（本视图没有页锚点）
 * - 但**参与目录跟随**：渲染后给标题段落打 data-section 锚点，父组件据此高亮 / 跳转（见 tagSections）
 */
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { Loading } from '@element-plus/icons-vue'

const props = defineProps({
  url: { type: String, required: true },
  sections: { type: Array, default: () => [] },   // 目录条目（章节标题原文），用于打段落锚点
})

const emit = defineEmits(['scroll', 'ready'])

const host = ref(null)
const loading = ref(true)
const error = ref('')
let cancelled = false
let resizeObserver = null

// 容器变窄时等比缩小版面：docx-preview 渲染的是固定宽度纸张（A4），
// 容器窄于纸宽会横向裁切（历史 bug：要切视图再切回才恢复）。
// 用 zoom 而非 transform scale —— zoom 会同步缩放布局高度，不留空白占位。
function fitWidth() {
  const wrap = host.value?.querySelector('.docx-wrapper')
  const section = host.value?.querySelector('section.docx')
  const container = host.value?.parentElement
  if (!wrap || !section || !container) return
  const pageWidth = section.offsetWidth
  const avail = container.clientWidth - 32
  if (!pageWidth || !avail) return
  wrap.style.zoom = pageWidth > avail ? String(Math.max(avail / pageWidth, 0.3)) : ''
}

// 章节锚点：给「文本等于目录条目」的标题段落打 data-section，供父组件做目录跟随 / 目录跳转。
//
// 为什么不用页号：docx 的 page_no 由后端按 ~700 字/页估算（python-docx 拿不到真实页码），
//   实测同一份方案书的 23 个章节 page_no 全是 1 → 页级映射不可用。
// 为什么能按文本匹配：后端解析时 section_path 取的就是标题段落的纯文本（parser.parse_docx），
//   与这里 textContent 归一化后逐一相等；docx-preview 渲染出的标题**没有语义标签**
//   （实测是 <p class="docx_1">，类名是 Word 样式 id，中文模板下不可依赖）。
// 顺序推进：同名正文段落不会被误标 —— 每个标题只认「上一个命中之后的第一个」匹配。
function norm(s) { return (s || '').replace(/\s+/g, ' ').trim() }

// 目录条目可能是**多级 section_path**（`章/节/小节`），而段落的 textContent 只有末级标题：
// 拿整条路径去比对，只有一级标题能命中（实测 23 条目录只标出 1 个锚点，其余点击静默无效）。
// 比对用末级；`／` 是全角转义（标题自带的斜杠，见 parser._path_seg），比对前还原成半角。
function leafTitle(t) {
  const segs = String(t || '').split('/')
  return (segs[segs.length - 1] || '').replace(/／/g, '/')
}

function tagSections() {
  const titles = (props.sections || []).filter(Boolean)
  if (!titles.length || !host.value) return
  const paras = [...host.value.querySelectorAll('p')]
  let pi = 0
  for (const title of titles) {
    const want = norm(leafTitle(title))
    for (; pi < paras.length; pi++) {
      if (norm(paras[pi].textContent) === want) {
        paras[pi].setAttribute('data-section', title)
        pi++
        break
      }
    }
  }
}

async function render() {
  if (!host.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(props.url)
    if (!res.ok) throw new Error(`文件读取失败（HTTP ${res.status}）`)
    const blob = await res.blob()
    if (cancelled) return
    const { renderAsync } = await import('docx-preview')
    host.value.innerHTML = ''
    await renderAsync(blob, host.value, undefined, {
      className: 'docx',
      inWrapper: true,
      breakPages: true,
      renderHeaders: true,
      renderFooters: true,
      renderFootnotes: true,
    })
    tagSections()
    fitWidth()
    emit('ready')
  } catch (e) {
    if (!cancelled) error.value = String(e?.message || e)
  } finally {
    if (!cancelled) loading.value = false
  }
}

onMounted(() => {
  render()
  // 拖分栏 / 改窗口 / 进出全屏 → 重新计算缩放
  resizeObserver = new ResizeObserver(() => fitWidth())
  if (host.value?.parentElement) resizeObserver.observe(host.value.parentElement)
})
onBeforeUnmount(() => {
  cancelled = true
  resizeObserver?.disconnect()
})
</script>

<style scoped>
.docx-origin {
  flex: 1; overflow-y: auto;
  background: #f2f2f3;
}
.docx-state {
  display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 28px 24px; font-size: 13px; color: var(--asc-text-2);
}
.docx-state-error { flex-direction: column; align-items: flex-start; gap: 6px; }
.docx-state-title { font-size: 14px; font-weight: 600; color: #d85a30; }
.docx-state-msg { font-size: 12px; color: var(--asc-text-2); word-break: break-word; }
.docx-state-sub { font-size: 12px; color: var(--asc-text-3); line-height: 1.7; }
.docx-host { min-height: 60px; }
/* docx-preview 生成的 wrapper 默认是浅灰底 + 白纸页，这里只收紧外边距以适配阅读区 */
.docx-host :deep(.docx-wrapper) {
  background: transparent; padding: 20px 0 48px;
}
.docx-host :deep(.docx-wrapper > section.docx) {
  margin: 0 auto 20px; box-shadow: var(--asc-shadow-hover);
  border-radius: 2px;
}
</style>
