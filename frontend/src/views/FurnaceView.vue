<template>
  <div class="page furnace-page">
    <div class="fhead">
      <div>
        <h2>炼丹炉</h2>
        <p class="fsub">把任意资料炼成独立的 AI 应用 —— 专属人格、知识边界、独立会话，为 IP 孵化而生。</p>
      </div>
      <el-button type="primary" @click="openWizard">＋ 开炉炼应用</el-button>
    </div>

    <div v-loading="loading" class="fbody">
      <!-- 空状态：丹房还空着 -->
      <div v-if="!loading && !apps.length" class="fempty">
        <div class="fempty-furnace" v-html="furnaceSVG(170, 'fe')"></div>
        <div class="fempty-t">丹房还空着</div>
        <div class="fempty-s">选一炉资料，炼出第一个有专属人格的 AI 助手</div>
        <el-button type="primary" @click="openWizard">＋ 开炉炼应用</el-button>
      </div>
      <div v-else class="app-grid">
        <div v-for="a in apps" :key="a.id" class="app-card" :style="themeOf(a.spirit)">
          <!-- 气质带：丹灵配色升格为页面色，每炉气质独版 -->
          <div class="ac-hero">
            <span v-if="a.status === 'draft'" class="tag-draft" title="绑定的资料已被删除，重新绑定资料即可复活">待补料</span>
            <span v-else class="tag-ok">运行中</span>
            <div class="ac-spirit spirit-breathe" v-html="spiritSVG(a.spirit, 62, 'normal')"></div>
            <div class="ac-name">{{ a.name }}</div>
          </div>
          <div class="ac-body">
            <p class="ac-persona">{{ (a.persona_head || '').slice(0, 46) }}<template v-if="(a.persona_head || '').length >= 300">…</template></p>
            <div class="ac-meta">绑定 {{ a.material_count }} 份资料 · 已聊 {{ a.session_count }} 场</div>
            <div class="ac-ops">
              <el-button v-if="a.status === 'draft'" type="primary" class="ac-go"
                @click="$router.push(`/furnace/app/${a.id}/edit`)">补料后复活</el-button>
              <el-button v-else type="primary" class="ac-go" @click="$router.push(`/furnace/app/${a.id}`)">进入对话</el-button>
              <el-button v-if="a.status !== 'draft'" text @click="$router.push(`/furnace/app/${a.id}/edit`)">配置</el-button>
              <el-button v-else text @click="$router.push(`/furnace/app/${a.id}`)">进入对话</el-button>
              <el-popconfirm title="删除该应用？其会话与消息一并清除，资料本体不受影响。" width="240"
                confirm-button-text="删除" confirm-button-type="danger" cancel-button-text="取消"
                @confirm="removeApp(a)">
                <template #reference><button class="ac-more" title="删除应用">···</button></template>
              </el-popconfirm>
            </div>
          </div>
        </div>
        <!-- 空炉位：开新一炉 -->
        <div class="app-new" @click="openWizard">
          <span class="an-furnace" v-html="furnaceSVG(72, 'fn')"></span>
          <span class="an-t">开一炉新应用</span>
          <span class="hint">选资料 → 定型 → 出炉，3 步</span>
        </div>
      </div>
    </div>

    <!-- ===== 创建向导：投料 → 定型 → 出炉 ===== -->
    <div v-if="wiz.open" class="wiz-mask" @click.self="closeWizard()">
      <div class="wiz-card">
        <div class="steps">
          <span v-for="(s, i) in ['① 投料', '② 定型', '③ 出炉']" :key="i" class="step" :class="{ on: wiz.step === i + 1 }">{{ s }}</span>
          <span class="wiz-x" title="关闭（内容会暂存）" @click="closeWizard()">×</span>
        </div>

        <!-- ---------- ① 投料 ---------- -->
        <div v-if="wiz.step === 1">
          <div class="wz-sub-row">
            <p class="wz-sub">选择要炼入的资料（至少 1 份）。这个应用的一切回答只来自选中的资料。</p>
            <el-input v-if="readyMaterials.length > 6" v-model="matQuery" size="small" clearable
              class="wz-search" placeholder="搜索资料标题…" />
          </div>
          <div class="mat-grid">
            <div v-for="m in visibleMats" :key="m.id" class="pick" :class="{ on: wiz.sel.has(m.id) }" @click="togglePick(m.id)">
              <div class="pick-check">✓</div>
              <div class="pick-name">{{ m.title }}</div>
              <div class="pick-meta">{{ (m.format || '').toUpperCase() }}<template v-if="m.page_count"> · {{ m.page_count }} 页</template></div>
            </div>
          </div>
          <el-empty v-if="!readyMaterials.length" description="没有已解析完成的资料，请先到材料库导入" :image-size="60" />
          <div v-else-if="!visibleMats.length" class="wz-empty">没有匹配「{{ matQuery }}」的资料</div>
          <div class="wz-bar">
            <span class="wz-count">已选 <b>{{ wiz.sel.size }}</b> 份</span>
            <span v-if="!wiz.sel.size" class="wz-hint">至少选择 1 份资料才能继续</span>
            <div class="wz-ops">
              <el-button @click="closeWizard()">取消</el-button>
              <el-button type="primary" :disabled="!wiz.sel.size" @click="wiz.step = 2">下一步：定型</el-button>
            </div>
          </div>
        </div>

        <!-- ---------- ② 定型 ---------- -->
        <div v-else-if="wiz.step === 2" class="wz-form">
          <!-- 起草引导：放在最前面。原来 AI 草稿按钮在表单末尾，用户先看到 7 个空字段，
               根本不知道该能一键起草 —— 这是「智能」被埋在最深处。 -->
          <div v-if="!wiz.draftBanner" class="wz-guide">
            <div class="wg-txt">
              <b>7 个字段不知道怎么写？</b>
              <span class="fh">让 AI 基于所选资料先起草，你再逐条改 —— 人格是 IP 的核心资产，别照抄。</span>
            </div>
            <el-button size="small" :loading="wiz.drafting" @click="runDraft">✨ AI 起草</el-button>
          </div>
          <el-alert v-else type="warning" :closable="false" class="wz-draft"
            title="以下为 AI 基于所选资料生成的草稿，请逐条修改确认后再出炉" />

          <div class="wz-group">① 身份</div>
          <div class="frow">
            <label>应用名称 <i>*</i></label>
            <el-input v-model="wiz.form.name" maxlength="64" placeholder="例如：《政治学十五讲》领读人" />
          </div>
          <div class="frow">
            <label>形象 <span class="fh">应用的 IP 形象，对话页常驻陪伴</span></label>
            <div class="spirit-opts">
              <div v-for="sp in SPIRIT_OPTIONS" :key="sp.id" class="spirit-opt" :class="{ on: wiz.form.spirit === sp.id }" @click="wiz.form.spirit = sp.id">
                <span v-html="decoPreviewSVG(sp.id, 44)"></span>{{ sp.label }}
              </div>
            </div>
            <div class="fh mt6">选的是气质（头顶装饰）；配色由应用名哈希定 —— 每个应用都是独版，出炉可见真身。</div>
          </div>

          <div class="wz-group">② 人格与边界</div>
          <div class="frow">
            <label>
              人格设定 <i>*</i>
              <button v-if="canUndo.persona" class="mini-btn undo" @click="undoPolish('persona')">撤销优化</button>
              <button class="mini-btn" :disabled="wiz.polishing" @click="polish('persona')">AI 优化</button>
            </label>
            <el-input v-model="wiz.form.persona" type="textarea" :rows="4" maxlength="4000" show-word-limit
              placeholder="它是谁、站什么视角、怎么说话……" />
          </div>
          <div class="frow">
            <label>
              回答边界 <span class="fh">只答什么、拒答什么</span>
              <button v-if="canUndo.bounds" class="mini-btn undo" @click="undoPolish('bounds')">撤销优化</button>
              <button class="mini-btn" :disabled="wiz.polishing" @click="polish('bounds')">AI 优化</button>
            </label>
            <el-input v-model="wiz.form.boundaries" type="textarea" :rows="3" maxlength="2000" show-word-limit
              placeholder="只答资料范围内的内容；拒绝时事、投资、医疗等无关领域……" />
          </div>
          <div class="frow">
            <label>未命中口径 <span class="fh">超出资料范围时的拒答话术</span></label>
            <el-input v-model="wiz.form.miss_text" maxlength="500" placeholder="例如：这个问题不在本书范围内……" />
          </div>

          <div class="wz-group">③ 对话开场</div>
          <div class="frow">
            <label>开场白</label>
            <el-input v-model="wiz.form.welcome" type="textarea" :rows="2" maxlength="500" placeholder="用户进入对话时看到的第一句话" />
          </div>
          <div class="frow">
            <label>建议问题 <span class="fh">最多 3 条，留空则不显示</span></label>
            <el-input v-for="i in 3" :key="i" v-model="wiz.form.questions[i - 1]" class="fq" maxlength="80" :placeholder="`问题 ${i}`" />
          </div>
          <div class="frow" v-if="models.length">
            <label>问答模型 <span class="fh">该应用的默认模型；对话页可临时切换</span></label>
            <el-select v-model="wiz.form.model_id" class="fmodel">
              <el-option label="跟随全局默认" value="" />
              <el-option v-for="m in models" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
            </el-select>
          </div>

          <div class="wz-bar">
            <el-button :loading="wiz.drafting" @click="runDraft">✨ AI 草稿（覆盖当前填写）</el-button>
            <div class="wz-ops">
              <span v-if="missing.length" class="wz-hint">还需填写：{{ missing.join('、') }}</span>
              <el-button @click="wiz.step = 1">上一步</el-button>
              <el-button type="primary" :disabled="!shapeOk" @click="wiz.step = 3">下一步：出炉</el-button>
            </div>
          </div>
        </div>

        <!-- ---------- ③ 出炉 ---------- -->
        <div v-else class="wz-sum">
          <div class="sum-head">
            <div class="sum-icon" v-html="spiritSVG(rollSpiritPreview(wiz.form.name, 0, wiz.form.spirit), 52, 'normal')"></div>
            <div>
              <div class="sum-name">{{ wiz.form.name }}</div>
              <div class="sum-sub">绑定 {{ wiz.sel.size }} 份资料 · {{ modelLabel }}</div>
            </div>
          </div>
          <div class="sum-row">
            <span class="sum-k">人格设定</span><span class="sum-v">{{ wiz.form.persona }}</span>
            <button class="sum-edit" @click="wiz.step = 2">改</button>
          </div>
          <div class="sum-row" v-if="wiz.form.boundaries">
            <span class="sum-k">回答边界</span><span class="sum-v">{{ wiz.form.boundaries }}</span>
            <button class="sum-edit" @click="wiz.step = 2">改</button>
          </div>
          <div class="sum-row" v-if="wiz.form.miss_text">
            <span class="sum-k">未命中口径</span><span class="sum-v">{{ wiz.form.miss_text }}</span>
            <button class="sum-edit" @click="wiz.step = 2">改</button>
          </div>
          <div class="sum-row" v-if="wiz.form.welcome">
            <span class="sum-k">开场白</span><span class="sum-v">{{ wiz.form.welcome }}</span>
            <button class="sum-edit" @click="wiz.step = 2">改</button>
          </div>
          <div class="sum-row">
            <span class="sum-k">绑定资料</span>
            <span class="sum-v">
              <span v-for="m in pickedMaterials" :key="m.id" class="mat-chip">{{ m.title }}</span>
            </span>
            <button class="sum-edit" @click="wiz.step = 1">改</button>
          </div>
          <div class="wz-bar">
            <span class="wz-note">出炉后资料与人格都可随时再改</span>
            <div class="wz-ops">
              <el-button @click="wiz.step = 2">上一步</el-button>
              <el-button type="primary" :loading="wiz.creating" @click="doCreate">确认出炉</el-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 揭炉仪式（创建成功后播放一次） ===== -->
    <div v-if="ritual.open" class="ritual-mask" @click.self="dismissRitual">
      <div class="ritual" :class="{ play: ritual.play }">
        <button class="ritual-x" title="关闭" @click="dismissRitual">×</button>
        <div class="ritual-stage">
          <div class="ritual-glow rv"></div>
          <div class="ritual-spirit rv" v-html="ritual.spiritHtml"></div>
          <div class="furnace-wrap" :class="{ 'can-open': ritual.canOpen }" @click="openLid" v-html="ritualFurnace">
          </div>
        </div>
        <div class="brew-stage">{{ ritual.stageText }}</div>
        <div class="ritual-hint" :class="{ vis: ritual.canOpen }">点击揭炉</div>
        <div class="ritual-text rv">丹成！</div>
        <div class="ritual-name rv">{{ ritual.appName }}</div>
        <div class="ritual-sub rv">人格与知识边界已定型 —— 从现在起，它只为这些资料开口</div>
        <div class="ritual-ops">
          <el-button v-if="!ritual.play" text @click="enterApp">跳过动画</el-button>
          <el-button class="rv ritual-btn" type="primary" @click="enterApp">进入对话</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { appsApi, materialApi, settingsApi } from '../api'
