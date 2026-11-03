// SSE 流式读取：POST 请求 + 逐事件回调（meta/progress/token/done/error）
//
// base 前缀：默认**同源**（''）——dev 走 vite proxy 转发 /api、生产由后端单端口托管，
// 两者都同源。原实现 dev 下硬编码 'http://127.0.0.1:8000'，换端口（如 ASC_PORT=8010）
// 即静默打错目标。如需覆盖可用 VITE_SSE_BASE。
import { h } from 'vue'

const SSE_BASE = import.meta.env.VITE_SSE_BASE || ''

// 「未配置模型」信号（与 api/http.js 的 NEED_SETUP 一致）：命中时弹「中文原因 + 前往配置」引导
const NEED_SETUP = '[NEED_SETUP]'
// ⚠️ message 必须传 **VNode**（h(...)），不能传真实 DOM 元素：
//    ElMessage 的 message 只接受 String / VNode / Function，塞 HTMLElement 会被当成普通对象
//    → 提示框里显示字面量「[object HTMLSpanElement]」（2026-10-30 实测截屏确认，链接从来没露过面）。
function guideSetup(message) {
  if (!message || typeof message !== 'string' || !message.includes(NEED_SETUP)) return false
  const reason = message.replace(NEED_SETUP, '').replace(/^\d{3}:\s*/, '').trim() || '尚未配置可用的 AI 模型'
  import('element-plus').then(({ ElMessage }) => {
    ElMessage({
      type: 'warning',
      duration: 8000,
      message: h('span', null, [
        reason + '，',
        h('a', {
          href: '/settings',
          style: 'color:#409eff;cursor:pointer;text-decoration:underline;font-weight:600',
        }, '前往配置'),
      ]),
    })
  })
  return true
}

// onProgress：分批任务（如长文档摘要的两段式）的进度回调，payload = {done, total}
// onStage：**阶段**进度回调，payload = {stage, detail}（AI 视频的生成流用它）。
//   ⚠️ 与 onProgress 分开，是因为两者载荷语义不同（阶段名+明细 vs 计数）：
//      合用一个回调会逼调用方靠猜字段名区分。
//   ⚠️ 不加这个分支时 `event: stage` 会被 `else` 链**静默丢弃** ——
//      表现为「点了生成、进度条一直不动」的假死（后端其实在跑）。
// signal：可选的 AbortSignal（WP17 侧栏订阅要在卸载时断开长连接）。
//   ⚠️ 长连接（SSE）**必须**能断开：否则每次订阅都在后端留一条不死连接 + 一个订阅者队列。
// opts（可选，第 10 个参数）：
//   onEvent(ev, payload)：**兜底回调**，只对上面没内置处理的事件触发
//     （目前是 sources / reasoning / suggestions / tool / 以及以后新增的任何事件）。
//     ⚠️ 没有它时，调用方实现「回答带引用、带思维链」的唯一办法是自己再写一份
//        fetch + getReader 逐事件解析（炼丹炉原先就是这么干的）→ 同一段解析逻辑存在两份，
//        每加一个事件都要改两处，漏改的那一边是**静默丢事件**（界面少引用、少思维链，
//        而 token 照常流出，看起来「只是没显示」，很难被发现）。
//     ⚠️ 内置事件（token/done/error/meta/progress/stage）**不会**再走 onEvent，
//        否则订阅了 onToken 的调用方会收到两遍。
//   inlineSetupGuide：true = error 命中「未配置模型」时**不弹**全局引导，
//     并把**原始**文案（含 [NEED_SETUP] 信号串）交给 onError，由调用方在气泡里画入口。
//     默认 false（弹全局「前往配置」提示，与原有调用方一致）。
export async function streamSSE(url, body, onToken, onDone, onError, onMeta, onProgress, signal, onStage, opts = {}) {
  const { onEvent, inlineSetupGuide } = opts || {}
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
    // HTTP 层未配置：默认已弹引导，调用方据此跳过自身 error 提示；
    // inlineSetupGuide 模式下由调用方渲染气泡内入口，这里不弹、也不算已引导。
    e.needSetup = inlineSetupGuide ? false : guideSetup(detail)
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
      // ⚠️ error 必须排在兜底 onEvent 之前：否则「未配置」引导会被当成未知事件静默吞掉
      if (ev === 'token') onToken?.(payload.t)
      else if (ev === 'done') onDone?.(payload)
      else if (ev === 'error') {
        // 未配置模型：默认额外弹「前往配置」引导；两种模式下都**必须**回调 onError，
        // 否则调用方 loading 不复位（表现是按钮永远转圈）。
        // payload 一并透传：错误事件也可能带 message_id（半截回答已落库），
        // 只给 message 字符串会让调用方拿不到它 → 重试/重新生成截断不了。
        const msg = payload.message || ''
        if (msg.includes(NEED_SETUP)) {
          if (inlineSetupGuide) {
            onError?.(msg, payload)                       // 原样（含信号串）：调用方自己剥
          } else {
            guideSetup(msg)
            onError?.(msg.replace(NEED_SETUP, ''), payload)
          }
        } else {
          onError?.(msg, payload)
        }
      }
      else if (ev === 'meta') onMeta?.(payload)
      else if (ev === 'progress') onProgress?.(payload)
      else if (ev === 'stage') onStage?.(payload)
      else onEvent?.(ev, payload)   // 兜底：sources / reasoning / suggestions / tool …
    }
  }
}
