<template>
  <div class="epub-origin">
    <div class="epub-bar">
      <el-button size="small" text :disabled="cur <= 1" @click="go(cur - 1)">上一章</el-button>
      <el-select v-model="cur" size="small" class="epub-select" filterable @change="go">
        <el-option v-for="c in chapters" :key="c.index" :label="c.title || `第 ${c.index} 章`" :value="c.index" />
      </el-select>
      <el-button size="small" text :disabled="cur >= chapters.length" @click="go(cur + 1)">下一章</el-button>
      <span class="epub-pos">第 {{ cur }} / {{ chapters.length }} 章</span>
    </div>

    <div v-if="!chapters.length" class="epub-state">未解析出章节，可切回「文本视图」查看，或下载原件阅读</div>

    <div v-else class="epub-frame-wrap">
      <iframe ref="frame" class="epub-frame" :src="src" sandbox="allow-same-origin"
        title="EPUB 原文" @load="onLoad" />
      <div v-if="loading" class="epub-overlay">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在还原原文版面…</span>
      </div>
      <div v-if="error" class="epub-overlay epub-overlay-error">
        <div class="epub-state-title">原文渲染失败</div>
        <div class="epub-state-msg">{{ error }}</div>
        <div class="epub-state-sub">可切回「文本视图」阅读已解析内容，或用右上角「下载文件」在阅读器中打开。</div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * EPUB「原文视图」：iframe 直出 zip 内的章节 XHTML，保留原书 CSS / 插图 / 字体
 *
 * 为什么不用 epub.js 之类的前端阅读器：
 * EPUB 是可重排（reflowable）格式，本身没有页码——滚动阅读就是它的原貌，
 * 而「翻页 / 字号调节」属于阅读器附加能力。走服务端镜像（后端 /materials/{id}/epub-res/）
 * 可让章节里的相对引用（../Styles/main.css、../Images/a.png）在浏览器里自然解析，
 * 既零新增前端依赖，也避开了 epub.js 在 macOS WKWebView 上的渲染不确定性。
 *
 * 状态约定：chapter index 与 MaterialChunk.page_no 一一对应，
 * 所以「当前在第几章」由父组件用 activePage 单一驱动（props.index），
 * 左栏目录点击、阅读进度恢复、上一章/下一章 三条路径自动收敛到同一个值。
 */
import { ref, computed, watch, nextTick } from 'vue'
import { Loading } from '@element-plus/icons-vue'

const props = defineProps({
  chapters: { type: Array, default: () => [] },
  index: { type: Number, default: 1 },
  base: { type: String, required: true },
})
const emit = defineEmits(['change'])

const frame = ref(null)
const loading = ref(true)
const error = ref('')

const clamp = (v) => {
  const n = Number(v) || 1
  const max = props.chapters.length || 1
  return Math.min(Math.max(1, n), max)
}

const cur = ref(clamp(props.index))

// 章节清单是异步加载的：加载完成后用 props.index 校正一次（此前 chapters 为空，clamp 只能给 1）
watch(() => props.chapters, () => { cur.value = clamp(props.index) })
// 外部驱动（目录跳转 / 进度恢复）只同步显示值，不回抛 change，避免与父组件形成回环
watch(() => props.index, (v) => { const n = clamp(v); if (n !== cur.value) cur.value = n })

const current = computed(() => props.chapters.find(c => c.index === cur.value) || null)
// 每段单独编码：保留 '/' 分隔，空格 / 中文 / # 等交给 encodeURIComponent
const encodePath = (p) => (p || '').split('/').map(encodeURIComponent).join('/')
const src = computed(() => (current.value ? props.base + encodePath(current.value.href) : ''))

watch(src, () => { loading.value = true; error.value = '' })

function onLoad() {
  loading.value = false
  // iframe 内是外来文档，加载失败不会抛异常，只能靠内容判断（空文档 / 解析器错误页）
  try {
    const doc = frame.value?.contentDocument
    if (doc && !doc.body?.textContent?.trim() && !doc.querySelector('img, svg, image')) {
      error.value = '该章节没有可显示的内容'
    }
  } catch {
    /* 跨源受限时忽略：不影响阅读 */
  }
}

function go(v) {
  const n = clamp(v)
  if (n === cur.value) return
  cur.value = n
  nextTick(() => emit('change', n))
}
</script>

<style scoped>
.epub-origin { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.epub-bar {
  display: flex; align-items: center; gap: 8px; flex-shrink: 0;
  padding: 8px 16px; background: var(--asc-bg);
  border-bottom: 1px solid var(--asc-divider);
}
.epub-select { width: 260px; }
.epub-pos { font-size: 12px; color: var(--asc-text-3); }
.epub-frame-wrap { flex: 1; position: relative; min-height: 0; background: #fff; }
.epub-frame { width: 100%; height: 100%; border: none; background: #fff; }
.epub-state {
  flex: 1; display: flex; align-items: center; justify-content: center;
  padding: 28px 24px; font-size: 13px; color: var(--asc-text-2);
}
.epub-overlay {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  gap: 8px; padding: 28px 24px; background: #fff;
  font-size: 13px; color: var(--asc-text-2);
}
.epub-overlay-error { flex-direction: column; align-items: flex-start; gap: 6px; }
.epub-state-title { font-size: 14px; font-weight: 600; color: #d85a30; }
.epub-state-msg { font-size: 12px; color: var(--asc-text-2); word-break: break-word; }
.epub-state-sub { font-size: 12px; color: var(--asc-text-3); line-height: 1.7; }
</style>