import { SPIRIT_OPTIONS, spiritSVG, decoPreviewSVG, rollSpiritPreview, spiritTheme, furnaceSVG } from '../utils/spirit'

function themeOf(spirit) {
  const t = spiritTheme(spirit)
  return { '--app-body': t.body, '--app-edge': t.edge }
}

const router = useRouter()
const apps = ref([])
const loading = ref(true)
const readyMaterials = ref([])
const models = ref([])

const emptyForm = () => ({ name: '', spirit: 'book', persona: '', boundaries: '', miss_text: '', welcome: '', questions: ['', '', ''], model_id: '' })
const wiz = reactive({ open: false, step: 1, sel: new Set(), form: emptyForm(), drafting: false, polishing: false, creating: false, draftBanner: false })
const ritual = reactive({ open: false, play: false, canOpen: false, appName: '', spiritHtml: '', stageText: '', appId: null, spirit: 'book' })
const ritualFurnace = furnaceSVG(240, 'rit')
let brewTimers = []

// 关掉向导时的内存级暂存（2026-10-29）：不落盘 —— 人格文案属产品资产，不该进 localStorage。
// 语义：同一页面会话内再点「开炉」能续上；刷新即失效。
let stash = null

const shapeOk = computed(() => wiz.form.name.trim() && wiz.form.persona.trim())
const missing = computed(() => {
  // 只变灰不说明原因 = 用户点不动按钮还不知道缺什么（第 1 步有提示、第 2 步原来没有）
  const m = []
  if (!wiz.form.name.trim()) m.push('应用名称')
  if (!wiz.form.persona.trim()) m.push('人格设定')
  return m
})
const pickedMaterials = computed(() => readyMaterials.value.filter(m => wiz.sel.has(m.id)))
const modelLabel = computed(() => {
  if (!wiz.form.model_id) return '跟随全局默认'
  const m = models.value.find(x => x.id === wiz.form.model_id)
  return m ? m.name : '跟随全局默认'
})

