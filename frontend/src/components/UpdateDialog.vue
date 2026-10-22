<template>
  <el-dialog
    v-model="visible"
    width="600px"
    top="8vh"
    :close-on-click-modal="false"
    class="upd-dialog"
    @closed="stopPoll"
  >
    <template #header>
      <div class="upd-head">
        <div class="upd-title-row">
          <span class="upd-title">发现新版本</span>
          <span class="upd-ver">
            <span class="v-cur">v{{ info?.current || '—' }}</span>
            <svg class="v-arrow" viewBox="0 0 24 24" width="15" height="15" fill="none"
              stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 12h15M13 6l6 6-6 6" />
            </svg>
            <span class="v-new">v{{ latest || '—' }}</span>
          </span>
        </div>
        <div class="upd-meta">
          <span v-if="info?.published_at">{{ fmtDate(info.published_at) }} 发布</span>
          <span v-if="assetSize">{{ fmtSize(assetSize) }}</span>
          <span v-if="info?.stale" class="meta-warn">离线缓存（当前网络未取到最新）</span>
        </div>
      </div>
    </template>

    <div class="upd-body">
      <!-- 更新说明：markdown 走统一渲染器（外链自动 target=_blank）
           ⚠️ 说明区必须自己限高滚动：整块 body 一起滚时，长日志会把「下载安装包」挤出首屏
              （0.1.6 实测按钮落在 btn_top=1446px，而可视区底边只有 632px） -->
      <div class="upd-notes-wrap" :class="{ 'is-scroll': notesOverflow, 'at-end': notesAtEnd }">
        <div v-if="notesHtml" ref="notesBox" class="upd-notes" v-html="notesHtml" @scroll="syncNotesMask"></div>
        <div v-else ref="notesBox" class="upd-notes empty" @scroll="syncNotesMask">该版本未提供更新说明，可前往 Release 页查看。</div>
      </div>
      <div v-if="notesOverflow" class="notes-more">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
          stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 5v14" /><path d="M19 12l-7 7-7-7" />
        </svg>
        更新说明可在框内滚动查看全部
      </div>

      <!-- 下载 / 校验 / 完成 -->
      <div class="upd-dl">
        <template v-if="dl.status === 'idle' || dl.status === 'error' || dl.status === 'canceled'">
          <el-button type="primary" :loading="starting" @click="startDownload">
            {{ dl.status === 'error' ? '重新下载' : '下载安装包' }}
          </el-button>
          <span class="dl-hint">{{ assetSize ? fmtSize(assetSize) : '大小未知' }}，仅下载、不改动当前版本</span>
        </template>

        <template v-else-if="dl.status === 'downloading' || dl.status === 'verifying' || dl.status === 'canceling'">
          <div class="dl-progress">
            <div class="dl-bar-top">
              <span class="dl-label">{{ dl.status === 'canceling' ? '正在取消…' : (dl.status === 'verifying' ? '校验安装包…' : '正在下载…') }}</span>
              <span class="dl-pct">{{ Math.floor(dl.percent || 0) }}%</span>
            </div>
            <el-progress :percentage="dl.percent || 0" :show-text="false" :stroke-width="6" />
            <div class="dl-bar-bottom">
              <span>{{ fmtSize(dl.downloaded) }} / {{ fmtSize(dl.total) }}</span>
              <span v-if="dl.status === 'downloading' && dl.speed > 0">{{ fmtSize(dl.speed) }}/s</span>
            </div>
          </div>
          <el-button text type="info" size="small" :disabled="dl.status === 'canceling'" @click="cancelDownload">取消</el-button>
        </template>

        <template v-else-if="dl.status === 'done'">
          <div class="dl-done">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
              stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20 6L9 17l-5-5" />
            </svg>
            <span>安装包已下载并校验通过</span>
          </div>
          <el-button size="small" @click="revealFile">打开所在文件夹</el-button>
        </template>

        <div v-if="dl.error" class="dl-err">{{ dl.error }}</div>
      </div>

      <!-- 安装指引：本阶段不自动替换，必须把「怎么装」写清楚 -->
      <div class="upd-howto">
        <div class="howto-title">安装方式（绿色版，无需卸载）</div>
        <ol class="howto-steps">
          <li>完全退出当前应用（关闭窗口即可，托盘无残留）</li>
          <li>把下载的 zip 解压，用其中的文件覆盖安装目录</li>
          <li>重新打开应用，版本号会变成 v{{ latest }}</li>
        </ol>
        <div v-if="info?.install_dir" class="howto-path">
          安装目录：<code>{{ info.install_dir }}</code>
        </div>
        <div class="howto-tip">你的资料、笔记与配置存放在独立的数据目录，覆盖安装不会影响它们。</div>
      </div>
    </div>

    <template #footer>
      <div class="upd-footer">
        <el-button text type="info" size="small" @click="ignoreVersion">忽略此版本</el-button>
        <div class="footer-right">
          <el-button text type="primary" @click="openSite">浏览器打开下载页</el-button>
          <el-button @click="visible = false">稍后</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { createMd } from '../utils/md'
