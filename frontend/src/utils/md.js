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
import { openZoomViewer } from './imgViewer'

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
    // 长回答里图多时懒加载，滚动到再取（经 imgproxy 的外链图同样受益）
    tokens[idx].attrSet('loading', 'lazy')
    tokens[idx].attrSet('decoding', 'async')
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
  // 代码运行（一期 A4）：HTML 页面 / JS 片段。
  // ⚠️⚠️ 命中条件不能只认 lang==='html'：AI 经常漏标语言（``` 直接贴 HTML）、或写成 ```htm。
  //    2026-10-28 实测：这两种形态**没有运行按钮**，而它们恰恰是最常见的「AI 帮我写个页面」。
  const isHtml = lang === 'html' || lang === 'htm' || lang === 'xhtml'
    || (lang === '' && looksLikeHtmlDoc(code))
  const isJs = lang === 'javascript' || lang === 'js'
  const runLang = isHtml ? 'html' : (isJs ? 'js' : '')
  const runBtn = runLang
    ? '<button type="button" class="md-code-run" data-code="' + b64encode(code) + '" data-lang="' + runLang + '">运行</button>' : ''
  // 无语言但认出是 HTML 时，语言标签也显示 html（否则这块代码看着像纯文本，没人会去点按钮）
  const showLang = isHtml ? 'html' : lang
  let inner
  if (showLang && hljs.getLanguage(showLang)) {
    inner = hljs.highlight(code, { language: showLang, ignoreIllegals: true }).value
  } else {
    inner = escapeHtml(code)
  }
  // ⚠️ 无语言标注时**不要**兜底成字面 `text` 标签（曾经这么写过，看着像漏出来的调试文案）。
  //    但空 <span> 必须留着：flex 的 space-between 在只有一个子元素时会把「复制」甩到左边。
  //    判据：_probe_md_lang2_20261024.py（固定输入 V1/V2/V5）+ _probe_md_enhance_bugs_20261023.py 的 B9a/B9b。
  return '<div class="md-code">'
    + '<div class="md-code-head">' + (showLang ? '<span class="md-code-lang">' + showLang + '</span>' : '<span></span>')
    + importBtn
    + runBtn
    + '<button type="button" class="md-code-copy" data-code="' + b64encode(code) + '">复制</button></div>'
    + '<pre class="md-code-pre"><code class="hljs' + (showLang ? ' language-' + showLang : '') + '">' + inner + '</code></pre></div>'
}

// 无语言围栏里判断「这是不是一份 HTML 页面」。判据要严，否则普通代码文本会被塞进 iframe。
// 只认两种：① 以文档/结构标签开头的；② 有成对 <html>/<body> 的（AI 常省掉 doctype）。
function looksLikeHtmlDoc(code) {
  const t = (code || '').replace(/^\s+/, '')
  if (!t) return false
  if (/^<(!doctype\s+html[\s>]|html[\s>]|head[\s>]|body[\s>])/i.test(t)) return true
  if (/^<(div|section|main|form|table|svg|style|script)[\s>]/i.test(t)
    && /<\/html>|<\/body>/i.test(code)) return true
  return false
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
  document.addEventListener('load', onImgLoad, true)   // img 的 load 不冒泡，必须捕获阶段
  window.addEventListener('message', onRunMessage)
  document.addEventListener('keydown', onRunKeydown)
  startMermaidObserver()
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer)
    resizeTimer = setTimeout(layoutMmdBoxes, 200)
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
  // mermaid 图（8 种）与正文图片同一套放大查看：SVG 按 viewBox 重新序列化放大，
  // 不取屏幕上的显示尺寸（宽图在卡片里是横向滚动的，取元素尺寸只能拿到一截）。
  const mmdSvg = t.closest('.mermaid-done svg')
  if (mmdSvg) { openZoomOn(mmdSvg); return }
  const img = t.closest('.md-body img, .ep-block img, .md-preview img')
  if (img) { openZoomOn(img); return }
}

// 正文插图分级（2026-10-26 美观优化）：「独占一个段落且原图宽 ≥160px」才升格为卡片图
// （md-fig → 圆角+细边+轻阴影，见 global.css）。行内图（段里还有文字/其他元素）与
// 小图标保持内联，绝不卡片化。
// ⚠️ CSS 的 p > img:only-child 看不见**文本节点**：「图片+文字」同段也会被误判成独段，
//    所以只能在这里遍历 childNodes 判（文本节点非空白 = 行内图）。
function onImgLoad(e) {
  const img = e.target
  if (!img || img.tagName !== 'IMG') return
  if (!img.closest('.md-body, .ep-block, .md-preview')) return
  const p = img.parentElement
  if (!p || p.tagName !== 'P') return
  if (img.naturalWidth < 160) return
  for (const n of p.childNodes) {
    if (n === img) continue
    if (n.nodeType === 3 && n.textContent.trim()) return
    if (n.nodeType === 1) return
  }
  img.classList.add('md-fig')
}

