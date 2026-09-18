// SSE 流式读取：POST 请求 + 逐事件回调（meta/progress/token/done/error）
//
// base 前缀：默认**同源**（''）——dev 走 vite proxy 转发 /api、生产由后端单端口托管，
// 两者都同源。原实现 dev 下硬编码 'http://127.0.0.1:8000'，换端口（如 ASC_PORT=8010）
// 即静默打错目标。如需覆盖可用 VITE_SSE_BASE。
const SSE_BASE = import.meta.env.VITE_SSE_BASE || ''

// onProgress：分批任务（如长文档摘要的两段式）的进度回调，payload = {done, total}
export async function streamSSE(url, body, onToken, onDone, onError, onMeta, onProgress) {
  const headers = { 'Content-Type': 'application/json' }
  let resp
  try {
    resp = await fetch(SSE_BASE + url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    })
  } catch (e) {
    // 网络层失败（后端没起来 / 被拦截）→ 给出可读提示，不要让它看起来像"卡死"
    throw new Error('无法连接服务，请确认后端已启动后重试')
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    throw new Error(err.detail || `请求失败 ${resp.status}`)
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
      else if (ev === 'error') onError?.(payload.message)
      else if (ev === 'meta') onMeta?.(payload)
      else if (ev === 'progress') onProgress?.(payload)
    }
  }
}