// ---- ① 投料：搜索 + 已选置顶 ----
const matQuery = ref('')
const visibleMats = computed(() => {
  const kw = matQuery.value.trim().toLowerCase()
  const list = kw ? readyMaterials.value.filter(m => (m.title || '').toLowerCase().includes(kw)) : readyMaterials.value
  // sort 稳定（ES2019+）：同组内保持材料库原顺序，勾选后只是「跳到前面」
  return [...list].sort((a, b) => (wiz.sel.has(b.id) ? 1 : 0) - (wiz.sel.has(a.id) ? 1 : 0))
})

// ---- AI 优化可撤销：改写是不可逆破坏性操作，用户不满意时不能只剩「凭记忆重打」 ----
const undoText = reactive({ persona: '', bounds: '' })
const canUndo = computed(() => ({ persona: !!undoText.persona, bounds: !!undoText.bounds }))

async function loadApps() {
  loading.value = true
  try {
    const { data } = await appsApi.list()
    apps.value = data
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '加载失败') }
  finally { loading.value = false }
}

async function loadMaterials() {
  try {
    const { data } = await materialApi.list()
    readyMaterials.value = data.filter(m => m.parsed_status === 'success')
  } catch (e) { /* 列表失败时选择区为空，el-empty 已兜底 */ }
}