// 收集同容器内全部可放大对象（正文图 + mermaid 图），点开后可左右切换
function openZoomOn(el) {
  const container = el.closest('.md-body, .ep-block, .md-preview')
  if (!container) return
  const els = Array.from(container.querySelectorAll('img, .mermaid-done svg'))
  if (!els.length) return
  openZoomViewer(els.map(toZoomItem), els.indexOf(el))
}

function toZoomItem(el) {
  if (el.tagName.toLowerCase() !== 'svg') return { url: el.src, revoke: false }
  return { url: mermaidZoomUrl(el), revoke: true }
}

// mermaid 图 → 放大用 SVG（矢量：查看器里再滚轮放大也不糊）。
// 尺寸取「视口 90% x 86%」与「2.5 倍原尺寸」的较小者；比视口还大的图保持原尺寸，
// 交给查看器按容器自适应缩小（仍比卡片里看到的大）。
function mermaidZoomUrl(svg) {
  const nat = svgNaturalSize(svg)
  // 高度上限要把底部工具栏（缩放/旋转，bottom:30px、高 44px → 顶边 =≈ vh-106）算进去。
  //   实测 vh=1100：取 0.86*vh=946 时图底 1023、工具栏顶 1026——只剩 3px，视觉上就贴在一起；
  //   取 vh-200=900 后图底 1000，留 26px 间隙（两者都不遮挡，前者观感差）。
  const k = Math.min(
    (window.innerWidth * 0.9) / nat.w,
    Math.max(320, window.innerHeight - 200) / nat.h,
    2.5
  )
  const built = buildExportSvg(svg, k > 1 ? k : 1)
  const blob = new Blob([built.xml], { type: 'image/svg+xml;charset=utf-8' })
  return URL.createObjectURL(blob)
}

// ---------- 代码运行（一期 A4：HTML/JS · sandbox iframe srcdoc） ----------
// ⚠️ sandbox 只给 allow-scripts、绝不加 allow-same-origin：iframe 跑在 opaque origin，
//    读不到父页面的 DOM / cookie / 本应用数据。
// ⚠️⚠️ 但 opaque origin 有两个连带伤（2026-10-28 实测），它们正是「点了运行像没运行」的真因：
//    ① localStorage / sessionStorage 一碰就抛 SecurityError —— AI 写的页面十有八九用它
//       记主题/待办，脚本在第一次 setItem 处就死掉，屏幕只剩半截静态骨架；
//    ② 沙箱不给 allow-modals，原生 alert 在 Chromium 下**既不弹也不报错**；而放开
//       allow-modals 后 alert 是**渲染进程级**模态，会把整个应用一起冻住。
//    ⇒ 两者都用 srcdoc 里**前置**的垫片顶上（必须在页面脚本之前执行，故不能放末尾）。
// 高度：父页读不到沙箱 iframe 的 contentDocument，只能由 iframe 内部把 scrollHeight
//    经 postMessage 回传；面板按内容长高（否则 100vh 的页面被塞进固定小框，排版全乱）。
// 查看：面板头带「全屏」（应用内浮层，不是新窗口 —— 桌面端 pywebview 会把新窗口交给
//    系统浏览器，切回来就丢了上下文）与「下载 HTML」（存到本地双击就是真浏览器环境）。
// 结构：panel > win > head / out? / body > frame + skel。加这层 win 是为了让全屏态
//    能有「浮窗」的圆角与阴影（panel 自身必须是铺满视口的可点遮罩）。
const RUN_SHIM = '<' + 'script>(function(){'
  // 内存版存储：读不到真存储，但让页面脚本不因 SecurityError 崩掉
  + 'var mem=function(){var b={};return{getItem:function(k){return k in b?b[k]:null},'
  + 'setItem:function(k,v){b[k]=String(v)},removeItem:function(k){delete b[k]},clear:function(){b={}},'
  + 'key:function(i){var ks=Object.keys(b);return i<ks.length?ks[i]:null},'
  + 'get length(){return Object.keys(b).length}}};'
  + 'var broken=false;try{window.localStorage.getItem("__asc_probe");}catch(e){broken=true}'
  + 'if(broken){try{Object.defineProperty(window,"localStorage",{value:mem(),configurable:true})}catch(e){}'
  + 'try{Object.defineProperty(window,"sessionStorage",{value:mem(),configurable:true})}catch(e){}}'
  // 原生弹窗垫片：改用页面内浮条，既是可见反馈又不会阻塞应用
  + 'var tip=function(msg){var d=document.getElementById("__ascTip");'
  + 'if(!d){d=document.createElement("div");d.id="__ascTip";'
  + 'd.style.cssText="position:fixed;left:50%;bottom:14px;transform:translateX(-50%);max-width:86%;'
  + 'padding:8px 14px;border-radius:8px;background:rgba(17,24,39,.92);color:#fff;'
  + 'font:13px/1.5 system-ui,-apple-system,sans-serif;z-index:2147483647;white-space:pre-wrap;'
  + 'box-shadow:0 6px 20px rgba(0,0,0,.28)";'
  + 'if(document.body){document.body.appendChild(d)}'
  + 'else{document.addEventListener("DOMContentLoaded",function(){document.body.appendChild(d)})}}'
  + 'd.textContent=String(msg);d.style.display="block";'
  + 'clearTimeout(d.__t);d.__t=setTimeout(function(){d.style.display="none"},3200)};'
  + 'window.alert=function(m){tip(m)};'
  + 'window.confirm=function(m){tip(m+"（预览环境已自动确认）");return true};'
  + 'window.prompt=function(m,v){tip(m+"（预览环境不支持输入，已返回默认值）");return v===undefined?null:v};'
  + '})();<' + '/script>'

