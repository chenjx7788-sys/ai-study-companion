// SSE 流式读取：POST 请求 + 逐事件回调（meta/token/done/error）
// dev 模式直连后端，绕过 vite 代理（vite 代理会缓冲 SSE 流式响应导致一次性返回）
export async function streamSSE(url, body, onToken, onDone, onError, onMeta) {
  const base = import.meta.env.DEV ? 'http://127.0.0.1:8000' : ''
  const resp = await fetch(base + url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
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
    }
  }
}