function hasDraftInput() {
  if (wiz.step > 1 || wiz.sel.size) return true
  if (['name', 'persona', 'boundaries', 'miss_text', 'welcome'].some(k => (wiz.form[k] || '').trim())) return true
  return wiz.form.questions.some(q => (q || '').trim())
}

async function openWizard() {
  const restored = !!stash
  wiz.open = true
  if (stash) {
    wiz.step = stash.step
    wiz.form = stash.form
    wiz.sel = stash.sel
    wiz.draftBanner = stash.draftBanner
    Object.assign(undoText, stash.undoText)
    stash = null
  } else {
    wiz.step = 1; wiz.sel = new Set(); wiz.form = emptyForm(); wiz.draftBanner = false
    Object.assign(undoText, { persona: '', bounds: '' })
  }
  matQuery.value = ''
  // 每次开炉都重取材料列表（2026-10-29 修）：原实现只在「列表为空」时请求一次，
  // 本次会话里新导入的资料因此永远不会出现在投料区，必须刷新页面才可见。
  await loadMaterials()
  // 恢复暂存时，勾选的资料可能已被删除 —— 不清理会让「已选 3 份」实际只绑到 2 份
  if (wiz.sel.size) {
    const alive = new Set(readyMaterials.value.map(m => m.id))
    const kept = new Set([...wiz.sel].filter(id => alive.has(id)))
    if (kept.size !== wiz.sel.size) {
      wiz.sel = kept
      ElMessage.warning('有资料已从材料库移除，已自动取消勾选')
    }
  }
  if (restored) ElMessage.success('已恢复上次未完成的填写')
}

async function closeWizard() {
  if (hasDraftInput()) {
    try {
      await ElMessageBox.confirm(
        '已填写的内容会暂存在本页，下次点「开炉」可继续；离开本页面则丢弃。',
        '关闭开炉向导',
        { confirmButtonText: '关闭并暂存', cancelButtonText: '继续填写', type: 'warning' })
    } catch { return }
    stash = {
      step: wiz.step,
      sel: new Set(wiz.sel),
      form: JSON.parse(JSON.stringify(wiz.form)),
      draftBanner: wiz.draftBanner,
      undoText: { ...undoText },
    }
  }
  wiz.open = false
}

function togglePick(id) {
  const s = new Set(wiz.sel)
  if (s.has(id)) s.delete(id); else s.add(id)
  wiz.sel = s
}

