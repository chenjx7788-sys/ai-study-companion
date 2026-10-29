<template>
  <div class="page fe-page">
    <!-- 首屏：骨架 / 失败态。⚠️ 原来 v-loading 被包在 v-if="app" 内部 → app 为 null 时整块不渲染，
         loading 变量完全无效，进入页面是一片空白，失败时也只有一闪而过的 toast。 -->
    <div v-if="!app" class="fe-boot">
      <div v-if="loading" class="fe-skel">
        <div class="sk-bar w50"></div>
        <div class="sk-bar w70 thin"></div>
        <div class="sk-card"></div>
        <div class="sk-card tall"></div>
        <p class="sk-tx">正在加载应用配置…</p>
      </div>
      <div v-else class="fe-fail">
        <div class="fail-t">应用没能加载出来</div>
        <p class="fail-s">{{ loadError || '可能是后端未启动，或这个应用已被删除。' }}</p>
        <div class="fail-ops">
          <el-button @click="$router.push('/furnace')">返回炼丹炉</el-button>
          <el-button type="primary" @click="load">重试</el-button>
        </div>
      </div>
    </div>

    <template v-else>
      <!-- 页头吸顶：保存入口跟着视口走（原来只在人格卡末尾，滚到资料区就找不到保存） -->
      <div class="fhead">
        <div class="fh-main">
          <h2>
            应用配置 · {{ app.name }}
            <span v-if="app.status === 'draft'" class="tag-draft">待补料</span>
            <span v-else class="tag-ok">运行中</span>
          </h2>
          <p class="fsub">资料绑定与人格口径都可按需调整：改动点「保存」后从下一条提问起生效，不回溯历史回答。</p>
        </div>
        <div class="fh-ops">
          <template v-if="dirty">
            <span class="dirty-pill" :title="dirtyLabel">{{ dirtyCount }} 项未保存</span>
            <el-button text @click="discard">放弃</el-button>
          </template>
          <el-button :type="dirty ? 'primary' : 'default'" :loading="saving" @click="save">保存</el-button>
          <el-button @click="goBack">← 返回对话</el-button>
        </div>
      </div>

      <div class="fe-grid">
        <div class="fe-left">
          <!-- 资料绑定：放在人格前面 —— 它是「这个应用能答什么」的根，也是最高频的调整 -->
          <div class="card">
            <div class="ctitle">
              资料绑定
              <span v-if="materialsDirty" class="unsaved-tag">未保存</span>
              <span class="fh">这个应用只能从这些资料里找答案，可增可减</span>
            </div>
            <div class="mb-row">
              <span v-for="m in boundMaterials" :key="m.id" class="mat-chip mb">
                {{ m.title }}
                <button class="mb-x" :disabled="boundMaterials.length <= 1"
                  :title="boundMaterials.length <= 1 ? '最后一份资料不可移除' : '移除'"
                  @click="unbind(m.id)">×</button>
              </span>
              <span v-if="!boundMaterials.length" class="fh">（异常：未绑定资料）</span>
            </div>
            <div class="fh mb-lab">从材料库追加：</div>
            <el-input v-if="addableMaterials.length > 6" v-model="matQuery" size="small" clearable
              class="mb-search" placeholder="搜索资料标题…" />
            <div class="mb-row">
              <span v-for="m in visibleAddable" :key="m.id" class="mat-chip add" @click="bind(m.id)">＋ {{ m.title }}</span>
              <span v-if="!matQuery && filteredAddable.length > POOL_SHOW" class="mat-chip add pool-toggle" @click="poolOpen = !poolOpen">
                {{ poolOpen ? '收起 ▴' : `展开其余 ${filteredAddable.length - POOL_SHOW} 份 ▾` }}</span>
              <span v-if="!filteredAddable.length" class="fh">
                {{ matQuery ? '没有匹配的资料' : '材料库暂无更多可绑资料' }}
              </span>
            </div>
            <div class="fnote">保存即生效、零索引重建（绑定只是查询过滤条件）；<b>最后一份资料不可移除</b>。</div>
          </div>

          <!-- 人格 · 形象 · 口径 -->
          <div class="card">
            <div class="ctitle">人格 · 形象 · 口径</div>
            <div class="frow">
              <label>形象 <span class="fh">选气质（头顶装饰）；配色独版，可换一身</span></label>
              <div class="spirit-edit">
                <div class="spirit-true">
                  <span class="spirit-true-ico spirit-breathe" v-html="spiritSVG(form.spirit, 56, 'normal')"></span>
                  <div class="spirit-true-ops">
                    <button class="mini-btn" @click="reroll">换一身配色</button>
                    <span class="fh">改完点保存才生效</span>
                  </div>
                </div>
                <div class="spirit-opts">
                  <div v-for="sp in SPIRIT_OPTIONS" :key="sp.id" class="spirit-opt" :class="{ on: curDeco === sp.id }" @click="pickDeco(sp.id)">
                    <span v-html="decoPreviewSVG(sp.id, 44)"></span>{{ sp.label }}
                  </div>
                </div>
              </div>
            </div>
            <div class="frow">
              <label>应用名称 <i>*</i></label>
              <el-input v-model="form.name" maxlength="64" />
            </div>
            <div class="frow">
              <label>人格设定 <i>*</i><button class="mini-btn" :disabled="polishing" @click="polish('persona')">AI 优化</button></label>
              <el-input v-model="form.persona" type="textarea" :rows="5" maxlength="4000" show-word-limit />
            </div>
            <div class="frow">
              <label>回答边界<button class="mini-btn" :disabled="polishing" @click="polish('bounds')">AI 优化</button></label>
              <el-input v-model="form.boundaries" type="textarea" :rows="3" maxlength="2000" show-word-limit />
            </div>
            <div class="frow">
              <label>未命中口径 <span class="fh">超出范围时的拒答话术</span></label>
              <el-input v-model="form.miss_text" maxlength="500" />
            </div>
            <div class="frow">
              <label>开场白</label>
              <el-input v-model="form.welcome" type="textarea" :rows="2" maxlength="500" />
            </div>
            <div class="frow">
              <label>建议问题</label>
              <el-input v-for="i in 3" :key="i" v-model="form.questions[i - 1]" class="fq" maxlength="80" :placeholder="`问题 ${i}`" />
            </div>
            <div class="frow cfg-row">
              <span>追问建议 <span class="fh">回答后自动给出 3 个可追问问题</span></span>
              <el-switch v-model="form.suggest_on" />
            </div>
            <div class="frow cfg-row" v-if="models.length">
              <span>问答模型 <span class="fh">该应用的默认模型</span></span>
              <el-select v-model="form.model_id" style="width: 240px">
                <el-option label="跟随全局默认" value="" />
                <el-option v-for="m in models" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
              </el-select>
            </div>
            <div class="save-bar">
              <span v-if="dirty" class="fh save-hint">{{ dirtyLabel }}</span>
              <el-button type="primary" :loading="saving" @click="save">保存</el-button>
            </div>
          </div>

          <!-- 人格版本：每次实质修改自动留档，可回滚 -->
          <div class="card" v-if="versions.length">
            <div class="ctitle">人格版本 <span class="fh">每次实质修改自动留档；回滚前先给当前态留档，绝不丢配置</span></div>
            <div class="ver-list">
              <div v-for="v in versions" :key="v.id" class="ver-item">
                <span class="ver-tag">v{{ v.version }}</span>
                <div class="ver-body">
                  <div class="ver-head">{{ v.note || '修改前快照' }} <span class="fh">{{ (v.created_at || '').slice(5, 16).replace('T', ' ') }}</span></div>
                  <div class="ver-persona">{{ v.persona_head || '（无人格）' }}…</div>
                </div>
                <el-button size="small" :loading="restoringId === v.id" @click="restoreVer(v)">回滚到此版</el-button>
              </div>
            </div>
          </div>

          <!-- 数据看板：配置任务里优先级最低，下沉到最末 -->
          <div class="card" v-if="stats">
            <div class="ctitle">数据看板 <span class="fh">只统计应用内会话，与主问答互不混</span></div>
            <div class="stats-row">
              <div class="stat"><div class="stat-n">{{ stats.session_count }}</div><div class="stat-l">已聊场数</div></div>
              <div class="stat"><div class="stat-n">{{ stats.question_count }}</div><div class="stat-l">总提问</div></div>
              <div class="stat"><div class="stat-n">{{ stats.hit_rate === null ? '—' : Math.round(stats.hit_rate * 100) + '%' }}</div><div class="stat-l">资料命中率</div></div>
              <div class="stat"><div class="stat-n stat-sm">{{ stats.last_active ? stats.last_active.slice(5, 16).replace('T', ' ') : '—' }}</div><div class="stat-l">最近活跃</div></div>
            </div>
            <div class="trend" v-if="stats.daily && stats.daily.length">
              <div v-for="d in stats.daily" :key="d.date" class="trend-col" :title="`${d.date}：${d.count} 问`">
                <div class="trend-bar" :style="{ height: trendH(d.count) }"></div>
                <div class="trend-d">{{ d.date.slice(3) }}</div>
              </div>
              <div class="trend-cap fh">近 7 天提问</div>
            </div>
          </div>
        </div>

        <!-- 试聊预览：吃当前表单值，未保存也能试 -->
        <div class="card pv-card">
          <div class="ctitle">
            试聊预览
            <span class="fh">吃当前表单值，未保存也能试</span>
          </div>
          <div class="pv-sub">
            <span class="fh">带最近 {{ pvTurns }} 轮上下文，和正式对话一致</span>
            <button class="mini-btn pv-clear" :disabled="pvBusy || !pvHistory.length" @click="pvClear">清空重开</button>
          </div>
          <div class="pv-log" ref="pvLog">
            <div v-for="(m, i) in pvMsgs" :key="i" class="pv-msg" :class="m.cls">{{ m.text || (m.pending ? '…' : '') }}</div>
          </div>
          <div class="pv-bar">
            <el-input v-model="pvQ" placeholder="试一句……（回车发送）" @keydown.enter="onPvEnter" :disabled="pvBusy" />
            <el-button size="small" type="primary" :loading="pvBusy" @click="pvSend">试</el-button>
          </div>
          <div class="fnote">预览不落库、不建会话；书外问题应按「未命中口径」拒答。可接着追问验证多轮口径。</div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { appsApi, materialApi, settingsApi } from '../api'
