<template>
  <div ref="wrapRef" class="pdf-reader" @mouseup="e => emit('mouseup', e)" @scroll="e => emit('scroll', e)">
    <div v-if="loading" class="pdf-loading">
      <el-icon class="is-loading"><Loading /></el-icon> 正在加载原文…
    </div>
    <el-empty v-else-if="error" :description="error" />
    <div v-for="pageNum in pageList" :key="pageNum" class="pdf-page" :data-page="pageNum">
      <!-- 足迹标记：本页有解读/笔记 -->
      <div v-if="fp(pageNum)" class="fp-marker">
        <span v-if="fp(pageNum).chain" class="fp-item"><i class="fp-dot fp-chain"></i>解读 {{ fp(pageNum).chain }}</span>
        <span v-if="fp(pageNum).note" class="fp-item"><i class="fp-dot fp-note"></i>笔记 {{ fp(pageNum).note }}</span>
      </div>
      <div class="page-inner" :ref="el => { if (el) innerRefs[pageNum] = el }">
        <canvas :ref="el => { if (el) canvasRefs[pageNum] = el }"></canvas>
        <div :ref="el => { if (el) textLayerRefs[pageNum] = el }" class="textLayer"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, shallowRef, onMounted, onUnmounted } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import * as pdfjs from 'pdfjs-dist'
import { TextLayer } from 'pdfjs-dist'
import 'pdfjs-dist/web/pdf_viewer.css'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url
).toString()

const props = defineProps({
  url: { type: String, required: true },
  footprints: { type: Object, default: () => ({}) },   // { pageNo: {chain:n, note:n} }
  highlights: { type: Array, default: () => [] },      // [{page_no, selected_text, color}]
})

const emit = defineEmits(['mouseup', 'scroll'])

const wrapRef = ref(null)
const loading = ref(true)
const error = ref('')
const total = ref(0)
const rendered = ref(0)
const pageList = ref([])
const canvasRefs = shallowRef({})
const textLayerRefs = shallowRef({})
const innerRefs = shallowRef({})

let doc = null
let observer = null
const renderedPages = new Set()
let scale = 1

const fp = (pageNum) => props.footprints?.[pageNum] || null

async function renderPage(pageNum) {
  const page = await doc.getPage(pageNum)
  const canvas = canvasRefs.value[pageNum]
  const textLayerDiv = textLayerRefs.value[pageNum]
  if (!canvas || !textLayerDiv) return

  // HiDPI 清晰化：canvas 位图按 devicePixelRatio 放大，CSS 尺寸保持逻辑像素
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const viewport = page.getViewport({ scale: scale * dpr })
  const cssViewport = page.getViewport({ scale })

  canvas.width = Math.floor(viewport.width)
  canvas.height = Math.floor(viewport.height)
  canvas.style.width = Math.floor(cssViewport.width) + 'px'
  canvas.style.height = Math.floor(cssViewport.height) + 'px'
  await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise

  const textContent = await page.getTextContent()
  textLayerDiv.style.setProperty('--scale-factor', String(scale))
  const tl = new TextLayer({
    textContentSource: textContent,
    container: textLayerDiv,
    viewport: cssViewport,
  })
  await tl.render()
  applyPdfHighlights(pageNum)
  rendered.value++
  if (rendered.value === total.value) loading.value = false
}

// 恢复该页划线高亮：给与高亮文本匹配的 textLayer span 加背景类
function applyPdfHighlights(pageNum) {
  const pageEl = wrapRef.value?.querySelector(`.pdf-page[data-page="${pageNum}"]`)
  const layer = pageEl?.querySelector('.textLayer')
  if (!layer) return
  const hls = props.highlights.filter(h => h.page_no === pageNum && h.selected_text)
  if (!hls.length) return
  for (const span of layer.querySelectorAll('span')) {
    const t = span.textContent?.trim()
    if (!t) continue
    for (const h of hls) {
      if (h.selected_text.includes(t)) {
        span.classList.add('hl-user', 'hl-' + h.color)
        break
      }
    }
  }
}

function handleIntersect(entries) {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue
    const pageNum = Number(entry.target.dataset.page)
    if (renderedPages.has(pageNum)) continue
    renderedPages.add(pageNum)
    renderPage(pageNum).catch(() => {})
  }
}

async function load() {
  try {
    doc = await pdfjs.getDocument(props.url).promise
    total.value = doc.numPages
    pageList.value = Array.from({ length: doc.numPages }, (_, i) => i + 1)
    // 按容器宽度自适应缩放
    const first = await doc.getPage(1)
    const base = first.getViewport({ scale: 1 })
    scale = Math.min((wrapRef.value.clientWidth - 64) / base.width, 2)
    // 首屏先渲染第 1 页，隐藏 loading（其余页懒加载）
    await renderPage(1)
    renderedPages.add(1)
    loading.value = false
    // 懒加载：页面容器已全部在 DOM，进入可视区才渲染 canvas
    observer = new IntersectionObserver(handleIntersect, { root: wrapRef.value, rootMargin: '300px 0px' })
    for (let i = 2; i <= doc.numPages; i++) {
      const el = wrapRef.value?.querySelector(`.pdf-page[data-page="${i}"]`)
      if (el) observer.observe(el)
    }
  } catch (e) {
    error.value = '原文渲染失败：' + (e.message || '未知错误')
    loading.value = false
  }
}

onMounted(load)
onUnmounted(() => {
  observer?.disconnect()
  doc?.destroy()
})
</script>

<style scoped>
.pdf-reader { flex: 1; overflow-y: auto; padding: 24px 32px 80px; background: #ececee; }
.pdf-loading { text-align: center; color: var(--asc-text-2); padding: 60px 0; font-size: 13px; }
.pdf-page {
  position: relative; margin: 0 auto 16px; width: fit-content;
  background: #fff; box-shadow: 0 2px 12px rgba(0, 0, 0, .08);
}
.page-inner { position: relative; }
.pdf-page canvas { display: block; }
.textLayer { position: absolute; inset: 0; }
.pdf-reader ::selection { background: rgba(124, 92, 252, .25); }

/* 足迹标记 */
.fp-marker {
  display: flex; gap: 10px; align-items: center;
  padding: 4px 10px; font-size: 11px; color: var(--asc-text-2);
  background: var(--asc-card); border-bottom: 1px solid var(--asc-border);
}
.fp-item { display: inline-flex; align-items: center; gap: 4px; }
.fp-dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; }
.fp-chain { background: rgba(124, 92, 252, .55); }
.fp-note { background: rgba(232, 163, 61, .7); }
/* 用户划线高亮（3 色，PDF 原文 textLayer） */
.textLayer :deep(span.hl-yellow) { background: rgba(252, 211, 77, .5); }
.textLayer :deep(span.hl-green) { background: rgba(134, 239, 172, .55); }
.textLayer :deep(span.hl-blue) { background: rgba(147, 197, 253, .55); }
</style>
