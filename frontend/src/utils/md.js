// 统一的 Markdown 渲染器工厂。
//
// 存在的唯一理由：**外链必须新窗口打开**，而这条规则要写在每个 md 实例上，
// 项目里有 9 处各自 `new MarkdownIt(...)` —— 逐处复制粘贴必然漂移（已经漂移过：
// 只有 EphemeralView / PodcastView 开了 linkify，其余没开）。
//
// 为什么外链必须 target=_blank（两条都是实测结论，不是习惯）：
//   ① 浏览器里：同标签打开会直接把 SPA 导航走，返回键回不到原页面状态；
//   ② 桌面端 pywebview：`OPEN_EXTERNAL_LINKS_IN_BROWSER` 默认 True，
//      EdgeChromium 平台把「新窗口请求」交给 `webbrowser.open()`（见
//      webview/platforms/edgechromium.py:on_new_window_request）→ 系统浏览器打开；
//      若同标签打开，整个桌面应用会被导航到外站且**没有返回入口**。
//
//
// ---------- 图片走本站只读代理（2026-09-23） ----------
// ⚠️ 与上面「外链新窗口」不同，这条**是**为所有调用方统一加的兜底，理由如下（实测）：
//   微信 `mmbiz.qpic.cn` 对带**非微信域 Referer** 的请求返回一张 140x140 占位图
//   （HTTP 200，图上印着「此图片来自微信公众平台未经允许不可引用」）；
//   而浏览器从本地应用加载外链图，必然带 `Referer: http://127.0.0.1:<port>/`；
//   用 `referrerpolicy="no-referrer"` 去掉 Referer 后会换成 **HTTP 400**
//   （浏览器特征头 `Sec-Fetch-*` + 无 Referer）—— 三组浏览器矩阵实测结论一致。
//   ⇒ 浏览器侧无解，只能让**后端**去取（后端不带 `Sec-Fetch-*`，被当普通抓取放行）。
//   见 `backend/app/routers/imgproxy.py`。
// ⚠️ 代理取不到时会 302 回原 URL —— 所以这条改写**不会让原本能显示的图变不能显示**。
// ⚠️ 只改 http(s) 外链；站内地址（`/api/assets/…`，已本地化的配图）原样保留。
// ⚠️ 工厂**不设隐式默认值**：调用方传什么就是什么，避免迁移某个页面时行为被悄悄改掉。
//
// ---------- 回答呈现增强（2026-10-23 · P0/P1/P2） ----------
// 以下对所有 createMd 调用方统一生效（统一正是本工厂存在的意义）：
//   ① 代码块：highlight.js 语法高亮 + 头部条（语言标签 + 复制按钮）；
//   ② mermaid 代码块：env.streaming 为真（流式输出中）时按代码原文显示；
//      否则输出 .mermaid-pending 占位，由 MutationObserver 去抖水合（懒加载 mermaid，
//      gzip ~500KB，只在真出现流程图时才下载）；语法错误降级为代码块 + 错误提示，
//      绝不让整条回答崩掉；
//   ③ AI 输出一律走 renderMd()（DOMPurify 消毒）再 v-html，不要直接 md.render。
// 交互（复制按钮 / 图片点击放大）走 document 级事件委托，
// 由 main.js 调 installMdEnhancements() 安装一次。
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import DOMPurify from 'dompurify'
import { openImgViewer } from './imgViewer'

export function createMd(opts = {}) {
  const md = new MarkdownIt(opts)
  // link_open 默认没有显式规则函数（走 renderToken），所以自己给一个兜底
  const base = md.renderer.rules.link_open || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    // ⚠️ 只对**外链**加（http(s):// 与 // 开头）。
    //    站内相对链接（/help、#anchor）加 target 会被桌面端 pywebview 交给系统浏览器，
    //    直接跳出应用外壳；SPA 内链本就应该同窗口交给 router。
    const hi = tokens[idx].attrIndex('href')
    const href = hi >= 0 ? tokens[idx].attrs[hi][1] : ''
    if (/^(https?:)?\/\//i.test(href)) {
      tokens[idx].attrSet('target', '_blank')
      tokens[idx].attrSet('rel', 'noopener noreferrer')
    }
    return base(tokens, idx, options, env, self)
  }
  // 图片：外链改走只读代理（见文件头「图片走本站只读代理」）。
  // ⚠️ markdown-it 的 `rules.image` 默认实现负责把 alt 从 children 渲染成文本，
  //    所以必须**先改 src 再调它**（自己重写一份等于把 alt/title 行为也接管了）。
  const baseImg = md.renderer.rules.image || ((t, i, o, e, self) => self.renderToken(t, i, o))
  md.renderer.rules.image = (tokens, idx, options, env, self) => {
    const at = tokens[idx].attrIndex('src')
    if (at >= 0) {
      const proxied = proxiedImageSrc(tokens[idx].attrs[at][1])
      if (proxied !== tokens[idx].attrs[at][1]) tokens[idx].attrs[at][1] = proxied
    }
    return baseImg(tokens, idx, options, env, self)
  }
  // 代码块：统一走增强 fence（高亮 + 头部条 + mermaid 分流）
  md.renderer.rules.fence = fenceRule
  return md
}