import { streamSSE } from '../utils/sse'
import { SPIRIT_OPTIONS, PALETTES, spiritSVG, decoPreviewSVG, parseSpirit } from '../utils/spirit'
import { enterToSend } from '../utils/keys'

const route = useRoute()
const router = useRouter()
const appId = Number(route.params.id)
const app = ref(null)
const serverData = ref(null)     // 服务端最近一次真值：放弃改动时从这里回填，不必重新请求
const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const polishing = ref(false)
const drafting = ref(false)
const allReady = ref([])           // 材料库全部已解析资料
const models = ref([])
const form = reactive({ name: '', spirit: 'book', persona: '', boundaries: '', miss_text: '', welcome: '', questions: ['', '', ''], suggest_on: true, model_id: '' })
const curDeco = computed(() => parseSpirit(form.spirit).deco)

// ---------------- 未保存追踪 ----------------
// 为什么要做：改完配置点「返回对话」会直接走，改动全丢且零提示（原来既无 dirty 也无路由守卫）。
// 用「逐字段快照 + 差集」而不是单个布尔：要能告诉用户「哪几项没保存」，只报总数他得自己找。
const base = ref(null)
function currentState() {
  return {
    name: form.name.trim(),
    spirit: form.spirit,
    persona: form.persona.trim(),
    boundaries: form.boundaries.trim(),
    miss_text: form.miss_text.trim(),
    welcome: form.welcome.trim(),
    q1: (form.questions[0] || '').trim(),
    q2: (form.questions[1] || '').trim(),
    q3: (form.questions[2] || '').trim(),
    suggest_on: form.suggest_on,
    model_id: form.model_id,
    materials: [...boundIds.value].sort((a, b) => a - b).join(','),
  }
}
const FIELD_LABEL = {
  name: '名称', spirit: '形象', persona: '人格', boundaries: '回答边界',
  miss_text: '未命中口径', welcome: '开场白', q1: '建议问题', q2: '建议问题',
  q3: '建议问题', suggest_on: '追问建议', model_id: '问答模型', materials: '资料绑定',
}
const dirtyKeys = computed(() => {
  if (!base.value) return []
  const cur = currentState()
  return Object.keys(cur).filter(k => cur[k] !== base.value[k])
})
const dirty = computed(() => dirtyKeys.value.length > 0)
const dirtyCount = computed(() => dirtyKeys.value.length)
const dirtyLabel = computed(() => {
  const names = [...new Set(dirtyKeys.value.map(k => FIELD_LABEL[k] || k))]
  return `未保存：${names.join('、')}`
})
const materialsDirty = computed(() => dirtyKeys.value.includes('materials'))

