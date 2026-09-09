import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 120000 })

// 错误信息归一化：后端 detail 可能是字符串 / 422 数组 / 对象 / 空，统一转成可读文本
export function errMsg(e, fallback = '操作失败，请重试') {
  const d = e?.response?.data?.detail
  if (typeof d === 'string' && d) return d
  if (Array.isArray(d) && d.length) return d[0]?.msg || fallback        // FastAPI 422
  if (d && typeof d === 'object' && Object.keys(d).length) return JSON.stringify(d)
  if (e?.code === 'ECONNABORTED') return '请求超时（超过 2 分钟），请重试'
  if (!e?.response) return '服务未响应，请确认后端已启动后重试'
  return fallback
}

export default http
