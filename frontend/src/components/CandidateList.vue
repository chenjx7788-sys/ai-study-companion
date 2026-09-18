<template>
  <div class="cand-wrap">
    <div class="cand-head">
      <div class="cand-head-l">
        <span class="cand-title">待确认内容</span>
        <span class="cand-count">{{ settledCount }} / {{ items.length }}</span>
      </div>
      <span class="cand-note">逐篇确认才会入库；本步骤不写磁盘、不建索引</span>
    </div>

    <div class="cand-scroll">
      <div v-for="(it, i) in items" :key="i" class="cand-item" :class="'act-' + it.action">
        <!-- 状态图标 -->
        <div class="cand-ic">
          <el-icon v-if="it.action === 'ready'" class="ic-ok"><CircleCheck /></el-icon>
          <el-icon v-else-if="it.action === 'paste'" class="ic-warn"><WarningFilled /></el-icon>
          <el-icon v-else class="ic-bad"><CircleClose /></el-icon>
        </div>

        <div class="cand-main">
          <div class="cand-t">
            <span class="cand-name" :title="it.title || it.url || it.input_url">
              {{ it.title || (it.action === 'blocked' ? '无法读取这个链接' : '（无标题）') }}
            </span>
            <span v-if="it.saved" class="badge-saved">已在知识库</span>
          </div>

          <div class="cand-meta">
            <span class="kind-chip" :class="'k-' + (it.kind || 'url')">{{ it.kind_label || kindFallback(it.kind) }}</span>
            <span v-if="hostOf(it)" class="meta-host">{{ hostOf(it) }}</span>
            <span v-if="it.chars">{{ it.chars }} 字</span>
            <!-- 图片张数：正文以图片为主时（截图/表格/流程图型文章）字数少是正常的，
                 不是抓取失败 —— 见 backend/services/external.py 的 include_images 说明 -->
            <span v-if="it.images" class="meta-img">{{ it.images }} 张图</span>
            <!-- 与阅读页同口径：正文少是因为「本来就是图 / 本来就是视频」，不是抓漏 -->
            <span v-if="it.wx_image_post" class="meta-warn">图片消息</span>
            <span v-if="it.xhs_video_note" class="meta-warn">视频笔记 · 仅抓配文与封面</span>
            <span v-if="it.wx_temp_link" class="meta-warn">临时链接约 6 小时过期</span>
          </div>

          <!-- 抓取失败 → 原地切粘贴通道（不是报错终点，见方案 §6.3） -->
          <div v-if="it.hint && it.action !== 'ready'" class="cand-hint">{{ it.hint }}</div>

          <div v-if="it.action === 'paste'" class="paste-box">
            <el-input v-model="it._paste" type="textarea" :rows="4" resize="vertical"
              placeholder="打开该网页，选中正文复制，粘贴到这里（流程与自动抓取完全一致）" />
            <div class="paste-ops">
              <el-button size="small" type="primary" plain :loading="it._pasting"
                :disabled="!(it._paste || '').trim()" @click="usePaste(it, i)">
                用这段正文继续
              </el-button>
              <span class="paste-tip">仅在你有权保存该内容时使用</span>
            </div>
          </div>

          <div class="cand-ops">
            <template v-if="it.saved">
              <el-button size="small" @click="$emit('open-material', it)">打开已有</el-button>
              <el-button size="small" type="primary" plain @click="readOnly(it)">继续阅读</el-button>
              <el-button size="small" text @click="drop(i)">移除</el-button>
            </template>
            <template v-else-if="it.action === 'blocked'">
              <el-button size="small" text @click="drop(i)">跳过</el-button>
            </template>
            <template v-else>
              <el-button size="small" :loading="it._reading" @click="readOnly(it)">仅阅读</el-button>
              <el-button size="small" type="primary" :loading="it._saving"
                :disabled="it.action === 'paste' && !(it._paste || '').trim()" @click="saveOne(it)">
                加入知识库
              </el-button>
              <el-button size="small" text @click="drop(i)">跳过</el-button>
            </template>
          </div>
        </div>
      </div>

      <el-empty v-if="!items.length" description="没有待确认的内容" :image-size="64" />
    </div>

    <div class="cand-foot">
      <span class="cand-foot-count">
        可入库 {{ counts.ready }} · 需粘贴 {{ counts.paste }} · 不可用 {{ counts.blocked }}
      </span>
      <div class="cand-foot-ops">
        <!-- ⚠️ 合规边界：这里**只允许**"全部跳过"，绝不提供"全部入库"（方案 §6.2） -->
        <el-button v-if="remaining.length" text @click="skipAll">全部跳过</el-button>
        <el-button type="primary" @click="$emit('done')">完成</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { CircleCheck, WarningFilled, CircleClose } from '@element-plus/icons-vue'