// 只读图片代理：把外链图交给后端取回（原因见文件头注释）。
const IMG_PROXY = '/api/imgproxy?url='

function proxiedImageSrc(src) {
  return /^https?:\/\//i.test(src) ? IMG_PROXY + encodeURIComponent(src) : src
}

// 渲染 + 消毒：AI 输出进 v-html 的唯一入口。
// env.streaming = true 时 mermaid 块按代码原文渲染（流式期间不触发水合）。
export function renderMd(md, text, env = {}) {
  return DOMPurify.sanitize(md.render(text || '', env), { ADD_ATTR: ['target'] })
}

// ---------- 代码块增强 ----------
function fenceRule(tokens, idx, options, env) {
  const token = tokens[idx]
  const lang = (token.info || '').trim().split(/\s+/)[0].toLowerCase().replace(/[^a-z0-9+#-]/g, '')
  const code = token.content || ''
  if (lang === 'mermaid' && !(env && env.streaming)) {
    return '<div class="mermaid-pending" data-mmd="' + b64encode(code) + '"><div class="mermaid-hint">图表渲染中…</div></div>'
  }
  return codeBlockHtml(lang, code, env)
}

function codeBlockHtml(lang, code, env) {
  // 技能文件草稿（P5）：`skill` 围栏且内容本身是完整技能文件（内含 ```skill 清单围栏）。
  // 只在对话页（env.skillImport）出「导入为技能」按钮——技能编辑器的预览里出这个按钮没有着陆点。
  const isSkillFile = lang === 'skill' && code.indexOf('```skill') !== -1
  const importBtn = (isSkillFile && env && env.skillImport)
    ? '<button type="button" class="md-code-import-skill" data-code="' + b64encode(code) + '">导入为技能</button>' : ''
  // 代码运行（一期 A4）：仅 HTML/JS，sandbox iframe srcdoc（只 allow-scripts，绝不加 same-origin）
  const runnable = lang === 'html' || lang === 'javascript' || lang === 'js'
  const runBtn = runnable
    ? '<button type="button" class="md-code-run" data-code="' + b64encode(code) + '" data-lang="' + lang + '">运行</button>' : ''
  let inner
  if (lang && hljs.getLanguage(lang)) {
    inner = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
  } else {
    inner = escapeHtml(code)
  }
  return '<div class="md-code">'
    + '<div class="md-code-head">' + (lang ? '<span class="md-code-lang">' + lang + '</span>' : '<span></span>')
    + importBtn
    + runBtn
    + '<button type="button" class="md-code-copy" data-code="' + b64encode(code) + '">复制</button></div>'
    + '<pre class="md-code-pre"><code class="hljs' + (lang ? ' language-' + lang : '') + '">' + inner + '</code></pre></div>'
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

// UTF-8 安全的 base64（代码块内容含中文/emoji 时 btoa 直接炸）
function b64encode(str) {
  const bytes = new TextEncoder().encode(str)
  let bin = ''
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
  }
  return btoa(bin)
}

function b64decode(b64) {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new TextDecoder().decode(bytes)
}

// ---------- 全局交互委托（复制按钮 / 图片点击放大） ----------
let installed = false
let resizeTimer = null

export function installMdEnhancements() {
  if (installed || typeof document === 'undefined') return
  installed = true
  document.addEventListener('click', onDocClick)
  window.addEventListener('message', onRunMessage)
  startMermaidObserver()
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer)
    resizeTimer = setTimeout(relayoutSaveButtons, 200)
  })
}