async function runDraft() {
  if (!wiz.sel.size) { ElMessage.warning('请先回到第①步选择资料'); return }
  // 已有手填内容时先确认：草稿是整体覆盖，不是合并
  const hasInput = ['name', 'persona', 'boundaries', 'miss_text', 'welcome'].some(k => (wiz.form[k] || '').trim())
  if (hasInput) {
    try {
      await ElMessageBox.confirm('AI 草稿会覆盖你已填写的内容。继续？', '覆盖当前填写',
        { confirmButtonText: '覆盖', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
  }
  wiz.drafting = true
  try {
    const { data } = await appsApi.assist({ kind: 'draft', material_ids: [...wiz.sel] })
    wiz.form.name = data.name || wiz.form.name
    wiz.form.spirit = data.spirit || 'book'
    wiz.form.persona = data.persona || ''
    wiz.form.boundaries = data.boundaries || ''
    wiz.form.miss_text = data.miss_text || ''
    wiz.form.welcome = data.welcome || ''
    const qs = data.questions || []
    wiz.form.questions = [qs[0] || '', qs[1] || '', qs[2] || '']
    wiz.draftBanner = true
    Object.assign(undoText, { persona: '', bounds: '' })
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '草稿生成失败') }
  finally { wiz.drafting = false }
}

async function polish(field) {
  const key = field === 'persona' ? 'persona' : 'boundaries'
  if (!wiz.form[key].trim()) { ElMessage.warning('先写点内容，或点 AI 起草'); return }
  const before = wiz.form[key]
  wiz.polishing = true
  try {
    const { data } = await appsApi.assist({ kind: 'polish', field, text: wiz.form[key], name: wiz.form.name })
    if (data.text && data.text !== before) undoText[key] = before    // 只有真变了才给撤销点
    wiz.form[key] = data.text
    ElMessage.success('已优化：不满意可点「撤销优化」')
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '优化失败') }
  finally { wiz.polishing = false }
}
function undoPolish(field) {
  const key = field === 'persona' ? 'persona' : 'boundaries'
  if (!undoText[key]) return
  wiz.form[key] = undoText[key]
  undoText[key] = ''
  ElMessage.success('已恢复优化前的内容')
}

async function doCreate() {
  wiz.creating = true
  try {
    const { data } = await appsApi.create({
      ...wiz.form,
      questions: wiz.form.questions.filter(q => q.trim()),
      material_ids: [...wiz.sel],
    })
    wiz.open = false
    stash = null
    startRitual(data)
    loadApps()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '创建失败') }
  finally { wiz.creating = false }
}

// ---- 揭炉仪式 ----
const BREW_STAGES = ['投料入炉…', '文火萃取中…', '丹香渐起…', '丹成了 —— 点击揭炉']
function startRitual(app) {
  ritual.open = true; ritual.play = false; ritual.canOpen = false
  ritual.appName = app.name; ritual.appId = app.id; ritual.spiritHtml = ''
  // 直接存创建接口返回的 spirit（2026-10-29 修）：原来 openLid 里从 apps.value 反查，
  // 而 loadApps 是异步的 —— 列表还没回来时反查不到 → 兜底成 book，用户选的形象没显示。
  ritual.spirit = app.spirit || 'book'
  ritual.stageText = BREW_STAGES[0]
  brewTimers.forEach(clearTimeout); brewTimers = []
  BREW_STAGES.forEach((s, i) => {
    if (i > 0) brewTimers.push(setTimeout(() => {
      ritual.stageText = s
      if (i === BREW_STAGES.length - 1) ritual.canOpen = true
    }, i * 900))
  })
}
function openLid() {
  if (!ritual.canOpen || ritual.play) return
  brewTimers.forEach(clearTimeout); brewTimers = []
  ritual.canOpen = false; ritual.stageText = ''
  ritual.spiritHtml = spiritSVG(ritual.spirit, 80, 'normal')
  ritual.play = true
}
function clearBrew() { brewTimers.forEach(clearTimeout); brewTimers = [] }
function dismissRitual() { clearBrew(); ritual.open = false }        // 只想关掉，留在列表看新卡片
function enterApp() { clearBrew(); ritual.open = false; router.push(`/furnace/app/${ritual.appId}`) }

async function removeApp(a) {
  try {
    await appsApi.remove(a.id)
    ElMessage.success('已删除')
    loadApps()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '删除失败') }
}

// ESC 逐层关闭：仪式 → 向导（向导走暂存确认）
function onKeydown(e) {
  if (e.key !== 'Escape') return
  if (ritual.open) { dismissRitual(); return }
  if (wiz.open) closeWizard()
}