function syncBase() { base.value = currentState() }

// 离开页面守卫：改过就问一句
onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      `${dirtyLabel.value}。离开将丢失这些改动。`,
      '有未保存的改动',
      { confirmButtonText: '放弃并离开', cancelButtonText: '继续编辑', type: 'warning' })
    return true
  } catch { return false }
})
// 刷新 / 关标签页守卫
function onBeforeUnload(e) {
  if (!dirty.value) return
  e.preventDefault()
  e.returnValue = ''
}
function goBack() { router.push(`/furnace/app/${appId}`) }

// ---------------- 形象 ----------------
function pickDeco(deco) {
  // 只换装饰、保留当前配色：直接构造令牌（后端校验过 regex）
  form.spirit = `g:${deco}:${parseSpirit(form.spirit).pi}`
}
function reroll() {
  // 本地换色、随保存生效（2026-10-29）。
  // ⚠️ 原实现调 POST /apps/{id}/spirit/reroll，那个接口**立即落库**：
  //    改完不点保存点「返回对话」，配色却已经变了 —— 与本页「保存才生效」的口径相反，
  //    用户也没法「预览一身看看，不喜欢就放弃」。现在只改表单，保存时才随 PATCH 提交。
  const cur = parseSpirit(form.spirit)
  const n = PALETTES.length
  if (n < 2) return
  let p = Math.floor(Math.random() * (n - 1))
  if (p >= cur.pi) p += 1        // 跳过当前色，保证「换」必有变化
  form.spirit = `g:${cur.deco}:${p}`
}

