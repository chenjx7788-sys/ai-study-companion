// AI 回答正文图片 / mermaid 图放大查看（P1 · 2026-10-23；2026-10-26 扩展到 mermaid 图）。
// 委托入口在 utils/md.js 的 installMdEnhancements()：点击 .md-body/.ep-block/.md-preview 内的
// <img> 或 mermaid 图，收集同容器全部可放大对象交给 ElImageViewer（左右切换 / 滚轮缩放 / 拖拽）。
// 组件挂载：App.vue -> components/MdImageViewer.vue
import { reactive } from 'vue'

export const imgViewerState = reactive({ show: false, urls: [], index: 0 })

// mermaid 图交给查看器的是运行时序列化出来的 blob URL，关闭后必须 revoke，
// 否则每点一次就在内存里留一份整图（反复点几十次会明显吃内存）。
let revokable = []

function revokeAll(urls) {
  const arr = urls || []
  arr.forEach((u) => {
    try {
      URL.revokeObjectURL(u)
    } catch (e) {
      // 非 blob URL（正文图片是真实地址）→ 无需释放
    }
  })
}

// items: [{ url, revoke }]，index: 打开时定位到第几张
export function openZoomViewer(items, index) {
  const list = (items || []).filter((it) => it && it.url)
  if (!list.length) return
  revokeAll(revokable)
  revokable = list.filter((it) => it.revoke).map((it) => it.url)
  imgViewerState.urls = list.map((it) => it.url)
  imgViewerState.index = Math.min(Math.max(0, index | 0), list.length - 1)
  imgViewerState.show = true
}

export function closeImgViewer() {
  imgViewerState.show = false
  // ElImageViewer 关闭带淡出动画，立即 revoke 会让动画里的图变空白 → 延后释放
  const urls = revokable
  revokable = []
  if (urls.length) setTimeout(() => revokeAll(urls), 600)
}