function onDocClick(e) {
  const t = e.target
  if (!t || !t.closest) return
  const runBtn = t.closest('.md-code-run')
  if (runBtn) { runCode(runBtn); return }
  const copyBtn = t.closest('.md-code-copy')
  if (copyBtn) { copyCode(copyBtn); return }
  const saveBtn = t.closest('[data-mmd-save]')
  if (saveBtn) { saveMermaid(saveBtn); return }
  const img = t.closest('.md-body img, .ep-block img, .md-preview img')
  if (img) {
    const container = img.closest('.md-body, .ep-block, .md-preview')
    if (container) openImgViewer(container, img)
  }
}

// ---------- 代码运行（一期 A4：仅 HTML/JS · sandbox iframe srcdoc） ----------
// ⚠️ sandbox 只给 allow-scripts、绝不加 allow-same-origin：iframe 跑在 opaque origin，
//    读不到父页面的 DOM / cookie / localStorage。console 输出经 postMessage 回传面板。
const RUN_BRIDGE = '<' + 'script>window.addEventListener("load",function(){setTimeout(function(){'
  + 'var o=document.getElementById("__out");'
  + 'parent.postMessage({__ascRun:"done",out:o?o.textContent:""},"*")},50)})<' + '/script>'
const RUN_CONSOLE_TAP = 'function __log(s){var o=document.getElementById("__out");if(o)o.textContent+=s+"\\n"}'
  + ';["log","info","warn","error"].forEach(function(k){console[k]=function(){'
  + '__log(Array.prototype.map.call(arguments,String).join(" "));}});'

function runCode(btn) {
  const code = b64decode(btn.getAttribute('data-code') || '')
  const lang = btn.getAttribute('data-lang') || ''
  const box = btn.closest('.md-code')
  if (!box || !box.parentNode) return
  const next = box.nextElementSibling
  if (next && next.classList && next.classList.contains('md-code-run-panel')) {
    next.remove()
    btn.textContent = '运行'
    return
  }
  const panel = document.createElement('div')
  panel.className = 'md-code-run-panel'
  const head = document.createElement('div')
  head.className = 'md-code-run-head'
  head.textContent = '运行结果（沙箱隔离：代码无法访问本应用数据）'
  const out = document.createElement('pre')
  out.className = 'md-code-run-out'
  out.style.display = 'none'
  const frame = document.createElement('iframe')
  frame.className = 'md-code-run-frame'
  frame.setAttribute('sandbox', 'allow-scripts')
  if (lang === 'html') {
    frame.srcdoc = code + RUN_BRIDGE
  } else {
    frame.srcdoc = '<!doctype html><html><head><meta charset="utf-8"></head><body>'
      + '<pre id="__out"></pre>'
      + '<' + 'script>' + RUN_CONSOLE_TAP + '\ntry {\n' + code
      + '\n} catch (e) { __log("运行出错: " + e) }\n<' + '/script>'
      + RUN_BRIDGE + '</body></html>'
  }
  panel.appendChild(head)
  panel.appendChild(out)
  panel.appendChild(frame)
  box.parentNode.insertBefore(panel, box.nextSibling)
  btn.textContent = '关闭'
}

function onRunMessage(e) {
  const d = e && e.data
  if (!d || d.__ascRun !== 'done') return
  // 只写 textContent（不碰 innerHTML）——回传内容永不成 HTML，无二次污染面
  document.querySelectorAll('.md-code-run-frame').forEach((f) => {
    if (f.contentWindow === e.source) {
      const out = f.parentElement && f.parentElement.querySelector('.md-code-run-out')
      if (out && typeof d.out === 'string' && d.out.trim()) {
        out.textContent = d.out
        out.style.display = ''
      }
    }
  })
}

function copyCode(btn) {
  const text = b64decode(btn.getAttribute('data-code') || '')
  const done = () => {
    if (btn.dataset.copied) return
    btn.dataset.copied = '1'
    const old = btn.textContent
    btn.textContent = '已复制'
    setTimeout(() => { btn.textContent = old; delete btn.dataset.copied }, 1200)
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done))
  } else {
    fallbackCopy(text, done)
  }
}

function fallbackCopy(text, done) {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.cssText = 'position:fixed;opacity:0'
  document.body.appendChild(ta)
  ta.select()
  try { document.execCommand('copy'); done() } catch { /* 忽略 */ }
  ta.remove()
}