// ---------------- 资料绑定 ----------------
const boundIds = ref(new Set())
const boundMaterials = computed(() => allReady.value.filter(m => boundIds.value.has(m.id)))
const addableMaterials = computed(() => allReady.value.filter(m => !boundIds.value.has(m.id)))
const POOL_SHOW = 8
const poolOpen = ref(false)
const matQuery = ref('')
const filteredAddable = computed(() => {
  const kw = matQuery.value.trim().toLowerCase()
  if (!kw) return addableMaterials.value
  return addableMaterials.value.filter(m => (m.title || '').toLowerCase().includes(kw))
})
const visibleAddable = computed(() =>
  (poolOpen.value || matQuery.value) ? filteredAddable.value : filteredAddable.value.slice(0, POOL_SHOW))

function bind(id) { const s = new Set(boundIds.value); s.add(id); boundIds.value = s }
function unbind(id) {
  if (boundIds.value.size <= 1) { ElMessage.warning('最后一份资料不可移除 —— 减到 0 等于拆掉知识边界'); return }
  const s = new Set(boundIds.value); s.delete(id); boundIds.value = s
}

// ---------------- 看板 / 版本 ----------------
const stats = ref(null)
const versions = ref([])
const restoringId = ref(null)
function trendH(c) {
  const max = Math.max(1, ...(stats.value?.daily || []).map(d => d.count))
  return `${Math.round(4 + (c / max) * 36)}px`
}
async function loadStats() {
  try { const { data } = await appsApi.stats(appId); stats.value = data } catch { /* 看板失败不挡配置 */ }
}
async function loadVersions() {
  try { const { data } = await appsApi.versions(appId); versions.value = data } catch { /* 同上 */ }
}
async function restoreVer(v) {
  const extra = dirty.value ? `\n\n⚠️ 当前还有 ${dirtyCount.value} 项未保存的改动，回滚会一并丢弃。` : ''
  try {
    await ElMessageBox.confirm(
      `回滚到 v${v.version}？当前配置会先自动留档（回滚可撤销），历史对话不受影响。${extra}`,
      '回滚人格版本', { confirmButtonText: '回滚', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  restoringId.value = v.id
  try {
    await appsApi.restoreVersion(appId, v.id)
    ElMessage.success(`已回滚到 v${v.version}`)
    await load()
    await loadVersions()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '回滚失败') }
  finally { restoringId.value = null }
}

// ---------------- 加载 ----------------
function applyServer(data) {
  app.value = data
  serverData.value = data
  Object.assign(form, {
    name: data.name, spirit: data.spirit || 'book', persona: data.persona || '',
    boundaries: data.boundaries || '', miss_text: data.miss_text || '', welcome: data.welcome || '',
    questions: [data.questions?.[0] || '', data.questions?.[1] || '', data.questions?.[2] || ''],
    suggest_on: !!data.suggest_on, model_id: data.model_id || '',
  })
  boundIds.value = new Set(data.material_ids || [])
  syncBase()
}
async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await appsApi.get(appId)
    applyServer(data)
  } catch (e) {
    loadError.value = e?.response?.data?.detail || ''
    if (app.value) ElMessage.error(loadError.value || '应用加载失败')   // 已有内容时只提示，不清屏
  } finally { loading.value = false }
  if (app.value) { loadStats(); loadVersions() }
}
async function discard() {
  try {
    await ElMessageBox.confirm(
      `${dirtyLabel.value}。放弃后回到上次保存的状态。`,
      '放弃未保存的改动',
      { confirmButtonText: '放弃改动', cancelButtonText: '继续编辑', type: 'warning' })
  } catch { return }
  if (serverData.value) applyServer(serverData.value)
  else await load()
  ElMessage.success('已回到上次保存的状态')
}

