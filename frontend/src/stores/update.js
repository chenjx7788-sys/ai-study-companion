import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { updateApi } from '../api'

// 客户端更新的**唯一状态源**：侧栏版本号、顶部提示条、设置页「关于」、更新弹窗
// 全部读这一个 store —— 否则在设置页点了「忽略此版本」，回到主界面侧栏红点还挂着。
//
// ⚠️ 桌面端没有推送通道：新版被用户看到 = 下次启动时这次 autoCheck()。
// ⚠️ 检查失败一律静默（除手动检查）：断网是常态，报错只会变成噪音。
const IGNORE_KEY = 'asc_update_ignored'    // 用户选择忽略的版本号
const TIP_KEY = 'asc_update_tip_count'     // {v: 版本号, n: 已自动提示次数}
const TIP_MAX = 3                          // 同一版本最多自动弹条 3 次，之后只留红点

// 版本号归一化：后端给的是纯版本号，但 localStorage 里可能残留带 v 的旧值
// （早期手写 / 用户手改）→ 不归一化会让「已忽略」判定失效，红点又冒出来。
const bare = (v) => String(v || '').trim().replace(/^[vV]/, '')

export const useUpdateStore = defineStore('update', {
  state: () => ({
    info: null,           // /update/check 的完整返回（current / latest / notes / asset ...）
    tip: false,           // 顶部「新版本已发布」提示条
    dialogOpen: false,    // 更新弹窗
    checkedAt: 0,
    ignoredVersion: '',
    tipCount: 0,
  }),

  getters: {
    // 原始判断：上游确实有新版本（不管用户是否忽略过）
    upstreamHasUpdate: (s) => !!s.info?.has_update,
    // 面向提示的判断：用户忽略掉的这一版**连红点一起不提示**。
    // ⚠️ 只把「提示条」关掉、红点留着，等于点了忽略没效果 —— 红点会一直挂到下次发版。
    //    想「知道但先不装」的用户，从侧栏版本号点进去仍能看到更新（openDialog 走原始判断）。
    hasUpdate: (s) => !!s.info?.has_update && bare(s.info?.latest) !== bare(s.ignoredVersion),
    latest: (s) => s.info?.latest || '',
    current: (s) => s.info?.current || '',
    isDev: (s) => (s.info ? !s.info.frozen : false),
    installDir: (s) => s.info?.install_dir || '',
    downloadDir: (s) => s.info?.download_dir || '',
  },

  actions: {
    /** 从 localStorage 恢复「已忽略的版本」与提示次数（App 启动 / 设置页挂载时各调一次即可） */
    hydrate() {
      try {
        this.ignoredVersion = localStorage.getItem(IGNORE_KEY) || ''
        const raw = JSON.parse(localStorage.getItem(TIP_KEY) || '{}')
        this.tipCount = raw.v && bare(raw.v) === bare(this.latest) ? (raw.n || 0) : 0
      } catch {
        this.ignoredVersion = ''
        this.tipCount = 0
      }
    },

    _apply(data) {
      this.info = data
      this.checkedAt = Date.now()
      try {
        const raw = JSON.parse(localStorage.getItem(TIP_KEY) || '{}')
        this.tipCount = raw.v && bare(raw.v) === bare(data.latest) ? (raw.n || 0) : 0
      } catch {
        this.tipCount = 0
      }
      return data
    },

    /** 启动时静默检查：绝不弹错，绝不打断首屏 */
    async autoCheck() {
      try {
        const { data } = await updateApi.check(false)
        this._apply(data)
        // 开发态（源码运行）不提示：版本号随时在改，提示只会是噪音
        if (!data.frozen || !data.has_update) return
        if (this.ignoredVersion && bare(this.ignoredVersion) === bare(data.latest)) return
        if (this.tipCount >= TIP_MAX) return
        this.tip = true
      } catch { /* 静默 */ }
    },

    /** 手动检查：force=1 跳过 6h 缓存。返回结果，由调用方决定怎么展示 */
    async manualCheck() {
      try {
        const { data } = await updateApi.check(true)
        this._apply(data)
        if (!data.latest) {
          return { ok: false, message: '暂时无法连接更新服务，请检查网络或前往官网下载' }
        }
        return { ok: true, hasUpdate: data.has_update, data }
      } catch {
        return { ok: false, message: '暂时无法连接更新服务，请稍后重试' }
      }
    },

    /** 点侧栏版本号 / 提示条 / 设置页按钮，都从这里进弹窗 */
    async openDialog() {
      this.tip = false
      const r = await this.manualCheck()
      if (!r.ok) {
        ElMessage.warning(r.message)
        return false
      }
      if (!r.hasUpdate) {
        ElMessage.success('当前已是最新版本 v' + this.current)
        return false
      }
      this.dialogOpen = true
      return true
    },

    /** 关掉提示条：计入次数，用尽后只保留侧栏红点 */
    dismissTip() {
      this.tip = false
      try {
        localStorage.setItem(TIP_KEY, JSON.stringify({ v: this.latest, n: this.tipCount + 1 }))
        this.tipCount += 1
      } catch { /* 静默 */ }
    },

    /** 忽略此版本：只在这一版生效，下一版会重新提示 */
    ignore(v) {
      this.ignoredVersion = v || this.latest
      try { localStorage.setItem(IGNORE_KEY, this.ignoredVersion) } catch { /* 静默 */ }
      this.tip = false
      this.dialogOpen = false
    },
  },
})
