<template>
  <div class="docx-origin">
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
 * - 该视图为只读呈现，不参与划线/阅读进度（文本视图才承载这些能力）
 */
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { Loading } from '@element-plus/icons-vue'

const props = defineProps({
  url: { type: String, required: true },
})

const host = ref(null)
const loading = ref(true)
const error = ref('')
let cancelled = false

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
  } catch (e) {
    if (!cancelled) error.value = String(e?.message || e)
  } finally {
    if (!cancelled) loading.value = false
  }
}

onMounted(render)
onBeforeUnmount(() => { cancelled = true })
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