const RUN_BRIDGE = '<' + 'script>(function(){'
  + 'function send(){try{var o=document.getElementById("__out");'
  + 'parent.postMessage({__ascRun:"done",out:o?o.textContent:"",'
  + 'h:document.documentElement.scrollHeight},"*")}catch(e){}}'
  + 'window.addEventListener("load",function(){setTimeout(send,60)});'
  // 多档补发：等异步渲染（字体、图片、定时器改布局）落定后再校一次高度
  + 'setTimeout(send,150);setTimeout(send,700);setTimeout(send,1600);'
  // 滚到边界后把剩余滚动交给外层：iframe 自己滚不动时会「吃掉」滚轮，
  // 表现为滚到底后整条对话纹丝不动，像卡住了。
  + 'function innerCanScroll(t,dy){var el=t;'
  + 'while(el&&el.nodeType===1&&el!==document.body&&el!==document.documentElement){'
  + 'var oy=getComputedStyle(el).overflowY;'
  + 'if(oy==="auto"||oy==="scroll"||oy==="overlay"){'
  + 'var m=el.scrollHeight-el.clientHeight;'
  + 'if(m>1&&((dy<0&&el.scrollTop>0)||(dy>0&&el.scrollTop<m-1)))return true}'
  + 'el=el.parentElement}return false}'
  + 'window.addEventListener("wheel",function(e){try{'
  + 'var dy=e.deltaY;if(typeof dy!=="number")return;'
  + 'if(innerCanScroll(e.target,dy))return;'
  + 'var y=window.scrollY||document.documentElement.scrollTop||0;'
  + 'var max=(document.documentElement.scrollHeight||0)-window.innerHeight;'
  + 'if(max<=1||(dy<0&&y<=1)||(dy>0&&y>=max-1))'
  + 'parent.postMessage({__ascRun:"wheel",dy:dy},"*")'
  + '}catch(x){}},{passive:true});'
  + '})();<' + '/script>'

const RUN_CONSOLE_TAP = 'function __log(s){var o=document.getElementById("__out");if(o)o.textContent+=s+"\\n"}'
  + ';["log","info","warn","error"].forEach(function(k){console[k]=function(){'
  + '__log(Array.prototype.map.call(arguments,String).join(" "));}});'

// 完整文档？有 <html>/<body> 就按文档处理，否则当片段包一层骨架（片段直接进 iframe 会
// 落进 quirks 模式，且没有 charset/viewport，中文与移动端排版都不对）
function isFullHtmlDoc(code) {
  return /<html[\s>]/i.test(code) || /<body[\s>]/i.test(code)
}

function buildHtmlDoc(code) {
  if (!isFullHtmlDoc(code)) {
    return '<!doctype html><html><head><meta charset="utf-8">'
      + '<meta name="viewport" content="width=device-width,initial-scale=1">'
      + RUN_SHIM + '</head><body>' + code + RUN_BRIDGE + '</body></html>'
  }
  let out = code
  // 垫片优先插进 <head> 开头：必须早于页面自己的脚本
  const headOpen = /<head[^>]*>/i.exec(out)
  if (headOpen) {
    const at = headOpen.index + headOpen[0].length
    out = out.slice(0, at) + RUN_SHIM + out.slice(at)
  } else {
    const htmlOpen = /<html[^>]*>/i.exec(out)
    if (htmlOpen) {
      const at = htmlOpen.index + htmlOpen[0].length
      out = out.slice(0, at) + '<head>' + RUN_SHIM + '</head>' + out.slice(at)
    } else {
      out = RUN_SHIM + out
    }
  }
  const bodyEnd = out.lastIndexOf('</body>')
  if (bodyEnd >= 0) out = out.slice(0, bodyEnd) + RUN_BRIDGE + out.slice(bodyEnd)
  else out = out + RUN_BRIDGE
  return out
}