// ---------- mermaid 懒加载水合 ----------
let mermaidPromise = null

function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then((mod) => {
      const mermaid = mod.default || mod
      mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'neutral',
      // 甘特图默认字号偏小（刻度 10px / 任务名 11px），抬到 13px；
      // 但字号变大后中文分阶段名会被任务条压住（左栏默认只留 75px），故一并加大左边距。
      // 刻度字号在 mermaid 源码里写死 font-size=10，配置改不了，属辅助信息不作要求。
      gantt: { fontSize: 13, sectionFontSize: 13, leftPadding: 150, gridLineStartPadding: 35 },
    })
      return mermaid
    })
  }
  return mermaidPromise
}

let mmdSeq = 0

// 缩放后字号低于 12px（scale < 0.75）时保留自然宽度，让容器横向滚动
function fixWideSvg(node) {
  const svg = node.querySelector('svg')
  if (!svg) return
  const vb = svg.viewBox && svg.viewBox.baseVal ? svg.viewBox.baseVal.width : 0
  const boxW = node.clientWidth || 0
  if (!vb || !boxW) return
  if (boxW / vb < 0.75) {
    svg.style.maxWidth = 'none'
    svg.setAttribute('width', Math.round(vb))
    svg.style.height = 'auto'
    node.classList.add('mermaid-scroll')
    addScrollHint(node)
  }
}

// 宽图提示：Chromium 的 overlay 滚动条不 hover 不可见，需给一行文字线索
function addScrollHint(node) {
  const next = node.nextElementSibling
  if (next && next.classList.contains('mermaid-scroll-hint')) return
  const hint = document.createElement('div')
  hint.className = 'mermaid-scroll-hint'
  hint.textContent = '图表较宽，可左右拖动查看完整内容'
  node.after(hint)
}

async function hydrateMermaid() {
  const nodes = document.querySelectorAll('.mermaid-pending:not([data-mmd-state])')
  if (!nodes.length) return
  let mermaid
  try { mermaid = await loadMermaid() } catch { return }   // 依赖加载失败：保留占位，下轮 mutation 再试
  for (const node of nodes) {
    node.dataset.mmdState = 'busy'
    const src = b64decode(node.getAttribute('data-mmd') || '')
    const id = 'mmd-' + (++mmdSeq)
    try {
      const { svg } = await mermaid.render(id, src)
      if (node.isConnected) {
        node.innerHTML = svg
        node.dataset.mmdState = 'done'
        node.classList.add('mermaid-done')
        addSaveButton(node)
        // 可读性护栏：宽图（如多节点 LR）被 max-width:100% 缩小后字号会低于 12px，
        // 此时改为按 viewBox 自然宽度渲染，交给容器的 overflow-x:auto 横向滚动。
        fixWideSvg(node)
      }
    } catch (err) {
      // mermaid 渲染失败会在 body 残留 #d<id> 节点，必须清掉
      const stray = document.getElementById('d' + id)
      if (stray) stray.remove()
      if (node.isConnected) {
        node.dataset.mmdState = 'error'
        node.innerHTML = '<div class="mermaid-error">图表语法有误，已按原文显示</div>' + codeBlockHtml('mermaid', src)
      }
    }
  }
}

// ---------- mermaid 图导出为 PNG ----------
// mermaid 的输出里文字可能是 <foreignObject>（htmlLabels）也可能是 <text>，
// 两者经 data URL 再 drawImage 到 canvas 都能正常绘制（2026-10-23 对 8 种图实测，
// 判据用「删掉文字层后画面是否变化」，中文未丢）。
const MMD_NAMES = {
  'flowchart-v2': '流程图', flowchart: '流程图', sequence: '时序图',
  'stateDiagram-v2': '状态图', stateDiagram: '状态图', state: '状态图',
  classDiagram: '类图', class: '类图',
  gantt: '甘特图', pie: '饼图', mindmap: '思维导图', timeline: '时间线',
  journey: '旅程图', er: 'ER图', quadrantChart: '象限图', sankey: '桑基图',
  xychart: 'XY图', gitGraph: 'Git图', requirement: '需求图',
}