// ---------------- 保存 ----------------
async function save() {
  if (!form.name.trim() || !form.persona.trim()) { ElMessage.warning('名称与人格不能为空'); return }
  if (!boundIds.value.size) { ElMessage.warning('至少绑定 1 份资料'); return }
  saving.value = true
  try {
    const { data } = await appsApi.update(appId, {
      ...form,
      questions: form.questions.filter(q => q.trim()),
      material_ids: [...boundIds.value],
    })
    app.value = data
    serverData.value = data
    syncBase()                       // 只有落库成功才重置基线，否则「保存失败仍显示已保存」
    ElMessage.success('已保存：资料与人格从下一条提问起生效')
    loadVersions()                   // 实质修改会留下新版本
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '保存失败') }
  finally { saving.value = false }
}

async function runDraft() {
  if (!boundIds.value.size) { ElMessage.warning('请先绑定资料'); return }
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        `AI 草稿会覆盖当前填写的内容（${dirtyLabel.value}）。继续？`,
        '覆盖当前填写', { confirmButtonText: '覆盖', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
  }
  drafting.value = true
  try {
    const { data } = await appsApi.assist({ kind: 'draft', material_ids: [...boundIds.value] })
    form.name = data.name || form.name
    form.spirit = data.spirit || form.spirit
    form.persona = data.persona || form.persona
    form.boundaries = data.boundaries || form.boundaries
    form.miss_text = data.miss_text || form.miss_text
    form.welcome = data.welcome || form.welcome
    const qs = data.questions || []
    form.questions = [qs[0] || '', qs[1] || '', qs[2] || '']
    ElMessage.success('AI 草稿已填入，确认后点保存才生效')
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '草稿生成失败') }
  finally { drafting.value = false }
}