function buildJsDoc(code) {
  return '<!doctype html><html><head><meta charset="utf-8">' + RUN_SHIM + '</head><body>'
    + '<pre id="__out" style="margin:0;padding:10px;font:12px/1.6 ui-monospace,Consolas,monospace;'
    + 'white-space:pre-wrap;word-break:break-word"></pre>'
    + '<' + 'script>' + RUN_CONSOLE_TAP + '\ntry {\n' + code
    + '\n} catch (e) { __log("运行出错: " + e) }\n<' + '/script>'
    + RUN_BRIDGE + '</body></html>'
}

function runAct(text, cls, onClick) {
  const b = document.createElement('button')
  b.type = 'button'
  b.className = cls
  b.textContent = text
  b.addEventListener('click', onClick)
  return b
}

function runCode(btn) {
  const code = b64decode(btn.getAttribute('data-code') || '')
  const lang = btn.getAttribute('data-lang') || ''
  const box = btn.closest('.md-code')
  if (!box || !box.parentNode) return
  const next = box.nextElementSibling
  if (next && next.classList && next.classList.contains('md-code-run-panel')) {
    closeRunPanel(next, btn)
    return
  }
  const isJs = lang === 'js'
  // 兜底：上一轮全屏时被搬到 body 的 panel，若宿主消息已被重渲染/切会话清掉，
  // 它会留在 body 里永远盖着界面 —— 打开新面板前先清掉这类残留。
  document.querySelectorAll('body > .md-code-run-panel').forEach((p) => p.remove())
  // 结构：panel(容器/全屏遮罩) > win(窗口) > head + out? + body > frame + skel
  // ⚠️ 层级不能再压缩：head 要横向铺满、body 要能定位骨架层，两者需求不同。
  const panel = document.createElement('div')
  panel.className = 'md-code-run-panel md-code-run-loading'
  const win = document.createElement('div')
  win.className = 'md-code-run-win'
  const head = document.createElement('div')
  head.className = 'md-code-run-head'
  // 头部三层：状态点 → 短标题 → 沙箱徽标（原来是一长串 12px 灰字，窄屏必截断）
  const dot = document.createElement('span')
  dot.className = 'md-code-run-dot'
  dot.setAttribute('aria-hidden', 'true')
  const title = document.createElement('span')
  title.className = 'md-code-run-title'
  title.textContent = isJs ? '控制台输出' : '页面预览'
  const badge = document.createElement('span')
  badge.className = 'md-code-run-badge'
  badge.textContent = '沙箱隔离'
  badge.title = '脚本可以运行，但读不到本应用的数据'
  const acts = document.createElement('span')
  acts.className = 'md-code-run-acts'
  if (!isJs) acts.appendChild(runAct('全屏', 'md-run-full-btn', () => toggleRunFull(panel)))
  acts.appendChild(runAct('下载 HTML', 'md-code-dl', () => downloadHtml(code)))
  acts.appendChild(runAct('收起', 'md-code-run-close', () => closeRunPanel(panel, btn)))
  head.appendChild(dot)
  head.appendChild(title)
  head.appendChild(badge)
  head.appendChild(acts)

  const out = document.createElement('pre')
  out.className = 'md-code-run-out'
  out.style.display = 'none'
  const body = document.createElement('div')
  body.className = 'md-code-run-body'
  const frame = document.createElement('iframe')
  frame.className = 'md-code-run-frame'
  frame.dataset.mode = isJs ? 'js' : 'html'
  frame.setAttribute('sandbox', 'allow-scripts')
  frame.title = isJs ? '控制台输出' : '页面预览'
  frame.srcdoc = isJs ? buildJsDoc(code) : buildHtmlDoc(code)
  // 首帧前的白屏骨架：srcdoc 解析 + 页面脚本跑完之前 iframe 是纯白，
  // 不铺这一层的话用户会以为「点了没反应」。
  const skel = document.createElement('div')
  skel.className = 'md-code-run-skel'
  skel.textContent = '正在渲染…'
  body.appendChild(frame)
  body.appendChild(skel)

  win.appendChild(head)
  if (isJs) win.appendChild(out)
  win.appendChild(body)
  panel.appendChild(win)
  // 全屏浮层点空白处退出（iframe 内的点击不冒泡到这里，不会误触发）
  panel.addEventListener('click', (e) => {
    if (e.target === panel && panel.classList.contains('md-run-full')) toggleRunFull(panel, false)
  })
  box.parentNode.insertBefore(panel, box.nextSibling)
  markRun(btn, true)
  // 面板紧贴代码块底部，而用户此刻多半正看着代码末尾 —— 不滚这一下，面板就正好落在
  // 视口外（2026-10-30 实测：面板顶边在滚动容器底边下方 10px，看起来像「点了没反应」）。
  startRevealWindow(panel)
  revealRun(panel, true)
  // 骨架兜底：外链资源超时 / 页面脚本死循环时 RUN_BRIDGE 可能回不来，
  // 2.5s 后强制收掉，绝不让它一直转（转着的加载指示比空白更让人焦虑）。
  setTimeout(() => panel.classList.remove('md-code-run-loading'), 2500)
}