// 弹层打开时锁背景滚动：遮罩是 fixed，背景能滚会让页面在弹层后面「自己动」
watch(() => wiz.open || ritual.open, (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
})

onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
  loadApps()
  try {
    const { data } = await settingsApi.get()
    models.value = data.llm_models || []
  } catch (e) { /* 模型清单拉取失败 → 隐藏模型选择行 */ }
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  clearBrew()
  document.body.style.overflow = ''
})
</script>

<style scoped>
.fhead { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }
.fhead h2 { font-size: 20px; font-weight: 600; margin: 0 0 6px; }
.fsub { margin: 0; font-size: 13px; color: var(--asc-text-2); line-height: 1.7; }
.fbody { min-height: 300px; }
/* 空状态：丹房 */
.fempty { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 72px 0 80px; }
.fempty-furnace { opacity: .9; margin-bottom: 6px; }
.fempty-t { font-size: 17px; font-weight: 600; }
.fempty-s { font-size: 13px; color: var(--asc-text-2); margin-bottom: 14px; }
.app-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; margin-top: 8px; }
/* 气质卡片：上气质带 + 下信息区 */
.app-card { background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 16px; overflow: hidden; transition: box-shadow .18s ease, transform .18s ease; }
.app-card:hover { box-shadow: 0 10px 28px rgba(60, 50, 120, .12); transform: translateY(-2px); }
.ac-hero { position: relative; padding: 20px 16px 12px; text-align: center;
  background: linear-gradient(180deg, color-mix(in srgb, var(--app-body) 60%, transparent), transparent 88%); }
.ac-hero .tag-ok, .ac-hero .tag-draft { position: absolute; top: 10px; right: 10px; }
.ac-spirit { position: relative; height: 62px; display: flex; align-items: flex-end; justify-content: center; }
.ac-spirit::before { content: ''; position: absolute; bottom: -4px; left: 50%; margin-left: -44px; width: 88px; height: 88px;
  border-radius: 50%; background: var(--app-body); opacity: .5; filter: blur(14px); }