import { updateApi } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  info: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'ignore'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const latest = computed(() => props.info?.latest || '')
const assetSize = computed(() => props.info?.asset?.size || 0)
const notesHtml = computed(() => {
  const src = props.info?.notes || ''
  if (!src) return ''
  // 每个组件实例独立 md：link_open 的 target 改写与图片代理都由工厂统一保住
  return createMd({ html: false, breaks: true }).render(src)
})

const notesBox = ref(null)
const notesOverflow = ref(false)
const notesAtEnd = ref(false)

// 说明区限高后，必须自己判断「还有没有内容没看到」：
// 有溢出才显示底部渐隐与提示；滚到底要撤掉渐隐，否则最后一行被遮住半截。
function syncNotesMask() {
  const el = notesBox.value
  if (!el) return
  notesOverflow.value = el.scrollHeight > el.clientHeight + 4
  notesAtEnd.value = el.scrollTop + el.clientHeight >= el.scrollHeight - 4
}

// el-dialog 首帧可能还没量出尺寸（clientHeight=0）→ 重试两帧，否则溢出判断会是 false
function measureNotes(tries = 2) {
  const el = notesBox.value
  if (!el) return
  if (!el.clientHeight && tries > 0) {
    requestAnimationFrame(() => measureNotes(tries - 1))
    return
  }
  el.scrollTop = 0
  syncNotesMask()
}

const dl = ref({ status: 'idle', downloaded: 0, total: 0, percent: 0, speed: 0, error: '', file: '' })
const starting = ref(false)
let timer = null

// 说明内容变了（换版本 / 缓存刷新）→ 溢出状态要重算
watch(notesHtml, () => nextTick(() => measureNotes()))

// 打开弹窗时同步一次真实进度：用户可能上次关掉弹窗后仍在后台下载
watch(() => props.modelValue, async (open) => {
  if (!open) {
    stopPoll()
    return
  }
  nextTick(() => measureNotes())
  if (props.info?.downloaded_file) {
    dl.value = { status: 'done', downloaded: assetSize.value, total: assetSize.value, percent: 100, speed: 0, error: '', file: props.info.downloaded_file }
  }
  try {
    const { data } = await updateApi.progress()
    if (data && data.status !== 'idle') {
      dl.value = { ...dl.value, ...data }
      if (data.status === 'downloading' || data.status === 'verifying') startPoll()
    }
  } catch { /* 静默：拿不到进度就按未开始处理 */ }
})

function startPoll() {
  stopPoll()
  timer = setInterval(async () => {
    try {
      const { data } = await updateApi.progress()
      const next = { ...dl.value, ...data }
      // 服务端还没真正停下时会继续回 downloading，别把「正在取消…」冲掉
      if (dl.value.status === 'canceling' && data.status === 'downloading') next.status = 'canceling'
      dl.value = next
      if (data.status === 'done' || data.status === 'error' || data.status === 'canceled') stopPoll()
    } catch { /* 轮询失败不打断，下一次继续 */ }
  }, 800)
}

function stopPoll() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function startDownload() {
  starting.value = true
  try {
    const { data } = await updateApi.download(latest.value)
    if (data.status === 'done') {
      dl.value = { status: 'done', downloaded: assetSize.value, total: assetSize.value, percent: 100, speed: 0, error: '', file: data.file }
    } else {
      dl.value = { status: 'downloading', downloaded: 0, total: assetSize.value, percent: 0, speed: 0, error: '', file: '' }
      startPoll()
    }
  } catch (e) {
    dl.value = { ...dl.value, status: 'error', error: e?.response?.data?.detail || '无法开始下载，请稍后重试' }
  } finally {
    starting.value = false
  }
}

async function cancelDownload() {
  // 立刻反馈：卡死的链路上真正停下可能要等读超时，不能让按钮看起来没反应
  if (dl.value.status === 'downloading' || dl.value.status === 'verifying') dl.value.status = 'canceling'
  try { await updateApi.cancel() } catch { /* 取消失败也无所谓，下一轮轮询会看到真实状态 */ }
}

async function revealFile() {
  try { await updateApi.reveal(dl.value.file || props.info?.downloaded_file || '') } catch { /* 静默 */ }
}

function ignoreVersion() {
  emit('ignore', latest.value)
  visible.value = false
}

function openSite() {
  const url = props.info?.page_url
  if (url) window.open(url, '_blank', 'noopener')
}