// 收起面板：按钮状态必须和面板一起收（否则「运行」按钮永远停在「收起」）
function closeRunPanel(panel, btn) {
  if (!panel) return
  if (panel.classList.contains('md-run-full')) toggleRunFull(panel, false)
  panel.remove()
  markRun(btn, false)
}

// 「运行」按钮的两态：文字 + 实心主色（告诉用户这段代码正在被预览）
function markRun(btn, on) {
  if (!btn) return
  btn.textContent = on ? '收起' : '运行'
  btn.classList.toggle('is-on', !!on)
}

// 全屏时 panel 的落脚点：{ parent, next } 记住原位，退出时插回去
let runFullHome = null

function toggleRunFull(panel, on) {
  if (!panel) return
  const full = typeof on === 'boolean' ? on : !panel.classList.contains('md-run-full')
  panel.classList.toggle('md-run-full', full)
  const b = panel.querySelector('.md-run-full-btn')
  if (b) b.textContent = full ? '退出全屏' : '全屏'
  // 全屏时锁住消息列表滚动，Esc / 遮罩退出后恢复
  document.body.classList.toggle('md-run-full-open', full)
  // ⚠️⚠️ 必须搬出原来的位置：z-index 只在**根层叠上下文**里比大小，
  //    祖先只要有一个 z-index/transform，全屏就会被顶栏、侧栏压住（2026-10-28 实测到）。
  if (full) {
    if (panel.parentNode !== document.body) {
      runFullHome = { parent: panel.parentNode, next: panel.nextSibling }
      document.body.appendChild(panel)
    }
  } else if (runFullHome) {
    const home = runFullHome
    runFullHome = null
    try {
      if (home.parent && home.parent.isConnected) home.parent.insertBefore(panel, home.next)
      else panel.remove()
    } catch (e) {
      panel.remove()
    }
  }
}

function onRunKeydown(e) {
  if (e.key !== 'Escape') return
  const full = document.querySelector('.md-code-run-panel.md-run-full')
  if (full) toggleRunFull(full, false)
}

function downloadHtml(code) {
  triggerDownload(new Blob([code], { type: 'text/html;charset=utf-8' }), '页面-' + mmdStamp() + '.html')
}

// 预览滚到边界后，剩余滚动转给外层对话区（否则滚轮「卡在框里」）。
// 只在内嵌态生效：全屏浮层本身就该自己滚。
function scrollRunOuter(panel, dy) {
  if (!panel || panel.classList.contains('md-run-full')) return
  if (typeof dy !== 'number' || !dy) return
  let sc = panel.__ascScroller
  if (!sc || !sc.isConnected) {
    sc = null
    let p = panel.parentElement
    while (p && p !== document.body && p !== document.documentElement) {
      const oy = getComputedStyle(p).overflowY
      if ((oy === 'auto' || oy === 'scroll' || oy === 'overlay')
        && p.scrollHeight > p.clientHeight + 1) { sc = p; break }
      p = p.parentElement
    }
    if (!sc) sc = document.scrollingElement || document.documentElement
    panel.__ascScroller = sc
  }
  // 手已经接管滚动了，自动滚动窗口立刻作废，免得两股劲打架
  panel.dataset.revealUntil = '0'
  sc.scrollTop += dy
}

function onRunMessage(e) {
  const d = e && e.data
  if (!d) return
  if (d.__ascRun === 'wheel') {
    const hit = Array.prototype.find.call(
      document.querySelectorAll('.md-code-run-frame'), (f) => f.contentWindow === e.source)
    if (hit) scrollRunOuter(hit.closest('.md-code-run-panel'), d.dy)
    return
  }
  if (d.__ascRun !== 'done') return
  // 只写 textContent（不碰 innerHTML）——回传内容永不成 HTML，无二次污染面
  document.querySelectorAll('.md-code-run-frame').forEach((f) => {
    if (f.contentWindow !== e.source) return
    // ⚠️ 不能用 f.parentElement：iframe 外面还有 .md-code-run-body 一层
    const panel = f.closest('.md-code-run-panel')
    if (panel) panel.classList.remove('md-code-run-loading')
    const out = panel && panel.querySelector('.md-code-run-out')
    if (out && typeof d.out === 'string' && d.out.trim()) {
      out.textContent = d.out
      out.style.display = ''
    }
    if (typeof d.h === 'number' && d.h > 0) fitRunFrame(panel, f, d.h)
  })
}

// 面板按内容长高。上限 560px：再高就把整条对话顶出屏幕，改由 iframe 内部滚动。
// JS 分支的下限放低（内容就是一个 pre，没必要留 300px 空框）。
// 滚动最小距离让面板进入视野。block:'nearest' 是幂等的：已完整可见时不动。
// smooth 只在「刚点开」那一次用，后续高度微调立刻跟手（不然会追着动画滚）。
function revealRun(panel, smooth) {
  if (!panel || panel.classList.contains('md-run-full') || !panel.isConnected) return
  if (panel.dataset.revealUntil && Date.now() > Number(panel.dataset.revealUntil)) return
  try {
    panel.scrollIntoView({ block: 'nearest', behavior: smooth ? 'smooth' : 'auto' })
  } catch (e) { panel.scrollIntoView() }
}