// 导出用 SVG：按 viewBox 自然尺寸（不能用屏幕上的缩放尺寸，否则宽图只导出一截），
// 补白底（mermaid 的 svg 背景透明，导出的 PNG 否则会是透明底）。
function buildExportSvg(svg) {
  const vb = svg.viewBox && svg.viewBox.baseVal
  const box = svg.getBoundingClientRect()
  const w = Math.max(1, Math.ceil(vb && vb.width ? vb.width : box.width))
  const h = Math.max(1, Math.ceil(vb && vb.height ? vb.height : box.height))
  const c = svg.cloneNode(true)
  c.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  c.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  c.setAttribute('width', w)
  c.setAttribute('height', h)
  // 水合时为宽图加过内联宽度/最大宽度，不清掉会裁掉一半
  c.style.maxWidth = 'none'
  c.style.width = w + 'px'
  c.style.height = h + 'px'
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
  bg.setAttribute('x', '0')
  bg.setAttribute('y', '0')
  bg.setAttribute('width', String(w))
  bg.setAttribute('height', String(h))
  bg.setAttribute('fill', '#ffffff')
  c.insertBefore(bg, c.firstChild)
  return { xml: new XMLSerializer().serializeToString(c), w: w, h: h }
}

function mmdStamp() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' +
    p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds())
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  setTimeout(() => { URL.revokeObjectURL(url); a.remove() }, 5000)
}

async function svgToPngBlob(xml, w, h) {
  // 2 倍图保证清晰；超长图会撞 canvas 边长上限，按最长边自适应降倍
  const scale = Math.min(2, Math.max(1, 12000 / Math.max(w, h)))
  const img = new Image()
  img.decoding = 'sync'
  await new Promise((res, rej) => {
    img.onload = res
    img.onerror = () => rej(new Error('svg load fail'))
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(xml)
  })
  const cv = document.createElement('canvas')
  cv.width = Math.max(1, Math.round(w * scale))
  cv.height = Math.max(1, Math.round(h * scale))
  const ctx = cv.getContext('2d')
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, cv.width, cv.height)
  ctx.drawImage(img, 0, 0, cv.width, cv.height)
  const blob = await new Promise((res) => cv.toBlob(res, 'image/png'))
  if (!blob) throw new Error('toBlob fail')
  return blob
}

async function saveMermaid(btn) {
  const node = btn.closest('.mermaid-done')
  const svg = node && node.querySelector('svg')
  if (!svg || btn.dataset.busy) return
  const old = btn.textContent
  btn.dataset.busy = '1'
  btn.textContent = '导出中…'
  try {
    const built = buildExportSvg(svg)
    const blob = await svgToPngBlob(built.xml, built.w, built.h)
    const kind = svg.getAttribute('aria-roledescription') || 'diagram'
    triggerDownload(blob, (MMD_NAMES[kind] || kind) + '-' + mmdStamp() + '.png')
    btn.textContent = '已保存'
  } catch (err) {
    btn.textContent = '导出失败'
  }
  setTimeout(() => { btn.textContent = old }, 1500)
  setTimeout(() => { delete btn.dataset.busy }, 800)
}

function addSaveButton(node) {
  if (node.querySelector('[data-mmd-save]')) return
  const btn = document.createElement('button')
  btn.type = 'button'
  btn.className = 'mermaid-save'
  btn.setAttribute('data-mmd-save', '1')
  btn.title = '保存为 PNG 图片'
  btn.textContent = '保存图片'
  node.appendChild(btn)
  // rAF：等 fixWideSvg 把宽图改成自然宽度之后再量尺寸
  requestAnimationFrame(() => placeSaveButton(node, btn))
}

// 按钮贴图右边缘（图比容器窄时不要飘到容器最右边）
function placeSaveButton(node, btn) {
  const svg = node.querySelector('svg')
  if (!svg) return
  const sn = svg.getBoundingClientRect()
  const nn = node.getBoundingClientRect()
  const gap = nn.right - sn.right
  // 宽图横向滚动时 svg 右边缘在容器外（gap 为负）→ 按钮贴容器右边缘
  btn.style.right = Math.max(2, Math.round(gap) + 6) + 'px'
}

function relayoutSaveButtons() {
  document.querySelectorAll('.mermaid-done').forEach((node) => {
    const btn = node.querySelector('[data-mmd-save]')
    if (btn) placeSaveButton(node, btn)
  })
}

function startMermaidObserver() {
  let timer = null
  const observer = new MutationObserver(() => {
    if (timer) return
    timer = setTimeout(() => { timer = null; hydrateMermaid() }, 300)
  })
  observer.observe(document.body, { childList: true, subtree: true })
}
