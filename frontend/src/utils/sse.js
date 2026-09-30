// SSE 流式读取：POST 请求 + 逐事件回调（meta/progress/token/done/error）
//
// base 前缀：默认**同源**（''）——dev 走 vite proxy 转发 /api、生产由后端单端口托管，
// 两者都同源。原实现 dev 下硬编码 'http://127.0.0.1:8000'，换端口（如 ASC_PORT=8010）
// 即静默打错目标。如需覆盖可用 VITE_SSE_BASE。
const SSE_BASE = import.meta.env.VITE_SSE_BASE || ''

// 「未配置模型」信号（与 api/http.js 的 NEED_SETUP 一致）：命中时弹「中文原因 + 前往配置」引导
const NEED_SETUP = '[NEED_SETUP]'
function guideSetup(message) {
  if (!message || typeof message !== 'string' || !message.includes(NEED_SETUP)) return false
  const reason = message.replace(NEED_SETUP, '').replace(/^\d{3}:\s*/, '').trim() || '尚未配置可用的 AI 模型'
  import('element-plus').then(({ ElMessage }) => {
    ElMessage({
      type: 'warning',
      duration: 8000,
      message: (() => {
        const span = document.createElement('span')
        span.innerHTML = reason + '，'
        const a = document.createElement('a')
        a.textContent = '前往配置'
          a.href = '/settings'
          a.style.cssText = 'color:#409eff;cursor:pointer;text-decoration:underline;font-weight:600'
          a.onclick = (ev) => { ev.preventDefault(); window.location.href = '/settings' }
        span.appendChild(a)
        return span
      })(),
    })
  })
  return true
}

// onProgress：分批任务（如长文档摘要的两段式）的进度回调，payload = {done, total}
// signal：可选的 AbortSignal（WP17 侧栏订阅要在卸载时断开长连接）。
//   ⚠️ 长连接（SSE）**必须**能断开：否则每次订阅都在后端留一条不死连接 + 一个订阅者队列。
export async function streamSSE(url, body, onToken, onDone, onError, onMeta, onProgress, signal) {
  const headers = { 'Content-Type': 'application/json' }
  let resp
  try {
    resp = await fetch(SSE_BASE + url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal,
    })
  } catch (e) {
    // ⚠️ 主动中断**不是**故障：原样抛 AbortError，调用方据此区分「我关的」与「连不上」
    if (e && e.name === 'AbortError') throw e
    // 网络层失败（后端没起来 / 被拦截）→ 给出可读提示，不要让它看起来像"卡死"
    throw new Error('无法连接服务，请确认后端已启动后重试')
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    const detail = err.detail || `请求失败 ${resp.status}`
    const e = new Error(detail)
    e.needSetup = guideSetup(detail)   // HTTP 层未配置：命中则已弹引导，调用方据此跳过自身 error 提示
    throw e
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const events = buffer.split('\n\n')
    buffer = events.pop()
    for (const evt of events) {
      const lines = evt.split('\n')
      const ev = lines.find(l => l.startsWith('event:'))?.slice(6).trim()
      const dataLine = lines.find(l => l.startsWith('data:'))?.slice(5)
      if (!ev || !dataLine) continue
      const payload = JSON.parse(dataLine)
      if (ev === 'token') onToken?.(payload.t)
      else if (ev === 'done') onDone?.(payload)
      else if (ev === 'error') {
        // 未配置模型：额外弹「前往配置」引导；仍回调 onError（剥掉信号前缀）以保证调用方复位 loading
        const msg = payload.message || ''
        if (msg.includes(NEED_SETUP)) {
          guideSetup(msg)
          onError?.(msg.replace(NEED_SETUP, ''))
        } else {
          onError?.(msg)
        }
      }
      else if (ev === 'meta') onMeta?.(payload)
      else if (ev === 'progress') onProgress?.(payload)
    }
  }
}
