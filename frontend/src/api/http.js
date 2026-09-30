import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({ baseURL: '/api', timeout: 120000 })

// 「未配置模型」类错误的稳定信号：后端 get_client 在缺 API Key 时以此前缀标识，
// 前端据此弹「前往配置」引导，而不是冷冰冰的报错。见 app/services/llm.py::get_client。
export const NEED_SETUP = '[NEED_SETUP]'

// 命中「未配置模型」→ 弹带跳转的引导提示；返回 true 表示已被接管（调用方应跳过自身 error 提示）
export function guideIfNeedsSetup(e) {
  const d = e?.response?.data?.detail
  const raw = typeof d === 'string' ? d : (d?.msg || '')
  return guideIfNeedsSetupMsg(raw)
}

// 字符串版：接受已取出的错误文案（供自定义 SSE 路径等直接持有 message 的场景复用）
// 命中「未配置模型」时，弹一条由「中文原因 + 前往配置链接」组成的提示（纯中文，不暴露内部信号前缀）
export function guideIfNeedsSetupMsg(msg) {
  if (!msg || typeof msg !== 'string' || !msg.includes(NEED_SETUP)) return false
  const reason = msg.replace(NEED_SETUP, '').replace(/^\d{3}:\s*/, '').trim() || '尚未配置可用的 AI 模型'
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
  return true
}

// 响应拦截器：统一拦截「未配置模型」错误（覆盖各视图经 axios 的 AI 调用，无需改各视图）
http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    // 命中未配置：弹「前往配置」引导；错误仍继续抛给调用方（errMsg 已剥除信号前缀，仅显示可读说明）
    guideIfNeedsSetup(error)
    return Promise.reject(error)
  }
)

// 错误信息归一化：后端 detail 可能是字符串 / 422 数组 / 对象 / 空，统一转成可读文本
export function errMsg(e, fallback = '操作失败，请重试') {
  const d = e?.response?.data?.detail
  if (typeof d === 'string' && d) return d.replace(NEED_SETUP, '')
  if (Array.isArray(d) && d.length) return d[0]?.msg || fallback        // FastAPI 422
  if (d && typeof d === 'object' && Object.keys(d).length) return JSON.stringify(d)
  if (e?.code === 'ECONNABORTED') return '请求超时（超过 2 分钟），请重试'
  if (!e?.response) return '服务未响应，请确认后端已启动后重试'
  return fallback
}

export default http
