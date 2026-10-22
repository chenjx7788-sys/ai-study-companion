<template>
  <div class="epub-origin">
    <div class="epub-bar">
      <el-button size="small" text :disabled="cur <= 1" @click="go(cur - 1)">上一章</el-button>
      <el-select v-model="cur" size="small" class="epub-select" filterable @change="go">
        <el-option v-for="c in chapters" :key="c.index" :label="c.title || `第 ${c.index} 章`" :value="c.index" />
      </el-select>
      <el-button size="small" text :disabled="cur >= chapters.length" @click="go(cur + 1)">下一章</el-button>
      <span class="epub-pos">第 {{ cur }} / {{ chapters.length }} 章</span>
      <!-- 阅读偏好：版心 / 字号 / 字体族。存 localStorage，跨章节与下次打开都沿用 -->
      <div class="epub-pref">
        <el-radio-group v-model="fontStep" size="small" @change="savePref">
          <el-radio-button value="s">小</el-radio-button>
          <el-radio-button value="m">标准</el-radio-button>
          <el-radio-button value="l">大</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="fontFamily" size="small" @change="savePref">
          <el-radio-button value="serif">宋</el-radio-button>
          <el-radio-button value="sans">黑</el-radio-button>
        </el-radio-group>
      </div>
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

    <!-- 插图放大：说明图缩进版心后细节会变小，点击原图按原始分辨率看 -->
    <el-image-viewer v-if="previewUrl" :url="previewUrl" hide-on-click-modal @close="previewUrl = ''" />
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
 *
 * 阅读增强（2026-10-21）：原书 CSS 普遍缺宽度约束，实测 760px 视口下文档被撑到 1400px、
 * 说明图整列被裁。这里在章节文档内注入一层「兜底 + 版心」样式（见 readCss），
 * 并补上插图放大、章内链接拦截、章内滚动位置记忆。注入发生在 iframe load 回调里，
 * 此时加载遮罩还没撤 → 用户第一眼看到的就是已排好的版面，不会闪一下原书样式。
 */
import { ref, computed, watch, nextTick } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  chapters: { type: Array, default: () => [] },
  index: { type: Number, default: 1 },
  base: { type: String, required: true },
})
const emit = defineEmits(['change', 'select', 'hidesel', 'loaded'])

const frame = ref(null)
const loading = ref(true)
const error = ref('')
let lastDoc = null          // 当前已 setup 的章节文档（onLoad 与兜底定时器共用）
let loadGuard = null        // load 事件兜底定时器

// ---------- 阅读偏好（localStorage 持久化） ----------
const READ_STYLE_ID = 'asc-epub-read-style'
const FONT_STEPS = { s: 16, m: 17, l: 19 }
const LS_STEP = 'asc.epub.fontStep'
const LS_FAMILY = 'asc.epub.fontFamily'

function lsGet(key, fallback) {
  try {
    const v = localStorage.getItem(key)
    return v || fallback
  } catch {
    return fallback   // 隐私模式 / 禁用存储：退回默认，不阻断阅读
  }
}

const fontStep = ref(FONT_STEPS[lsGet(LS_STEP, 'm')] ? lsGet(LS_STEP, 'm') : 'm')
const fontFamily = ref(lsGet(LS_FAMILY, 'serif') === 'sans' ? 'sans' : 'serif')
const previewUrl = ref('')

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

watch(src, () => {
  loading.value = true
  error.value = ''
  // 兜底：个别章节含外链视频 / 超大图，load 可能迟迟不触发（实测一章 >12s），
  // 用户会一直停在「正在还原原文版面…」。到点无论资源是否就绪都按当前文档就绪，
  // 宁可图片后续补上，也不要永久转圈。
  if (loadGuard) clearTimeout(loadGuard)
  loadGuard = setTimeout(() => {
    loadGuard = null
    const doc = frame.value?.contentDocument
    if (doc?.body && doc !== lastDoc) {
      loading.value = false
      setupFrame(doc)
    }
  }, 15000)
})