import { clipApi, ephemeralApi } from '../api'
import { useReadingStore } from '../stores/reading'

const props = defineProps({
  items: { type: Array, required: true },
  subject: { type: String, default: '' }   // 用于提示文案（可选）
})
const emit = defineEmits(['saved', 'done', 'open-material'])
const reading = useReadingStore()

// 组件内可变标记：_paste / _saving / _reading / _pasting 直接挂在条目上（父组件持有的同一对象）
const items = reactive(props.items)

const remaining = computed(() => items.filter(i => !i.saved && i.action !== 'blocked'))
const settledCount = computed(() => items.filter(i => i.saved).length)
const counts = computed(() => ({
  ready: items.filter(i => i.action === 'ready' && !i.saved).length,
  paste: items.filter(i => i.action === 'paste' && !i.saved).length,
  blocked: items.filter(i => i.action === 'blocked').length
}))

function kindFallback(kind) {
  return { url: '网页剪藏', wx: '公众号文章', xhs: '小红书笔记', weread: '微信读书', local: '本地导入' }[kind] || '网页剪藏'
}

function hostOf(it) {
  const u = it.url || it.input_url || ''
  try { return new URL(u).host } catch { return '' }
}

function drop(i) {
  items.splice(i, 1)
}

function skipAll() {
  for (let i = items.length - 1; i >= 0; i--) {
    if (!items[i].saved && items[i].action !== 'blocked') items.splice(i, 1)
  }
}

/** 把当前 URL 重新以「粘贴正文」通道预览一次（后端 preview 支持 text → 不抓取）。 */
async function usePaste(it, i) {
  it._pasting = true
  try {
    // ⚠️ 传 `input_url`（用户原始输入）而**不是** `it.url`：后者是归一化地址，
    //    小红书会被故意剥掉 xsec_token → 拿它去抓必然是「你访问的页面不见了」。
    //    身份用归一化、抓取用原始输入，两边必须成对（后端同理）。
    const { data } = await clipApi.preview({
      url: it.input_url || it.url,
      text: it._paste,
      title: it.title || ''
    })
    // 就地替换该条：正文已就位 → action 变 ready，可入库
    Object.assign(it, data, { _paste: it._paste, _pasting: false })
    if (data.action !== 'ready') {
      ElMessage.warning(data.hint || '这段正文太短，请确认是否复制完整')
    }
  } catch (e) {
    it._pasting = false
    ElMessage.error(errText(e))
  }
}

/** 逐条入库（方案 §6.2 的关键约束：只有逐条，没有"全部入库"） */
async function saveOne(it) {
  it._saving = true
  try {
    // ⚠️ 传 `input_url`（用户原始输入）而**不是** `it.url`：后者是归一化地址，
    //    小红书会被故意剥掉 xsec_token → 拿它去抓必然是「你访问的页面不见了」。
    //    身份用归一化、抓取用原始输入，两边必须成对（后端同理）。
    const { data } = await clipApi.save({
      url: it.input_url || it.url,
      text: it._paste || undefined,
      title: it.title || undefined
    })
    it.saved = true
    it.material_id = data.id
    it.duplicated = !!data.duplicated
    ElMessage.success(data.duplicated ? '这篇已在知识库中，未重复入库' : '已加入知识库，正在建立索引')
    emit('saved', { item: it, material: data })
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    it._saving = false
  }
}