// 自动滚动的有效期：用户一动手就作废（否则外链图片慢慢加载、面板反复长高，
// 会把正在往上翻代码的人一次次拽回底部）。
function startRevealWindow(panel) {
  panel.dataset.revealUntil = String(Date.now() + 2500)
  if (panel.dataset.revealBound) return
  panel.dataset.revealBound = '1'
  const stop = () => { panel.dataset.revealUntil = '0' }
  window.addEventListener('wheel', stop, { passive: true, once: true })
  window.addEventListener('touchstart', stop, { passive: true, once: true })
  window.addEventListener('keydown', stop, { once: true })
}

function fitRunFrame(panel, frame, contentH) {
  if (!panel || panel.classList.contains('md-run-full')) return
  const isJs = frame.dataset.mode === 'js'
  const min = isJs ? 90 : 200
  // 上限跟视口走：窗口只有 700 高时，560px 的结果框会盖满整屏，代码一行不剩
  const max = isJs ? 320 : Math.min(560, Math.round(window.innerHeight * 0.72))
  const h = Math.max(min, Math.min(Math.round(contentH) + 2, max))
  frame.style.height = h + 'px'
  // ⚠️⚠️ 高度是 iframe 回传后才确定的：插入时那次滚动按初值算，面板长高后底部会又被切掉
  // （2026-10-30 真实 /chat 实测：601px 的面板只露出 370px，底部藏在输入框后面）。
  // 所以在「刚点开的短窗口内」补一次；用户一动滚轮/键盘就关窗，绝不抢手动滚动。
  revealRun(panel)
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
      // ⚠️️ 主题必须是 default：编辑器（Vditor）自带的 mermaid 不显式给 theme，
      //   走 mermaid 内置默认（default）。这里写 neutral 会让同一张图在对话里变灰、
      //   在编辑器里是淡紫，用户会以为是两套东西。要改就两边一起改。
      theme: 'default',
      // 甘特图默认字号偏小（刻度 10px / 任务名 11px），抬到 13px；
      // 但字号变大后中文分阶段名会被任务条压住（左栏默认只留 75px），故一并加大左边距。
      // 刻度字号在 mermaid 源码里写死 font-size=10，配置改不了，属辅助信息不作要求。
      gantt: { fontSize: 13, sectionFontSize: 13, leftPadding: 150, gridLineStartPadding: 35 },
      // 流程图节点换行宽度（2026-11-02）：默认 200px 对中文太窄 —— 没有词边界就乱断，
      // 实测断出「制作素/材」这种孤字行。280 又偏大（用户反馈「整体再小一号」）→ 240。
      flowchart: { wrappingWidth: 240 },
      // 整体小一号（2026-11-02 用户反馈）：mermaid 默认 16px 在对话流里偏大 → 14px。
      // themeVariables 对所有图型生效（流程图/时序/类图…），甘特字号由上面 gantt 段单列。
      themeVariables: { fontSize: '14px' },
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

// 宽图标题对齐（2026-10-26）
// 甘特图的标题被 mermaid 放在 x = viewBox 宽 / 2，也就是**整张图**的水平中心；
// 而首屏只能看到左侧 clientWidth 一段 → 标题被裁掉一半。实测 vb=1400、可视 692 时
// 标题「三周复习计划」108px 只露出 46px（右侧 62px 落在滚动区外），看着像图表出了问题。
// 修法：把这个标题平移到「首屏可视区中心」，只动这一个 text 的 x，不碰图内容与内边距。
// ⚠️ 只对 .mermaid-scroll（真滚动的宽图）搬运：不滚动的图标题本来就在可视区正中；
//    而 timeline / pie 的标题不在图中轴上（实测 timeline x=249.5 ≠ vb/2 599.5），
//    一刀切会把它们挪到中间 → 只认「顶层居中标题」（甘特用 class=titleText）。
function svgVbWidth(svg) {
  const vb = svg.viewBox && svg.viewBox.baseVal
  if (vb && vb.width) return vb.width
  const p = (svg.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number)
  return p.length === 4 && p[2] > 0 ? p[2] : 0
}

function mmdTitleText(svg) {
  const byClass = svg.querySelector('text.titleText')
  if (byClass) return byClass
  const vbw = svgVbWidth(svg)
  if (!vbw) return null
  // 兜底：顶层直接子 text 里 x 落在水平中心的（图内节点都在 <g> 里，选不到）
  return Array.from(svg.children).find((el) => {
    if (!el.tagName || el.tagName.toLowerCase() !== 'text') return false
    const x = parseFloat(el.getAttribute('x'))
    return Number.isFinite(x) && Math.abs(x - vbw / 2) < 2
  }) || null
}

function centerMmdTitle(node) {
  if (!node || !node.querySelector) return
  const svg = node.querySelector('svg')
  if (!svg) return
  const t = mmdTitleText(svg)
  const vbw = svgVbWidth(svg)
  if (!t || !vbw) return
  const w = node.clientWidth || 0
  const wide = node.classList.contains('mermaid-scroll')
  // 宽图居中于首屏可视区；其余回到 viewBox 中心（不滚时两者等价，resize 也能自愈）
  t.setAttribute('x', String(Math.round(wide && w > 0 ? w / 2 : vbw / 2)))
}

// 宽图提示：Chromium 的 overlay 滚动条不 hover 不可见，需给一行文字线索
function addScrollHint(node) {
  // ⚠️ 去重必须按**卡片**取兄弟：图被 .mmd-box 包住后，node.nextElementSibling 恒为 null，
  //    重复水合时会再插一条 → 卡片内/外各一条提示（实测出现过）。
  const box = node.closest('.mmd-box')
  const anchor = box || node
  // 幂等：清掉残留在卡片内的旧提示（含图内部），保证全页只有一条且位于卡片之后
  if (box) box.querySelectorAll('.mermaid-scroll-hint').forEach((e) => e.remove())
  const next = anchor.nextElementSibling
  if (next && next.classList.contains('mermaid-scroll-hint')) return
  const hint = document.createElement('div')
  hint.className = 'mermaid-scroll-hint'
  hint.textContent = '图表较宽，可左右拖动查看完整内容'
  anchor.after(hint)
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
        // 可读性护栏：宽图（如多节点 LR）被 max-width:100% 缩小后字号会低于 12px，
        // 此时改为按 viewBox 自然宽度渲染，交给容器的 overflow-x:auto 横向滚动。
        // ⚠️ 必须在包卡片之前跑：它用 node.clientWidth 当作正文可用宽。
        fixWideSvg(node)
        // 包一层背景卡片（宽度贴合图），随后把保存按钮挂到卡片上（CSS 的 top/right 即定位）
        wrapMmdBox(node)
        addSaveButton(node)
        // 必须在卡片宽度定下来之后：它要用 node.clientWidth 当首屏可视宽
        centerMmdTitle(node)
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
function svgNaturalSize(svg) {
  const vb = svg.viewBox && svg.viewBox.baseVal
  const box = svg.getBoundingClientRect()
  return {
    w: Math.max(1, Math.ceil(vb && vb.width ? vb.width : box.width)),
    h: Math.max(1, Math.ceil(vb && vb.height ? vb.height : box.height)),
  }
}

// scale 只给「点击放大」用；导出 PNG 不传 → 仍是 viewBox 自然尺寸的 2 倍图
function buildExportSvg(svg, scale) {
  const nat = svgNaturalSize(svg)
  const s = scale && scale > 1 ? scale : 1
  const w = Math.max(1, Math.round(nat.w * s))
  const h = Math.max(1, Math.round(nat.h * s))
  const c = svg.cloneNode(true)
  c.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  c.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  c.setAttribute('width', w)
  c.setAttribute('height', h)
  // 水合时为宽图加过内联宽度/最大宽度，不清掉会裁掉一半
  c.style.maxWidth = 'none'
  c.style.width = w + 'px'
  c.style.height = h + 'px'
  // 宽图标题在页面上被搬到了「首屏可视区中心」（见 centerMmdTitle）；这里导出/放大的是
  // 整张图，必须复位到 viewBox 中心，否则成品图片/放大视图里标题会偏左。
  const ct = mmdTitleText(c)
  const cvbw = svgVbWidth(c)
  if (ct && cvbw) ct.setAttribute('x', String(Math.round(cvbw / 2)))
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
  // ⚠️ 按钮挂在背景卡片 .mmd-box 上，图是它的**兄弟**节点而不是祖先，
  //    只用 closest('.mermaid-done') 会返回 null → 点击静默无反应。
  const boxEl = btn.closest('.mmd-box')
  const node = boxEl ? boxEl.querySelector('.mermaid-done') : btn.closest('.mermaid-done')
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
  // ⚠️⚠️ 判重必须查**按钮真正的载体**：按钮挂在 .mmd-box 上、是 node 的**兄弟**，
  //   所以 `node.querySelector('[data-mmd-save]')` 永远查不到刚挂上去的那个 →
  //   每次重新水合都会再挂一个。实测（2026-10-26）：思维导图卡里 3 个、类图/甘特/饼图各 2 个，
  //   完全重叠看不出重复，但 3 个 .55 透明度的按钮叠起来 ≈ .91 不透明 → 各卡片按钮深浅不一。
  const host = node.closest('.mmd-box') || node
  const existing = host.querySelectorAll('[data-mmd-save]')
  if (existing.length) {
    // 兜底：老版本叠挂的重复按钮（热更新后还留在 DOM 里）顺手清掉，只留第一个
    for (let k = 1; k < existing.length; k++) existing[k].remove()
    return
  }
  const btn = document.createElement('button')
  btn.type = 'button'
  btn.className = 'mermaid-save'
  btn.setAttribute('data-mmd-save', '1')
  btn.title = '保存为 PNG 图片'
  btn.textContent = '保存图片'
  // 挂到背景卡片上：卡片宽度贴合图，按钮的 top:6px/right:8px 正好落在卡片内边距区；
  // 宽图横向滚动时按钮也不跟着内容跑。
  host.appendChild(btn)
  // rAF：等 fixWideSvg 把宽图改成自然宽度之后再量尺寸
  requestAnimationFrame(() => placeSaveButton(node, btn))
}

// 按钮贴图右边缘（图比容器窄时不要飘到容器最右边）
function placeSaveButton(node, btn) {
  const svg = node.querySelector('svg')
  if (!svg) return
  // 挂在背景卡片上时，按钮定位交给 CSS（top/right），无需再按图宽反算
  if (btn.parentElement && btn.parentElement.classList.contains('mmd-box')) return
  const sn = svg.getBoundingClientRect()
  const nn = node.getBoundingClientRect()
  const gap = nn.right - sn.right
  // 宽图横向滚动时 svg 右边缘在容器外（gap 为负）→ 按钮贴容器右边缘
  btn.style.right = Math.max(2, Math.round(gap) + 6) + 'px'
}

// ---------- 图背景卡片：宽度贴合图本身（2026-10-26） ----------
// ⚠️⚠️ 不能只靠 CSS。两种纯 CSS 做法实测都不可用：
//   ① 背景直接挂 .mermaid-done：它是 block、宽度撑满答案列 → 类图(169px)右侧 557px 全是空白底色；
//   ② 给 svg 包 display:inline-block 外层：svg 只有 max-width:100%，与"外层宽由内容决定"形成循环依赖
//      → 浏览器退回 SVG 默认 300px，时序图 650→300、饼图 546→300，图被缩小一半。
// 所以：外层 .mmd-box 承载背景，宽度由 JS 按 viewBox 精确计算；内层 .mermaid-done 继续负责横向滚动。
function mmdAvailWidth(node) {
  const box = node.closest('.mmd-box')
  const host = box ? box.parentElement : node.parentElement
  if (!host) return 0
  const cs = getComputedStyle(host)
  const avail = host.clientWidth
    - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0)
  return Math.max(0, Math.floor(avail))
}

function layoutMmdBox(box) {
  const node = box.querySelector('.mermaid-done')
  const svg = node && node.querySelector('svg')
  if (!svg) return
  const vb = svg.viewBox && svg.viewBox.baseVal
  if (!vb || !vb.width) return
  const cs = getComputedStyle(box)
  // box-sizing:border-box → padding 与 border 都要算进 width，否则宽图会因 2px 边框溢出正文列
  const chrome = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0)
    + (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0)
  const natW = Math.round(vb.width)
  const avail = mmdAvailWidth(node)
  // 窄图适度放大（2026-11-02 三轮调参）：**只救真正的小图** —— 1.6× 无门槛翻车、
  // 1.35×（门槛 420px）用户仍反馈「有点大」。
  // 三轮：门槛收紧到 < min(340px, 50%可用宽)（常见竖流程图 ~420px 不再被放大）；
  // 倍率 1.25、上限 80%可用宽 —— 小图只是「提一口气」，不追大。
  // SVG 是矢量放大不糊；宽图不动（.mermaid-scroll 走横向滚动）。
  // 导出/放大读 viewBox 自然尺寸，不吃这里的显示宽。
  let drawW = natW
  if (!node.classList.contains('mermaid-scroll') && avail
      && natW < Math.min(340, Math.round(avail * 0.5))) {
    drawW = Math.max(natW, Math.min(Math.round(natW * 1.25), Math.round(avail * 0.8)))
    if (drawW > natW) {
      // mermaid 内联 style="max-width:<自然宽>px"，不盖掉它放大会被顶回原尺寸
      svg.style.maxWidth = '100%'
      svg.setAttribute('width', drawW)
      svg.style.height = 'auto'
    }
  }
  const want = drawW + Math.round(chrome)
  box.style.width = (avail ? Math.min(want, avail) : want) + 'px'
}

function layoutMmdBoxes() {
  // 先定宽度，标题居中要用新的 clientWidth（窗口/侧栏变化后必须重算）
  document.querySelectorAll('.mmd-box').forEach(layoutMmdBox)
  document.querySelectorAll('.mermaid-done').forEach(centerMmdTitle)
  relayoutSaveButtons()
}

function wrapMmdBox(node) {
  const parent = node.parentElement
  if (!parent) return
  if (parent.classList.contains('mmd-box')) { layoutMmdBox(parent); return }
  const box = document.createElement('div')
  box.className = 'mmd-box'
  parent.insertBefore(box, node)
  box.appendChild(node)
  layoutMmdBox(box)
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