// ---------- 章节文档内的样式注入 ----------
//
// 三条硬约束（都是实测踩出来的，改之前先看这段）：
// ① 只加 max-width，绝不写 img{height:auto}：行内图标的高度来自 class（.inline10{height:1.02em}），
//    不在 style 属性里，任何 height:auto 兜底都会把 16px 的脚注图标还原成 200×200。
// ② 不写 overflow-x:hidden：它把「看不全但能滚」变成「静默裁掉」，治因靠 max-width。
// ③ 版心用 em（40em ≈ 40 字/行），字号档位一变版心跟着变，不写死 px。

function readCss() {
  const fs = FONT_STEPS[fontStep.value] || 17
  const sans = fontFamily.value === 'sans'
  // 字体切换只覆盖「文本」，代码块回退等宽字体
  const familyRule = sans
    ? `body, body * { font-family: "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", system-ui, sans-serif !important; }
pre, pre *, code, code * { font-family: ui-monospace, Consolas, "Courier New", monospace !important; }`
    : ''
  return `
img, svg, video, canvas, object, embed { max-width: 100% !important; }
table { max-width: 100% !important; }
pre, code { max-width: 100%; overflow-x: auto; }
body { overflow-wrap: break-word; }
body {
  max-width: 40em !important;
  margin: 0 auto !important;
  padding: 28px 32px 64px !important;
  font-size: ${fs}px !important;
  line-height: 1.75 !important;
}
p, li, dd, blockquote { line-height: 1.75 !important; }
h1, h2, h3, h4, h5, h6 { line-height: 1.35 !important; }
${familyRule}
a { color: #6b4ee6 !important; }
::selection { background: rgba(124, 92, 252, .22); }
/* 整页图（封面 / 题图）：放宽版心，图按原比例铺满 */
body.asc-fullbleed { max-width: none !important; padding: 0 !important; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: rgba(0, 0, 0, .18); border-radius: 6px; border: 2px solid #fff; }
::-webkit-scrollbar-thumb:hover { background: rgba(0, 0, 0, .3); }
::-webkit-scrollbar-track { background: transparent; }
`.trim()
}

function injectReadStyle(doc) {
  if (!doc?.head) return
  let st = doc.getElementById(READ_STYLE_ID)
  if (!st) {
    st = doc.createElement('style')
    st.id = READ_STYLE_ID
    doc.head.appendChild(st)
  }
  const css = readCss()
  if (st.textContent !== css) st.textContent = css
  markFullBleed(doc)
}

// 封面 / 题图页：只有一张图、几乎没有文字 → 不套版心（否则大图被缩到 680 宽）
function markFullBleed(doc) {
  const body = doc.body
  if (!body) return
  body.classList.remove('asc-fullbleed')
  const text = (body.textContent || '').replace(/\s+/g, '')
  if (text.length > 60) return
  const media = body.querySelectorAll('img, svg, image')
  if (media.length !== 1) return
  const r = media[0].getBoundingClientRect()
  const vw = doc.documentElement.clientWidth || 1
  if (r.width >= vw * 0.55) body.classList.add('asc-fullbleed')
}

// ---------- 章节内交互：滚动记忆 / 图放大 / 链接拦截 ----------
const scrollMemo = new Map()   // chapter index -> scrollTop
let frameChapter = 1           // 当前文档对应的章号（滚动写记忆用，避免切章瞬间错记到新章）

function onFrameScroll() {
  const win = frame.value?.contentWindow
  if (!win) return
  scrollMemo.set(frameChapter, win.scrollTop || 0)
}

function restoreScroll() {
  const win = frame.value?.contentWindow
  if (!win) return
  const saved = scrollMemo.get(frameChapter) || 0
  if (saved > 8) win.scrollTo(0, saved)   // 阈值：规避 1~2px 的布局抖动
  scrollMemo.set(frameChapter, win.scrollTop || 0)
}