/** 仅阅读：先 open 进内存快照，再把整包交给阅读页（正文不进 URL、不落盘）。 */
async function readOnly(it) {
  it._reading = true
  try {
    // ⚠️ 传 `input_url`（用户原始输入）而**不是** `it.url`：后者是归一化地址，
    //    小红书会被故意剥掉 xsec_token → 拿它去抓必然是「你访问的页面不见了」。
    //    身份用归一化、抓取用原始输入，两边必须成对（后端同理）。
    const { data } = await ephemeralApi.open({
      url: it.input_url || it.url,
      text: it._paste || undefined,
      title: it.title || undefined
    })
    if (!data.key && !data.text) {
      ElMessage.warning(data.hint || '这篇暂时读不了，可以试试粘贴正文')
      return
    }
    reading.setHandoff(data)
    emit('done')
  } catch (e) {
    ElMessage.error(errText(e))
  } finally {
    it._reading = false
  }
}

function errText(e) {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.hint || d.reason || '操作失败'
  return e?.message || '操作失败'
}
</script>

<style scoped>
.cand-wrap { display: flex; flex-direction: column; max-height: 66vh; }
.cand-head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  padding: 0 2px 10px;
}
.cand-head-l { display: flex; align-items: baseline; gap: 8px; }
.cand-title { font-size: 14px; font-weight: 600; }
.cand-count { font-size: 12px; color: var(--asc-text-2); }
.cand-note { font-size: 12px; color: var(--asc-text-2); margin-left: auto; }

.cand-scroll { overflow-y: auto; flex: 1; padding-right: 2px; }

.cand-item {
  display: flex; gap: 10px; padding: 12px; margin-bottom: 8px;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 10px;
}
.cand-item.act-paste { border-color: #e8c98a; background: #fffdf7; }
.cand-item.act-blocked { border-color: var(--asc-border); background: var(--asc-surface-2); }
.cand-ic { flex-shrink: 0; padding-top: 1px; }
.ic-ok { color: #1f9d63; } .ic-warn { color: #b8860b; } .ic-bad { color: #9a9aa2; }

.cand-main { flex: 1; min-width: 0; }
.cand-t { display: flex; align-items: center; gap: 8px; }
.cand-name {
  font-size: 13.5px; font-weight: 600; line-height: 1.5;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.badge-saved {
  flex-shrink: 0; font-size: 11px; font-weight: 500; color: #15803d;
  background: #e8f6ed; border-radius: 4px; padding: 1px 6px;
}
.cand-meta {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 12px; color: var(--asc-text-2); margin-top: 3px;
}
.kind-chip {
  font-size: 11px; font-weight: 600; border-radius: 4px; padding: 1px 6px;
}
.k-url { color: #2563eb; background: #e8f0fd; }
.k-wx { color: #15803d; background: #e8f6ed; }
.k-weread { color: #15803d; background: #e8f6ed; }
.k-local { color: #6e6e6e; background: var(--asc-surface-2); }
.meta-host { color: var(--asc-text-2); }
.meta-warn { color: #8a5a00; }
.meta-img { color: #6b5bd2; }

.cand-hint {
  font-size: 12px; line-height: 1.65; color: #8a5a00;
  background: #fdf6e3; border-radius: 6px; padding: 6px 9px; margin-top: 7px;
}

.paste-box { margin-top: 9px; }
.paste-ops { display: flex; align-items: center; gap: 10px; margin-top: 7px; }
.paste-tip { font-size: 11px; color: var(--asc-text-2); }

.cand-ops { display: flex; align-items: center; gap: 4px; margin-top: 9px; }

.cand-foot {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; padding-top: 10px; margin-top: 2px;
  border-top: 1px solid var(--asc-divider);
}
.cand-foot-count { font-size: 12px; color: var(--asc-text-2); }
.cand-foot-ops { display: flex; align-items: center; gap: 6px; }
</style>
