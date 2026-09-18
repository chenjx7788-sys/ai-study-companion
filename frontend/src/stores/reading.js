import { defineStore } from 'pinia'

// 临时阅读的交接与「最近阅读」缓存（P0-6）
//
// ⚠️ 为什么不把正文塞进 URL：正文可能几万字，query 会超长且会被写进浏览器历史。
//    「仅阅读」的统一姿势是：先调 /ai/ephemeral/open（拿到 key + 正文 + chunks），
//    再把整包数据放到本 store，然后 router.push('/read')。零二次请求、零落盘。
export const useReadingStore = defineStore('reading', {
  state: () => ({
    // 一次性交接包：{ key, url, title, text, chunks, kind, kind_label, saved, material_id, ... }
    handoff: null,
    // 「最近阅读」列表（内存快照的投影，应用重启即空）
    recent: [],
    recentLoading: false,
    // N11：剪藏「候选列表」的寄存处（接线见 LibraryView 的 stashClip 说明）。
    // ⚠️ 存的是**同一个数组引用**、不是副本 —— 条目上的 `_paste` 草稿 / `saved` /
    //    `material_id` 必须一并保住。纯内存，应用重启即空（与 recent 同语义）。
    clipDraft: null
  }),
  actions: {
    setHandoff(payload) { this.handoff = payload },
    takeHandoff() {
      const p = this.handoff
      this.handoff = null
      return p
    },
    clearHandoff() { this.handoff = null },
    setClipDraft(draft) { this.clipDraft = draft },
    clearClipDraft() { this.clipDraft = null }
  }
})