// 章内链接 → zip 内路径（相对当前章解析，兼容 ../Text/x.xhtml 与绝对 /OEBPS/x.xhtml）
function resolveZipPath(href) {
  const curHref = current.value?.href || ''
  try {
    const u = new URL(href, 'http://zip.local/' + curHref)
    let p = u.pathname.replace(/^\/+/, '')
    try { p = decodeURIComponent(p) } catch { /* 编码异常时保持原样 */ }
    return p
  } catch {
    return ''
  }
}

function findChapter(path) {
  if (!path || !props.chapters?.length) return null
  const hit = props.chapters.find(c => c.href === path)
  if (hit) return hit
  const lower = String(path).toLowerCase()   // zip 路径大小写敏感，但书内链接常写成另一种大小写
  return props.chapters.find(c => String(c.href || '').toLowerCase() === lower) || null
}

function onFrameClick(e) {
  const t = e.target
  if (!t || t.nodeType !== 1) return
  // 图：只有「插图」才放大，脚注 / 播放键这类行内小图标不弹
  if (t.tagName === 'IMG') {
    const r = t.getBoundingClientRect()
    if (r.width >= 56) {
      previewUrl.value = t.currentSrc || t.src
      emit('hidesel')
    }
    return
  }
  const a = t.closest?.('a')
  if (!a) return
  const href = a.getAttribute('href') || ''
  if (!href || href.startsWith('#')) return   // 章内脚注锚点：交给浏览器默认行为
  if (/^(https?:)?\/\//i.test(href)) {        // 外链：父页面打开（iframe 无 allow-popups）
    e.preventDefault()
    window.open(href, '_blank', 'noopener')
    return
  }
  // 其它带协议的（geebook:// 视频深链、kindle://、mailto: 等）：原文视图没有可用落点，
  // 必须阻止默认行为 —— 否则浏览器会弹「打开外部应用」，把阅读打断
  if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
    e.preventDefault()
    return
  }
  e.preventDefault()
  const hit = findChapter(resolveZipPath(href))
  // ⚠️ 不拦截的话 iframe 会自己跳到另一章，而父组件的章号不动 → 目录错位、上一章/下一章失效
  if (hit && hit.index !== cur.value) go(hit.index)
  else if (!hit) ElMessage.info('该链接指向书内附加页，原文视图暂不支持跳转，可切到文本视图查看')
}

function onLoad() {
  if (loadGuard) { clearTimeout(loadGuard); loadGuard = null }
  loading.value = false
  // iframe 内是外来文档，加载失败不会抛异常，只能靠内容判断（空文档 / 解析器错误页）
  try {
    const doc = frame.value?.contentDocument
    if (doc && !doc.body?.textContent?.trim() && !doc.querySelector('img, svg, image')) {
      error.value = '该章节没有可显示的内容'
    } else if (doc?.body) {
      setupFrame(doc)
    }
  } catch {
    /* 跨源受限时忽略：不影响阅读 */
  }
  // 无论成功与否都通知父组件（applyHighlights 对无 body 场景有容错）
  emit('loaded', cur.value)
}

// ---------- 选区捕获与划线渲染（划线 / AI 解读 / 转笔记 / 复制的入口） ----------
//
// iframe 内的 mouseup 不会冒泡到父文档，必须挂进章节文档；
// 工具条是父页面的 position:fixed 元素 → 选区矩形要换算到顶层视口坐标。
// 划线恢复与 PDF/Markdown 同口径：selected_text 文本匹配包裹（同章多处出现会都标上）。

const HL_STYLE_ID = 'asc-epub-hl-style'
function injectHlStyle(doc) {
  if (doc.getElementById(HL_STYLE_ID)) return
  const st = doc.createElement('style')
  st.id = HL_STYLE_ID
  st.textContent = `
    .hl-user.hl-yellow { background: rgba(252, 211, 77, .5); }
    .hl-user.hl-green { background: rgba(134, 239, 172, .55); }
    .hl-user.hl-blue { background: rgba(147, 197, 253, .55); }`
  doc.head?.appendChild(st)
}

