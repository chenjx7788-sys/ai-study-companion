// AI 回答正文图片查看器（P1 · 2026-10-23）——全局单例状态。
// 委托入口在 utils/md.js 的 installMdEnhancements()：点击 .md-body/.ep-block 内 img 时
// 收集同容器全部图片打开 ElImageViewer（支持多图左右切换）。
// 组件挂载：App.vue -> components/MdImageViewer.vue
import { reactive } from 'vue'

export const imgViewerState = reactive({ show: false, urls: [], index: 0 })

export function openImgViewer(container, imgEl) {
  const imgs = Array.from(container.querySelectorAll('img')).filter((el) => el.src)
  if (!imgs.length) return
  imgViewerState.urls = imgs.map((el) => el.src)
  imgViewerState.index = Math.max(0, imgs.indexOf(imgEl))
  imgViewerState.show = true
}

export function closeImgViewer() {
  imgViewerState.show = false
}