async function polish(field) {
  const key = field === 'persona' ? 'persona' : 'boundaries'
  if (!form[key].trim()) { ElMessage.warning('先写点内容，或点 AI 草稿'); return }
  polishing.value = true
  try {
    const { data } = await appsApi.assist({ kind: 'polish', field, text: form[key], name: form.name })
    form[key] = data.text
    ElMessage.success('已优化：确认后点保存')
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '优化失败') }
  finally { polishing.value = false }
}

// ---------------- 试聊预览（多轮） ----------------
const pvTurns = 3
const pvMsgs = ref([{ cls: 'pv-kb', text: '你好，试着问我点什么 —— 书内、书外都试试，也可以接着追问。' }])
const pvHistory = ref([])        // [{role, content}]，只保留最近 pvTurns 轮
const pvQ = ref('')
const pvBusy = ref(false)
const pvLog = ref(null)

function pvScroll() {
  requestAnimationFrame(() => { if (pvLog.value) pvLog.value.scrollTop = pvLog.value.scrollHeight })
}
function remember(q, aiMsg) {
  const text = (aiMsg.text || '').trim()
  if (!text || aiMsg.isErr) return       // 报错内容不能进上下文，否则下一轮会「照着错误答」
  pvHistory.value.push({ role: 'user', content: q }, { role: 'assistant', content: text })
  const cap = pvTurns * 2
  if (pvHistory.value.length > cap) pvHistory.value = pvHistory.value.slice(-cap)
}
function pvClear() {
  pvMsgs.value = [{ cls: 'pv-kb', text: '已清空，重新开始试聊。' }]
  pvHistory.value = []
  pvQ.value = ''
}

async function pvSend() {
  const q = pvQ.value.trim()
  if (!q || pvBusy.value) return
  pvQ.value = ''
  pvMsgs.value.push({ cls: 'pv-user', text: q })
  const aiMsg = reactive({ cls: 'pv-kb', text: '', pending: true, isErr: false })
  pvMsgs.value.push(aiMsg)
  pvBusy.value = true
  pvScroll()
  try {
    await streamSSE('/api/apps/preview/stream', {
      name: form.name, persona: form.persona, boundaries: form.boundaries,
      miss_text: form.miss_text, material_ids: [...boundIds.value], question: q,
      model_id: form.model_id || '',
      // 多轮上下文：后端会再裁一次（只认 user/assistant、限 3 轮、总量截断），
      // 这里截断只是为了少传无用载荷。
      history: pvHistory.value.slice(-pvTurns * 2),
    },
      (t) => { aiMsg.text += t; aiMsg.pending = false; pvScroll() },
      () => { aiMsg.pending = false; pvBusy.value = false; remember(q, aiMsg) },
      (msg) => {
        aiMsg.text = msg || '生成失败'; aiMsg.cls = 'pv-miss'; aiMsg.isErr = true
        aiMsg.pending = false; pvBusy.value = false
      },
      (meta) => { aiMsg.cls = meta.kb_hit ? 'pv-kb' : 'pv-miss' })
  } catch (e) {
    aiMsg.text = (e && e.message) || '请求失败'
    aiMsg.cls = 'pv-miss'
    aiMsg.isErr = true
    aiMsg.pending = false
    pvBusy.value = false
  }
}
const onPvEnter = enterToSend(pvSend)

onMounted(async () => {
  window.addEventListener('beforeunload', onBeforeUnload)
  load()
  try {
    const { data } = await materialApi.list()
    allReady.value = data.filter(m => m.parsed_status === 'success')
  } catch (e) { /* 追加区为空即可 */ }
  try {
    const { data } = await settingsApi.get()
    models.value = data.llm_models || []
  } catch (e) { /* 隐藏模型行 */ }
})
onUnmounted(() => window.removeEventListener('beforeunload', onBeforeUnload))
</script>