.ac-spirit :deep(svg) { position: relative; }
.ac-name { position: relative; font-size: 15px; font-weight: 600; margin-top: 8px; }
.tag-ok { font-size: 11px; font-weight: 500; border-radius: 4px; padding: 1px 7px; background: #f0f9eb; color: #67c23a; }
.tag-draft { font-size: 11px; font-weight: 500; border-radius: 4px; padding: 1px 7px; background: #fdf6ec; color: #b45309; }
.ac-body { padding: 10px 16px 14px; }
.ac-persona { margin: 0 0 8px; font-size: 12.5px; color: var(--asc-text-2); line-height: 1.6; height: 40px; overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.ac-meta { font-size: 12px; color: var(--asc-text-2); margin-bottom: 12px; }
.ac-ops { display: flex; gap: 6px; align-items: center; }
.ac-go { flex: 1; }
.ac-more { border: none; background: transparent; color: var(--asc-text-3); cursor: pointer; font-size: 16px; letter-spacing: 1px;
  padding: 4px 8px; border-radius: 6px; line-height: 1; transition: all .15s ease; }
.ac-more:hover { color: #d85a30; background: #fdf6ec; }
/* 空炉位 */
.app-new { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; min-height: 210px;
  cursor: pointer; color: var(--asc-text-2); border-radius: 16px; background: var(--asc-surface-2);
  transition: all .18s ease; }
.app-new:hover { color: var(--asc-primary); background: var(--asc-primary-soft); }
.app-new:hover .an-furnace { transform: translateY(-3px); }
.an-furnace { transition: transform .2s ease; opacity: .85; }
.an-t { font-size: 14px; font-weight: 500; }
.app-new .hint { font-size: 12px; }
.spirit-breathe :deep(svg) { animation: breathe 3.2s ease-in-out infinite; transform-origin: 50% 88%; }
@keyframes breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.035); } }

/* 向导 */
.wiz-mask { position: fixed; inset: 0; background: rgba(30, 27, 46, .45); z-index: 100; display: flex; align-items: center; justify-content: center; }
.wiz-card { background: var(--asc-card); border-radius: 14px; width: 660px; max-width: calc(100vw - 64px); max-height: 86vh; overflow-y: auto; padding: 22px 26px; }
.steps { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; }
.step { padding: 6px 14px; border-radius: 999px; background: var(--asc-surface-2); color: var(--asc-text-2); font-size: 12.5px; }
.step.on { background: var(--asc-primary-soft); color: var(--asc-primary); font-weight: 500; }
.wiz-x { margin-left: auto; cursor: pointer; color: var(--asc-text-2); font-size: 18px; padding: 0 6px; }
.wiz-x:hover { color: var(--asc-text); }
.wz-sub-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.wz-sub { margin: 0 0 12px; font-size: 13px; color: var(--asc-text-2); }
.wz-search { width: 200px; flex-shrink: 0; }
.mat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
.pick { position: relative; background: var(--asc-card); border: 1.5px solid var(--asc-border); border-radius: 10px; padding: 12px 14px; cursor: pointer; transition: all .15s ease; }
.pick:hover { border-color: #c9c4e8; }
.pick.on { border-color: var(--asc-primary); background: var(--asc-primary-soft); }
.pick-check { position: absolute; top: 8px; right: 8px; width: 18px; height: 18px; border-radius: 50%; border: 1.5px solid var(--asc-border); background: #fff; font-size: 11px; color: #fff; display: flex; align-items: center; justify-content: center; }
.pick.on .pick-check { background: var(--asc-primary); border-color: var(--asc-primary); }
.pick-name { font-size: 13.5px; font-weight: 500; margin-bottom: 3px; padding-right: 22px; }
.pick-meta { font-size: 12px; color: var(--asc-text-2); }
.wz-empty { font-size: 13px; color: var(--asc-text-2); padding: 18px 0; text-align: center; }
/* 操作条：贴在卡片底部（原来是流内元素，填到表单末尾时按钮已滚出视口） */
.wz-bar { position: sticky; bottom: -22px; z-index: 2; background: var(--asc-card);
  display: flex; align-items: center; gap: 12px; border-top: 1px solid var(--asc-divider);
  padding: 14px 0 22px; margin-top: 6px; }
.wz-count { font-size: 13.5px; }
.wz-hint { font-size: 12.5px; color: #d85a30; }
.wz-note { font-size: 12.5px; color: var(--asc-text-2); }
.wz-ops { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.wz-draft { margin-bottom: 12px; }
/* 起草引导条 */
.wz-guide { display: flex; align-items: center; gap: 14px; padding: 12px 14px; margin-bottom: 16px;
  background: var(--asc-primary-soft); border: 1px solid rgba(124, 92, 252, .25); border-radius: 10px; }
.wg-txt { display: flex; flex-direction: column; gap: 3px; font-size: 13px; }
.wg-txt .fh { line-height: 1.6; }
.wz-group { font-size: 12px; font-weight: 600; color: var(--asc-text-2); letter-spacing: .5px;
  margin: 4px 0 12px; padding-bottom: 6px; border-bottom: 1px dashed var(--asc-divider); }
.frow { margin-bottom: 14px; }
.frow label { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; margin-bottom: 6px; }
.frow label i { color: #d85a30; font-style: normal; }
.fh { font-size: 12px; color: var(--asc-text-2); font-weight: 400; }
.mt6 { margin-top: 6px; }
.fq { margin-bottom: 8px; }
.fmodel { width: 280px; }
.mini-btn { margin-left: auto; border: 1px solid rgba(124, 92, 252, .4); background: var(--asc-primary-soft); color: var(--asc-primary); font-size: 11.5px; padding: 2px 9px; border-radius: 999px; cursor: pointer; font-family: inherit; font-weight: 400; }
.mini-btn + .mini-btn { margin-left: 6px; }
.mini-btn.undo { border-color: var(--asc-border); background: var(--asc-surface-2); color: var(--asc-text-2); }
.mini-btn:hover { background: rgba(124, 92, 252, .2); }
.mini-btn:disabled { opacity: .5; cursor: default; }
.spirit-opts { display: flex; gap: 10px; flex-wrap: wrap; }
.spirit-opt { border: 1.5px solid var(--asc-border); border-radius: 10px; background: #fff; padding: 10px 16px; cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 6px; font-size: 12.5px; color: var(--asc-text-2); transition: all .15s ease; }
.spirit-opt:hover { border-color: #c9c4e8; }
.spirit-opt.on { border-color: var(--asc-primary); background: var(--asc-primary-soft); color: var(--asc-primary); font-weight: 500; }
.sum-head { display: flex; align-items: center; gap: 14px; margin-bottom: 8px; }
.sum-icon { width: 52px; height: 52px; border-radius: 12px; background: var(--asc-primary-soft); display: flex; align-items: center; justify-content: center; }
.sum-name { font-size: 16px; font-weight: 600; }
.sum-sub { font-size: 12px; color: var(--asc-text-2); margin-top: 3px; }
.sum-row { display: flex; gap: 14px; padding: 10px 0; border-bottom: 1px solid var(--asc-divider); font-size: 13.5px; align-items: flex-start; }
.sum-k { width: 76px; flex-shrink: 0; color: var(--asc-text-2); font-size: 13px; }
.sum-v { flex: 1; line-height: 1.7; white-space: pre-wrap; }
.sum-edit { flex-shrink: 0; border: none; background: transparent; color: var(--asc-primary); font-size: 12px; cursor: pointer; padding: 0 2px; font-family: inherit; }
.sum-edit:hover { text-decoration: underline; }
.mat-chip { display: inline-block; background: var(--asc-surface-2); border-radius: 6px; padding: 3px 10px; font-size: 12.5px; color: var(--asc-text-2); margin: 0 6px 6px 0; }

/* 揭炉仪式 */
.ritual-mask { position: fixed; inset: 0; background: rgba(30, 27, 46, .5); z-index: 110; display: flex; align-items: center; justify-content: center; }
.ritual { position: relative; text-align: center; background: var(--asc-card); border-radius: 16px; padding: 34px 48px 30px; min-width: 420px; }
.ritual-x { position: absolute; top: 10px; right: 12px; border: none; background: transparent; color: var(--asc-text-2);
  font-size: 18px; cursor: pointer; line-height: 1; padding: 2px 6px; border-radius: 6px; }
.ritual-x:hover { color: var(--asc-text); background: var(--asc-surface-2); }
.ritual-stage { position: relative; height: 230px; }
.ritual-glow { position: absolute; left: 50%; top: 56px; width: 170px; height: 170px; margin-left: -85px; border-radius: 50%; background: rgba(124, 92, 252, .14); }
.ritual-spirit { position: absolute; left: 50%; top: 40px; margin-left: -40px; width: 80px; }
.furnace-wrap { position: absolute; left: 0; right: 0; bottom: 0; display: flex; justify-content: center; cursor: default; }
.furnace-wrap.can-open { cursor: pointer; }
.furnace-wrap.can-open :deep(.furnace) { animation: shakeHint .7s ease-in-out infinite; }
@keyframes shakeHint { 0%, 100% { transform: translateX(0); } 25% { transform: translateX(-2px); } 75% { transform: translateX(2px); } }
.brew-stage { font-size: 13px; color: var(--asc-text-2); height: 20px; letter-spacing: 1px; margin-bottom: 6px; }
.ritual-hint { display: inline-block; padding: 6px 18px; border-radius: 999px; background: var(--asc-primary); color: #fff; font-size: 13px; opacity: 0; transition: opacity .3s; margin-bottom: 10px; }
.ritual-hint.vis { opacity: 1; animation: hintPulse 1.4s ease-in-out infinite; }
@keyframes hintPulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.07); } }
.ritual-ops { display: flex; justify-content: center; align-items: center; gap: 10px; }
.rv { opacity: 0; }
.play :deep(.furnace-lid) { animation: lidOpen 1s .15s ease forwards; transform-origin: 100px 58px; }
.play :deep(.furnace-fire) { animation: fireReveal .7s .55s ease forwards; }
.play .ritual-glow { animation: fadeIn .8s .9s forwards; }
.play .ritual-spirit { animation: riseUp .9s .85s cubic-bezier(.2, .8, .3, 1.25) forwards, bob 3.2s 1.9s ease-in-out infinite; }
.play .ritual-text { animation: fadeUp .5s 1.5s forwards; }
.play .ritual-name { animation: fadeUp .5s 1.75s forwards; }
.play .ritual-sub { animation: fadeUp .5s 1.95s forwards; }
.play .ritual-btn { animation: fadeUp .5s 2.25s forwards; }
.ritual-text { font-size: 26px; font-weight: 600; color: var(--asc-primary); letter-spacing: 2px; }
.ritual-name { font-size: 17px; font-weight: 600; margin-top: 8px; }
.ritual-sub { font-size: 13px; color: var(--asc-text-2); margin: 8px 0 18px; }
.ritual-btn { min-width: 140px; }
@keyframes lidOpen { 0% { transform: translate(0, 0) rotate(0); } 20% { transform: translate(0, 2px) rotate(-2deg); } 34% { transform: translate(0, -2px) rotate(2deg); } 46% { transform: translate(0, 1px) rotate(-1deg); } 100% { transform: translate(-12px, -40px) rotate(-14deg); } }
@keyframes riseUp { from { opacity: 0; transform: translateY(64px) scale(.6); } to { opacity: 1; transform: translateY(-4px) scale(1); } }
@keyframes bob { 0%, 100% { transform: translateY(-4px); } 50% { transform: translateY(-12px); } }
@keyframes fadeUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes fireReveal { from { opacity: 0; } to { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .spirit-breathe :deep(svg), .furnace-wrap.can-open :deep(.furnace), .ritual-hint.vis { animation: none; }
  .play .rv { opacity: 1; transform: none; animation: none; }
  .play :deep(.furnace-lid) { animation: none; transform: translate(-12px, -40px) rotate(-14deg); }
  .play :deep(.furnace-fire) { animation: none; opacity: 1; }
}
</style>