// 文本匹配包裹（同 StudyView.wrapTextOccurrences 口径，但节点由 iframe 文档创建）
function wrapText(doc, root, text, className) {
  if (!text || !root) return
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes = []
  let n
  while ((n = walker.nextNode())) nodes.push(n)
  for (const node of nodes) {
    const val = node.nodeValue || ''
    if (!val.includes(text)) continue
    if (node.parentElement?.closest('.hl-user')) continue
    const parts = val.split(text)
    if (parts.length === 1) continue
    const frag = doc.createDocumentFragment()
    parts.forEach((part, i) => {
      if (part) frag.appendChild(doc.createTextNode(part))
      if (i < parts.length - 1) {
        const span = doc.createElement('span')
        span.className = className
        span.textContent = text
        frag.appendChild(span)
      }
    })
    node.parentNode.replaceChild(frag, node)
  }
}

function clearHlMarks(doc) {
  doc.querySelectorAll('.hl-user').forEach(el => {
    el.parentNode.replaceChild(doc.createTextNode(el.textContent), el)
  })
  doc.body?.normalize()
}

// 父组件调用：把「当前章」的划线列表重刷进 iframe（先清再按文本匹配包裹）
function applyHighlights(list) {
  const doc = frame.value?.contentDocument
  if (!doc?.body) return
  injectHlStyle(doc)
  clearHlMarks(doc)
  for (const h of list || []) {
    if (h.selected_text) wrapText(doc, doc.body, h.selected_text, `hl-user hl-${h.color}`)
  }
}

function clearSelection() {
  frame.value?.contentWindow?.getSelection()?.removeAllRanges()
}

function onFrameMouseUp() {
  const win = frame.value?.contentWindow
  const sel = win?.getSelection()
  const text = sel?.toString().trim() || ''
  if (!text || !sel.rangeCount) { emit('hidesel'); return }
  const rect = sel.getRangeAt(0).getBoundingClientRect()
  const fr = frame.value.getBoundingClientRect()
  emit('select', {
    text,
    page: cur.value,
    x: Math.min(fr.left + rect.left, window.innerWidth - 320),
    y: fr.top + rect.bottom + 8,
  })
}

function setupFrame(doc) {
  lastDoc = doc
  frameChapter = cur.value
  injectReadStyle(doc)          // 先注样式：markFullBleed 的判定依赖注入后的布局
  injectHlStyle(doc)
  doc.addEventListener('mouseup', onFrameMouseUp)
  doc.addEventListener('mousedown', () => emit('hidesel'))
  doc.addEventListener('click', onFrameClick)
  doc.addEventListener('scroll', onFrameScroll, true)
  restoreScroll()
}

// 字号 / 字体切换：重新注入样式，并按比例守住阅读位置（内容高度会变）
function refreshFrame() {
  const doc = lastDoc
  const win = frame.value?.contentWindow
  if (!doc?.body || !win) return
  const before = doc.documentElement.scrollHeight || 1
  const ratio = (win.scrollTop || 0) / before
  injectReadStyle(doc)
  const after = doc.documentElement.scrollHeight || 1
  win.scrollTo(0, Math.round(ratio * after))
  scrollMemo.set(frameChapter, win.scrollTop || 0)
}

function savePref() {
  try {
    localStorage.setItem(LS_STEP, fontStep.value)
    localStorage.setItem(LS_FAMILY, fontFamily.value)
  } catch {
    /* 存储不可用时只在本次会话生效 */
  }
  refreshFrame()
}

defineExpose({ applyHighlights, clearSelection })

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
  flex-wrap: wrap; row-gap: 6px;
  padding: 8px 16px; background: var(--asc-bg);
  border-bottom: 1px solid var(--asc-divider);
}
.epub-select { width: 240px; min-width: 120px; max-width: 100%; }
.epub-pos { font-size: 12px; color: var(--asc-text-3); white-space: nowrap; }
/* 偏好控件靠右；窄栏时随 flex-wrap 换到第二行，不挤压章节选择 */
.epub-pref { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
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