<style scoped>
.fhead { position: sticky; top: 0; z-index: 12; background: var(--asc-bg);
  display: flex; justify-content: space-between; align-items: flex-start; gap: 16px;
  margin: 0 -4px 14px; padding: 6px 4px 10px; border-bottom: 1px solid transparent;
  transition: border-color .18s ease; }
.fhead:hover { border-bottom-color: var(--asc-divider); }
.fh-main { min-width: 0; }
.fh-ops { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.dirty-pill { font-size: 12px; font-weight: 500; color: #b45309; background: #fdf6ec;
  border-radius: 999px; padding: 2px 10px; white-space: nowrap; cursor: default; }
.fhead h2 { font-size: 20px; font-weight: 600; margin: 0 0 6px; }
.tag-ok { font-size: 11px; font-weight: 500; border-radius: 4px; padding: 1px 7px; background: #f0f9eb; color: #67c23a; }
.tag-draft { font-size: 11px; font-weight: 500; border-radius: 4px; padding: 1px 7px; background: #fdf6ec; color: #b45309; }
.fsub { margin: 0; font-size: 13px; color: var(--asc-text-2); line-height: 1.7; }
.fe-grid { display: grid; grid-template-columns: 1fr 340px; gap: 14px; align-items: start; }
.card { background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: var(--asc-radius); padding: 18px 20px; margin-bottom: 14px; }
.ctitle { font-size: 14px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.fh { font-size: 12px; color: var(--asc-text-2); font-weight: 400; }
.unsaved-tag { font-size: 11px; font-weight: 500; color: #b45309; background: #fdf6ec; border-radius: 4px; padding: 1px 6px; }
.fnote { font-size: 12px; color: var(--asc-text-2); margin-top: 10px; line-height: 1.6; }
.mb-lab { margin: 12px 0 6px; }
.mb-search { width: 240px; margin-bottom: 8px; }
.mb-row { display: flex; flex-wrap: wrap; gap: 6px; }
.mat-chip { display: inline-flex; align-items: center; gap: 6px; background: var(--asc-surface-2); border-radius: 6px; padding: 3px 10px; font-size: 12.5px; color: var(--asc-text-2); }
.mb-x { border: none; background: transparent; color: var(--asc-text-2); cursor: pointer; font-size: 13px; padding: 0 2px; border-radius: 3px; line-height: 1; }
.mb-x:hover:not(:disabled) { color: #d85a30; }
.mb-x:disabled { opacity: .35; cursor: not-allowed; }
.mat-chip.add { border: 1px dashed var(--asc-border); background: transparent; cursor: pointer; }
.mat-chip.add:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
.pool-toggle { font-weight: 500; color: var(--asc-primary); border-color: rgba(124, 92, 252, .4); }
.frow { margin-bottom: 14px; }
.frow label { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; margin-bottom: 6px; }
.frow label i { color: #d85a30; font-style: normal; }
.fq { margin-bottom: 8px; }
.cfg-row { display: flex; justify-content: space-between; align-items: center; font-size: 13.5px; gap: 10px; }
.mini-btn { border: 1px solid rgba(124, 92, 252, .4); background: var(--asc-primary-soft); color: var(--asc-primary); font-size: 11.5px; padding: 2px 9px; border-radius: 999px; cursor: pointer; font-family: inherit; font-weight: 400; }
.frow label .mini-btn { margin-left: auto; }
.mini-btn:hover { background: rgba(124, 92, 252, .2); }
.mini-btn:disabled { opacity: .5; cursor: default; }
/* 数据看板 */
.stats-row { display: flex; gap: 26px; flex-wrap: wrap; margin-bottom: 12px; }
.stat-n { font-size: 22px; font-weight: 600; color: var(--asc-text); }
.stat-n.stat-sm { font-size: 14px; padding-top: 7px; }
.stat-l { font-size: 12px; color: var(--asc-text-2); margin-top: 2px; }
.trend { display: flex; align-items: flex-end; gap: 6px; padding-top: 8px; border-top: 1px dashed var(--asc-border); }
.trend-col { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.trend-bar { width: 20px; border-radius: 4px 4px 0 0; background: var(--asc-primary-soft); border: 1px solid rgba(124, 92, 252, .35); border-bottom: none; }
.trend-d { font-size: 10.5px; color: var(--asc-text-2); }
.trend-cap { margin-left: auto; align-self: center; }
/* 人格版本 */
.ver-list { display: flex; flex-direction: column; gap: 8px; }
.ver-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid var(--asc-border); border-radius: 10px; }
.ver-tag { flex-shrink: 0; font-size: 11px; font-weight: 600; color: var(--asc-primary); background: var(--asc-primary-soft); border-radius: 4px; padding: 2px 7px; }
.ver-body { flex: 1; min-width: 0; }
.ver-head { font-size: 12.5px; font-weight: 500; }
.ver-head .fh { margin-left: 6px; }
.ver-persona { font-size: 12px; color: var(--asc-text-2); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 形象 */
.spirit-opts { display: flex; gap: 10px; flex-wrap: wrap; }
.spirit-edit { display: flex; flex-direction: column; gap: 10px; }
.spirit-true { display: flex; align-items: center; gap: 14px; padding: 10px 14px; background: var(--asc-surface-2); border-radius: 12px; width: fit-content; }
.spirit-true-ico { display: inline-flex; }
.spirit-true-ops { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.spirit-breathe :deep(svg) { animation: breathe 3.2s ease-in-out infinite; transform-origin: 50% 88%; }
@keyframes breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.035); } }
.spirit-opt { border: 1.5px solid var(--asc-border); border-radius: 10px; background: #fff; padding: 10px 16px; cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 6px; font-size: 12.5px; color: var(--asc-text-2); transition: all .15s ease; }
.spirit-opt:hover { border-color: #c9c4e8; }
.spirit-opt.on { border-color: var(--asc-primary); background: var(--asc-primary-soft); color: var(--asc-primary); font-weight: 500; }
.save-bar { display: flex; justify-content: flex-end; align-items: center; gap: 10px; border-top: 1px solid var(--asc-divider); padding-top: 14px; }
.save-hint { margin-right: auto; }
/* 试聊预览 */
.pv-card { position: sticky; top: 66px; }
.pv-sub { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: -4px 0 10px; }
.pv-clear { flex-shrink: 0; }
.pv-log { height: 320px; overflow-y: auto; background: var(--asc-surface-2); border-radius: 8px; padding: 10px; margin-bottom: 10px; }
.pv-msg { font-size: 12.5px; line-height: 1.6; padding: 7px 10px; border-radius: 8px; margin-bottom: 8px; max-width: 95%; white-space: pre-wrap; }
.pv-user { background: var(--asc-primary); color: #fff; margin-left: 8%; width: fit-content; }
.pv-kb { background: #fff; border: 1px solid var(--asc-border); }
.pv-miss { background: #fdf6ec; border: 1px solid #faecd8; color: var(--asc-text-2); }
.pv-bar { display: flex; gap: 8px; }
/* 首屏骨架 / 失败态 */
.fe-boot { padding: 8px 0; }
.sk-bar { height: 18px; border-radius: 6px; background: var(--asc-surface-2); margin-bottom: 10px; }
.sk-bar.w50 { width: 50%; height: 22px; }
.sk-bar.w70 { width: 70%; }
.sk-bar.thin { height: 12px; }
.sk-card { height: 120px; border-radius: 12px; background: var(--asc-surface-2); margin-bottom: 12px; }
.sk-card.tall { height: 320px; }
.sk-tx { font-size: 12.5px; color: var(--asc-text-2); margin: 8px 0 0; }
.fe-fail { padding: 64px 0; text-align: center; }
.fail-t { font-size: 15px; font-weight: 600; margin-bottom: 6px; }
.fail-s { font-size: 12.5px; color: var(--asc-text-2); margin: 0 0 16px; }
.fail-ops { display: flex; gap: 8px; justify-content: center; }
@media (max-width: 1100px) { .fe-grid { grid-template-columns: 1fr; } .pv-card { position: static; } }
</style>