function fmtSize(n) {
  const b = Number(n) || 0
  if (!b) return ''
  if (b >= 1024 * 1024 * 1024) return (b / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  if (b >= 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB'
  return Math.max(1, Math.round(b / 1024)) + ' KB'
}

function fmtDate(s) {
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

onUnmounted(stopPoll)
</script>

<style scoped>
.upd-head { padding-right: 8px; }
.upd-title-row { display: flex; align-items: center; gap: 12px; }
.upd-title { font-size: 16px; font-weight: 600; color: var(--asc-text, #1f1f22); }
.upd-ver { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; }
.v-cur { color: var(--asc-text-2, #6e6e6e); font-weight: 500; }
.v-arrow { color: var(--asc-text-2, #6e6e6e); }
.v-new { color: var(--asc-primary, #7c5cfc); }
.upd-meta { margin-top: 6px; display: flex; gap: 14px; font-size: 12px; color: #6e6e6e; }
.meta-warn { color: #b8791a; }

/* ⚠️ 不要让 body 整体滚动：长更新日志会把「下载安装包」顶出首屏，
   而它和安装指引是用户在这个弹窗里唯一要做的事。滚动下沉到说明区内部。 */
.upd-body { padding-right: 4px; }

.upd-notes-wrap { position: relative; margin-bottom: 14px; }
.upd-notes {
  font-size: 13px; line-height: 1.75; color: #333;
  background: var(--asc-surface-2, #f7f7f8); border-radius: 10px;
  padding: 12px 14px;
  /* 196px 是实测「能看出是更新日志、又不挤掉下载区」的高度；矮窗口按 30vh 收 */
  max-height: min(196px, 30vh); overflow-y: auto;
}
.upd-notes::-webkit-scrollbar { width: 6px; }
.upd-notes::-webkit-scrollbar-thumb { background: #d5d5dc; border-radius: 3px; }
.upd-notes::-webkit-scrollbar-thumb:hover { background: #c0c0c9; }

/* 硬切会切掉半行字，看起来像内容坏了 → 底部渐隐；滚到底时撤掉，免得遮住最后一行。
   ⚠️ 这里不加 transition：伪元素从「不匹配」变「匹配」时过渡起始值不推进，
      computed opacity 会一直停在 0，遮罩根本不渲染。 */
.upd-notes-wrap.is-scroll::after {
  content: ''; position: absolute; left: 0; right: 6px; bottom: 0; height: 15px;
  background: linear-gradient(to bottom, rgba(247, 247, 248, 0), #f7f7f8 88%);
  border-radius: 0 0 10px 10px; pointer-events: none;
  opacity: 1;
}
/* 滚到底只是「透明」，不写成 :not(.at-end) 去取消 content —— 伪元素不生成时
   getComputedStyle(::after).opacity 会读回初始值 1，看着像没生效。 */
.upd-notes-wrap.is-scroll.at-end::after { opacity: 0; }
.notes-more {
  display: inline-flex; align-items: center; gap: 5px;
  margin: -8px 0 12px; font-size: 11.5px; color: #6e6e6e;
}
.upd-notes.empty { color: #6e6e6e; }
.upd-notes :deep(h1),
.upd-notes :deep(h2),
.upd-notes :deep(h3),
.upd-notes :deep(h4) { font-size: 13px; margin: 12px 0 6px; color: #1f1f22; }
.upd-notes :deep(h3:first-child),
.upd-notes :deep(h4:first-child) { margin-top: 0; }
.upd-notes :deep(ul),
.upd-notes :deep(ol) { margin: 6px 0; padding-left: 20px; }
.upd-notes :deep(li) { margin: 3px 0; }
.upd-notes :deep(p) { margin: 6px 0; }
.upd-notes :deep(code) {
  background: #ececf0; padding: 1px 5px; border-radius: 4px; font-size: 12px;
}
.upd-notes :deep(a) { color: var(--asc-primary, #7c5cfc); text-decoration: underline; }
.upd-notes :deep(img) { max-width: 100%; height: auto; }

.upd-dl { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 6px; }
.dl-hint { font-size: 12px; color: #6e6e6e; }
.dl-progress { flex: 1; min-width: 320px; }
.dl-bar-top { display: flex; justify-content: space-between; font-size: 12px; color: #333; margin-bottom: 4px; }
.dl-pct { color: var(--asc-primary, #7c5cfc); font-weight: 600; }
.dl-bar-bottom { display: flex; justify-content: space-between; font-size: 11px; color: #6e6e6e; margin-top: 4px; }
.dl-done { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: #1f8a4c; font-weight: 500; }
.dl-err { width: 100%; font-size: 12px; color: #c0392b; margin-top: 2px; }

.upd-howto {
  margin-top: 14px; padding: 12px 14px; border-radius: 10px;
  background: #fffbf2; border: 1px solid #f3e3c3;
}
.howto-title { font-size: 13px; font-weight: 600; color: #8a5a00; margin-bottom: 6px; }
.howto-steps { margin: 0 0 6px; padding-left: 20px; font-size: 12.5px; line-height: 1.8; color: #4a4a4a; }
.howto-path { font-size: 12px; color: #4a4a4a; word-break: break-all; }
.howto-path code {
  background: #f3e9d6; padding: 1px 6px; border-radius: 4px; font-size: 11.5px; color: #6b4a00;
}
.howto-tip { font-size: 12px; color: #6e6e6e; margin-top: 6px; }

.upd-footer { display: flex; align-items: center; justify-content: space-between; }
.footer-right { display: flex; align-items: center; gap: 8px; }
</style>
