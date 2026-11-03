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

      <!-- 页级 tab：每屏单一任务。⚠️ 探针锚定 .fe-tab 与「默认落资料绑定」，改名/改默认要同步探针。
           tab 上的琥珀数字 = 该 tab 内未保存的字段数（哪些 tab 有脏字段一眼可见）。 -->
      <div class="fe-tabs" role="tablist" aria-label="应用配置分区">
        <button v-for="t in TABS" :key="t.id" type="button" role="tab"
          class="fe-tab" :class="{ on: activeTab === t.id }"
          :aria-selected="activeTab === t.id"
          @click="switchTab(t.id)">
          {{ t.label }}
          <span v-if="tabDirty[t.id]" class="fe-tab-dot" :title="`${tabDirty[t.id]} 项未保存`">{{ tabDirty[t.id] }}</span>
        </button>
      </div>

      <!-- ============ tab 2 · 资料绑定（「这个应用能答什么」的根） ============ -->
      <div v-if="activeTab === 'materials'" class="fe-pane" role="tabpanel">
        <div class="mat-grid">
          <!-- 当前状态（主卡）：这个应用读什么 -->
          <div class="card mat-card">
            <!-- 卡片「主人位」：丹灵独立成列陪在标题旁（此前叠在标题行末尾 → 三面留白，像贴纸；
                 位图是 600×825 竖幅，故用 flex 让文字列让出宽度，加宽才不会被行高挤压） -->
            <div class="mat-head">
              <div class="mat-head-txt">
                <div class="ctitle">
                  已绑定资料
                  <span class="mat-count">{{ boundMaterials.length }} 份</span>
                  <span v-if="materialsDirty" class="unsaved-tag">未保存</span>
                </div>
                <p class="mat-sub fh">这个应用只能从这些资料里找答案，<span class="nw">可增可减</span>；最后一项（资料+笔记合计）不可移除。</p>
              </div>
              <!-- 投喂反馈（P0）：资料/笔记入列时它开心 800ms -->
              <img class="mat-feed-spirit" :src="spiritImg(form.spirit, feedMood)" alt="" />
            </div>
            <div class="mb-row">
              <span v-for="m in boundMaterials" :key="m.id" class="mat-chip mb">
                {{ m.title }}
                <button class="mb-x" :disabled="boundTotal <= 1"
                  :title="boundTotal <= 1 ? '最后一项绑定不可移除' : '移除'"
                  @click="unbind(m.id)">×</button>
              </span>
              <span v-if="!boundMaterials.length" class="fh">（未绑定资料）</span>
            </div>
            <!-- 已绑定笔记（2026-10-30 笔记绑定）：挂在已绑资料下的笔记随资料自动入语料，不在此列 -->
            <div class="ctitle ctitle-sub">
              已绑定笔记
              <span class="mat-count">{{ boundNotes.length }} 条</span>
            </div>
            <p class="mat-sub fh">材料无关笔记只有显式绑定才会进语料；随资料纳入的笔记无需在此添加。</p>
            <div class="mb-row">
              <span v-for="n in boundNotes" :key="n.id" class="mat-chip mb note-chip">
                {{ n.title }}
                <button class="mb-x" :disabled="boundTotal <= 1"
                  :title="boundTotal <= 1 ? '最后一项绑定不可移除' : '移除'"
                  @click="unbindNote(n.id)">×</button>
              </span>
              <span v-if="!boundNotes.length" class="fh">（未单独绑定笔记）</span>
            </div>
          </div>

          <!-- 流向提示：点右边 → 进左边（纯装饰，窄屏隐藏） -->
          <div class="mat-flow" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M19 12H5"/><path d="m11 18-6-6 6-6"/>
            </svg>
          </div>

          <!-- 追加池（次级卡）：还能加什么 -->
          <div class="card mat-card mat-pool">
            <div class="ctitle">
              材料库
              <span class="mat-count">{{ addableMaterials.length }} 份可绑</span>
            </div>
            <p class="mat-sub fh">点资料即加入左侧绑定列表，保存后才生效。</p>
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
            <div class="ctitle ctitle-sub">
              笔记库
              <span class="mat-count">{{ addableNotes.length }} 条可绑</span>
            </div>
            <p class="mat-sub fh">随已绑资料自动纳入的笔记已置灰；点其余笔记即加入绑定。</p>
            <el-input v-if="allNotes.length > 6" v-model="noteQuery" size="small" clearable
              class="mb-search" placeholder="搜索笔记标题…" />
            <div class="mb-row">
              <span v-for="n in visibleAddableNotes" :key="n.id" class="mat-chip add note-chip"
                :class="{ auto: noteCovered(n) }"
                :title="noteCovered(n) ? '该笔记挂在已绑资料下，已自动纳入语料' : ''"
                @click="bindNote(n)">＋ {{ n.title }}</span>
              <span v-if="!noteQuery && filteredAddableNotes.length > POOL_SHOW" class="mat-chip add pool-toggle" @click="notePoolOpen = !notePoolOpen">
                {{ notePoolOpen ? '收起 ▴' : `展开其余 ${filteredAddableNotes.length - POOL_SHOW} 条 ▾` }}</span>
              <span v-if="!filteredAddableNotes.length" class="fh">
                {{ noteQuery ? '没有匹配的笔记' : '暂无可绑笔记' }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- ============ tab 1 · 人格 · 形象 · 口径 + 试聊预览（默认落点，左表单右试聊） ============ -->
      <div v-else-if="activeTab === 'persona'" class="fe-grid" role="tabpanel">
        <div class="fe-left">
          <div class="card">
            <div class="ctitle">人格 · 形象 · 口径</div>

            <div class="fsec">形象与名称</div>
            <div class="frow">
              <label>形象 <span class="fh">选气质：头饰与配色随气质绑定</span></label>
              <div class="spirit-stage">
                <!-- 主舞台：当前形象（搪胶位图，情绪随试聊联动） -->
                <div class="spirit-hero" :style="heroBg">
                  <img class="spirit-hero-img spirit-breathe" :src="heroSrc" alt="当前形象" />
                  <span class="spirit-shadow" aria-hidden="true"></span>
                  <div class="spirit-hero-name">{{ curSpiritMeta.label }}</div>
                  <div class="spirit-hero-blurb">{{ curSpiritMeta.blurb }}</div>
                  <div class="fh spirit-hero-hint">改完点保存才生效</div>
                </div>
                <div class="spirit-opts">
                  <div v-for="sp in SPIRIT_OPTIONS" :key="sp.id" class="spirit-opt" :class="{ on: curDeco === sp.id }" @click="pickDeco(sp.id)">
                    <img class="spirit-opt-img" :src="spiritImg('g:' + sp.id + ':0')" :alt="sp.label" />
                    <span class="spirit-opt-tx"><b>{{ sp.label }}</b><i>{{ sp.blurb }}</i></span>
                  </div>
                </div>
              </div>
            </div>
            <div class="frow">
              <label>应用名称 <i>*</i></label>
              <el-input v-model="form.name" maxlength="64" />
            </div>

            <div class="fsec">人格口径</div>
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

            <div class="fsec">会话行为</div>
            <div class="frow cfg-row">
              <span>追问建议 <span class="fh">回答后自动给出 3 个可追问问题</span></span>
              <el-switch v-model="form.suggest_on" />
            </div>
            <div class="frow cfg-row">
              <span>资料外问题 <span class="fh">开：资料未命中时可动用通用知识回答（会先声明不在资料范围内）；关：按未命中口径拒答</span></span>
              <el-switch v-model="form.allow_out_of_scope" />
            </div>
            <div class="frow cfg-row" v-if="models.length">
              <span>问答模型 <span class="fh">该应用的默认模型</span></span>
              <el-select v-model="form.model_id" style="width: 240px">
                <!-- 哨兵 default = 跟随全局（空串会被 EP 视为未选、显示成 placeholder）；保存/试聊时映射回 '' -->
                <el-option :label="globalModelLabel ? `跟随全局默认（${globalModelLabel}）` : '跟随全局默认'" value="default" />
                <el-option v-for="m in models" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
              </el-select>
            </div>
            <div class="save-bar">
              <span v-if="dirty" class="fh save-hint">{{ dirtyLabel }}</span>
              <el-button type="primary" :loading="saving" @click="save">保存</el-button>
            </div>
          </div>

          <!-- 人格版本：每次实质修改自动留档，可回滚（归在人格 tab：它是人格的快照历史） -->
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
          <div class="fnote">预览不落库、不建会话；{{ form.allow_out_of_scope ? '书外问题会动用通用知识回答，并先声明不在资料范围内' : '书外问题应按「未命中口径」拒答' }}。可接着追问验证多轮口径。</div>
        </div>
      </div>

      <!-- ============ tab 3 · 数据看板（通栏：指标卡 + 近 7 天趋势） ============ -->
      <div v-else class="fe-pane" role="tabpanel">
        <div v-if="stats" class="card stats-card">
          <div class="ctitle">数据看板 <span class="fh">只统计应用内会话，与主问答互不混</span></div>
          <div class="stats-row">
            <div class="stat"><div class="stat-n">{{ stats.session_count }}</div><div class="stat-l">已聊场数</div></div>
            <div class="stat"><div class="stat-n">{{ stats.question_count }}</div><div class="stat-l">总提问</div></div>
            <div class="stat"><div class="stat-n">{{ stats.hit_rate === null ? '—' : Math.round(stats.hit_rate * 100) + '%' }}</div><div class="stat-l" :title="`只统计真正给出回答的助手消息；报错/中断 ${stats.failed_count || 0} 条不计入分母`">资料命中率</div></div>
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
        <!-- C3 miss 清单：命中率低时要能看到「是哪几道题没答上来」，补料才有方向 -->
        <div v-if="stats && (stats.miss_questions || []).length" class="card miss-card">
          <div class="ctitle">最近没答上来的问题
            <span class="fh">这些问题在绑定资料里没找到答案 —— 补料后回来再问一次，命中率会回升</span>
          </div>
          <div class="miss-list">
            <div v-for="(mq, i) in stats.miss_questions" :key="i" class="miss-row">
              <span class="miss-q" :title="mq.q">{{ mq.q || '（未找到对应提问）' }}<span v-if="mq.count > 1" class="miss-n" title="同一道题没答上来的次数">×{{ mq.count }}</span></span>
              <span class="miss-at">{{ (mq.at || '').slice(5, 16).replace('T', ' ') }}</span>
              <button type="button" class="miss-ask" title="带着这道题去对话页再问一次" @click="reask(mq)">再问一次</button>
            </div>
          </div>
          <div class="miss-ops">
            <el-button type="primary" plain size="small" @click="switchTab('materials')">去补料</el-button>
          </div>
        </div>
        <div v-else class="card stats-empty">
          <div class="fail-t">看板数据暂时没取到</div>
          <p class="fail-s">不影响配置与保存，可稍后重试。</p>
          <el-button size="small" @click="loadStats">重试</el-button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { appsApi, materialApi, settingsApi, kbApi } from '../api'
import { streamSSE } from '../utils/sse'
import { SPIRIT_OPTIONS, spiritImg, spiritTheme, parseSpirit } from '../utils/spirit'
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
const allNotes = ref([])           // 知识库全部笔记（含材料无关；随资料纳入的置灰）
const models = ref([])
const globalChatModelId = ref('')   // 管理中心「对话模型」引用：用于把「跟随全局默认」解析成当前生效的具体模型名
// 全局对话模型名：跟随全局时展示当前生效的是谁（每次进页重拉设置，管理中心改了就变）
const globalModelLabel = computed(() => {
  const m = models.value.find(x => x.id === globalChatModelId.value)
  return m ? `${m.name}（${m.model}）` : ''
})
const form = reactive({ name: '', spirit: 'book', persona: '', boundaries: '', miss_text: '', welcome: '', questions: ['', '', ''], suggest_on: true, allow_out_of_scope: false, model_id: 'default' })
const curDeco = computed(() => parseSpirit(form.spirit).deco)
// 形象舞台：当前角色元信息 / 光晕背景（气质色绑装饰，spiritTheme 口径）
// （⚠️ 必须声明在 curDeco 之后，TDZ 整页白屏）
const curSpiritMeta = computed(() => SPIRIT_OPTIONS.find(o => o.id === curDeco.value) || SPIRIT_OPTIONS[0])
const heroBg = computed(() => {
  const t = spiritTheme(form.spirit)
  return { background: `radial-gradient(130% 95% at 50% 26%, ${t.body} 0%, #ffffff 76%)` }
})

// ---------------- 页级 tab ----------------
// ⚠️ 探针锚定：TABS 顺序（默认落第一个 = persona）、.fe-tab 类名、面板用 v-if 互斥（非 v-show —
//    v-show 会让隐藏面板的 textarea/input 仍能被选择器命中，fill 上去超时才报错，像回归）。
const TABS = [
  { id: 'persona', label: '人格 · 形象 · 口径' },
  { id: 'materials', label: '资料绑定' },
  { id: 'stats', label: '数据看板' },
]
const activeTab = ref('persona')
function switchTab(id) {
  if (activeTab.value === id) return
  activeTab.value = id
  // 深链：?tab= 可直达（与全站 ?tab=&auto=1 约定一致）；同路由 replace 不触发离开守卫
  router.replace({ query: { ...route.query, tab: id } })
}

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
    allow_out_of_scope: form.allow_out_of_scope,
    model_id: form.model_id,
    materials: [...boundIds.value].sort((a, b) => a - b).join(','),
    notes: [...boundNoteIds.value].sort((a, b) => a - b).join(','),
  }
}
const FIELD_LABEL = {
  name: '名称', spirit: '形象', persona: '人格', boundaries: '回答边界',
  miss_text: '未命中口径', welcome: '开场白', q1: '建议问题', q2: '建议问题',
  q3: '建议问题', suggest_on: '追问建议', allow_out_of_scope: '资料外问题', model_id: '问答模型', materials: '资料绑定', notes: '笔记绑定',
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
const materialsDirty = computed(() => dirtyKeys.value.includes('materials') || dirtyKeys.value.includes('notes'))

// tab 脏字段徽标：哪些分区有未保存改动一眼可见（⚠️ 必须声明在 dirtyKeys 之后，setup 的
// computed getter 注册即同步跑一次，读到的 ref 声明在后 = TDZ 整页白屏）
const TAB_FIELDS = {
  materials: ['materials', 'notes'],
  persona: ['name', 'spirit', 'persona', 'boundaries', 'miss_text', 'welcome', 'q1', 'q2', 'q3', 'suggest_on', 'allow_out_of_scope', 'model_id'],
  stats: [],
}
const tabDirty = computed(() => {
  const m = {}
  for (const id of Object.keys(TAB_FIELDS)) {
    m[id] = dirtyKeys.value.filter(k => TAB_FIELDS[id].includes(k)).length
  }
  return m
})

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
// ⚠️「换一身配色」2026-10-30 随位图化下线（reroll 已删）：气质色由装饰绑定，用户只选气质。
function pickDeco(deco) {
  // 只换装饰、保留令牌 palette 位（位废弃但保持令牌合法）：直接构造令牌（后端校验过 regex）
  form.spirit = `g:${deco}:${parseSpirit(form.spirit).pi}`
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

// 投喂反馈（P0）：资料/笔记入列时，已绑定卡的丹灵开心 800ms —— 把「喂资料」和养成心智连起来
const feedMood = ref('normal')
let feedTimer = null
function feedSpirit() {
  feedMood.value = 'happy'
  clearTimeout(feedTimer)
  feedTimer = setTimeout(() => { feedMood.value = 'normal' }, 800)
}

function bind(id) { const s = new Set(boundIds.value); s.add(id); boundIds.value = s; feedSpirit() }
function unbind(id) {
  if (boundTotal.value <= 1) { ElMessage.warning('最后一项绑定不可移除 —— 减到 0 等于拆掉知识边界'); return }
  const s = new Set(boundIds.value); s.delete(id); boundIds.value = s
}

// ---- 笔记绑定（2026-10-30）：挂在已绑资料下的笔记随资料自动入语料 → 置灰；材料无关笔记只能显式绑 ----
const boundNoteIds = ref(new Set())
const noteQuery = ref('')
const notePoolOpen = ref(false)
const boundNotes = computed(() => allNotes.value.filter(n => boundNoteIds.value.has(n.id)))
const boundTotal = computed(() => boundIds.value.size + boundNoteIds.value.size)
function noteCovered(n) { return !!n.material_id && boundIds.value.has(n.material_id) }
const addableNotes = computed(() => allNotes.value.filter(n => !boundNoteIds.value.has(n.id)))
const filteredAddableNotes = computed(() => {
  const kw = noteQuery.value.trim().toLowerCase()
  const list = kw ? addableNotes.value.filter(n => (n.title || '').toLowerCase().includes(kw)) : addableNotes.value
  // 可绑的在前，随资料纳入（置灰）的沉底
  return [...list].sort((a, b) => (noteCovered(a) ? 1 : 0) - (noteCovered(b) ? 1 : 0))
})
const visibleAddableNotes = computed(() =>
  (notePoolOpen.value || noteQuery.value) ? filteredAddableNotes.value : filteredAddableNotes.value.slice(0, POOL_SHOW))

function bindNote(n) {
  if (noteCovered(n)) return
  const s = new Set(boundNoteIds.value); s.add(n.id); boundNoteIds.value = s
  feedSpirit()
}
function unbindNote(id) {
  if (boundTotal.value <= 1) { ElMessage.warning('最后一项绑定不可移除 —— 减到 0 等于拆掉知识边界'); return }
  const s = new Set(boundNoteIds.value); s.delete(id); boundNoteIds.value = s
}

// ---------------- 看板 / 版本 ----------------
const stats = ref(null)
const versions = ref([])
const restoringId = ref(null)
function trendH(c) {
  const max = Math.max(1, ...(stats.value?.daily || []).map(d => d.count))
  return `${Math.round(6 + (c / max) * 64)}px`
}
async function loadStats() {
  try { const { data } = await appsApi.stats(appId); stats.value = data } catch { /* 看板失败不挡配置 */ }
}
// C3+（2026-10-31 二轮）：miss 行「再问一次」——带着这道题跳回对话页自动发送，
// 补料后立刻能验证「这次答上来了没有」（命中率回升的体感闭环）。
function reask(mq) {
  if (!mq.q) return
  router.push({ path: `/furnace/app/${appId}`, query: { ask: mq.q } })
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
    suggest_on: !!data.suggest_on, allow_out_of_scope: !!data.allow_out_of_scope, model_id: data.model_id || 'default',
  })
  boundIds.value = new Set(data.material_ids || [])
  boundNoteIds.value = new Set(data.note_ids || [])
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
  if (!boundIds.value.size && !boundNoteIds.value.size) { ElMessage.warning('至少绑定 1 份资料或 1 条笔记'); return }
  saving.value = true
  try {
    const { data } = await appsApi.update(appId, {
      ...form,
      model_id: form.model_id === 'default' ? '' : form.model_id,
      questions: form.questions.filter(q => q.trim()),
      material_ids: [...boundIds.value],
      note_ids: [...boundNoteIds.value],
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
  if (!boundIds.value.size && !boundNoteIds.value.size) { ElMessage.warning('请先绑定资料或笔记'); return }
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        `AI 草稿会覆盖当前填写的内容（${dirtyLabel.value}）。继续？`,
        '覆盖当前填写', { confirmButtonText: '覆盖', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
  }
  drafting.value = true
  try {
    const { data } = await appsApi.assist({ kind: 'draft', material_ids: [...boundIds.value], note_ids: [...boundNoteIds.value] })
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

// 舞台卡情绪（2026-10-30「让角色活」）：跟着试聊事件走 ——
// 发送中 think、命中 happy、未命中/报错 miss，4200ms 后回落常态。
// ⚠️ 情绪只挂真实事件，不做无事件轮播（那是噪音不是生命力）。
const heroMood = ref('normal')
let heroMoodTimer = null
function setHeroMood(m, decayMs = 4200) {
  heroMood.value = m
  if (heroMoodTimer) { clearTimeout(heroMoodTimer); heroMoodTimer = null }
  if (m !== 'normal' && decayMs) {
    heroMoodTimer = setTimeout(() => { heroMood.value = 'normal'; heroMoodTimer = null }, decayMs)
  }
}
function heroFromResult(m) { setHeroMood(m.isErr ? 'miss' : (m.kbHit ? 'happy' : 'miss')) }

// 待机眨眼（P1）：常态下每 4.6s 闪切闭眼帧 140ms；情绪非 normal 时不眨（事件优先）；
// prefers-reduced-motion 下不眨。⚠️ 必须声明在 heroMood 之后（TDZ）。
const blinkOn = ref(false)
let blinkIv = null, blinkOff = null
const heroSrc = computed(() => spiritImg(form.spirit, blinkOn.value && heroMood.value === 'normal' ? 'blink' : heroMood.value))
const _reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
function startBlink() {
  blinkIv = setInterval(() => {
    if (_reducedMotion.matches || heroMood.value !== 'normal') return
    blinkOn.value = true
    clearTimeout(blinkOff)
    blinkOff = setTimeout(() => { blinkOn.value = false }, 140)
  }, 4600)
}

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
  setHeroMood('think', 0)          // 生成中：思考表情（无衰减，结果出来再定）
  pvScroll()
  try {
    await streamSSE('/api/apps/preview/stream', {
      name: form.name, persona: form.persona, boundaries: form.boundaries,
      miss_text: form.miss_text, allow_out_of_scope: form.allow_out_of_scope,
      material_ids: [...boundIds.value], note_ids: [...boundNoteIds.value], question: q,
      model_id: form.model_id === 'default' ? '' : form.model_id,
      // 多轮上下文：后端会再裁一次（只认 user/assistant、限 3 轮、总量截断），
      // 这里截断只是为了少传无用载荷。
      history: pvHistory.value.slice(-pvTurns * 2),
    },
      (t) => { aiMsg.text += t; aiMsg.pending = false; pvScroll() },
      () => { aiMsg.pending = false; pvBusy.value = false; remember(q, aiMsg); heroFromResult(aiMsg) },
      (msg) => {
        aiMsg.text = msg || '生成失败'; aiMsg.cls = 'pv-miss'; aiMsg.isErr = true
        aiMsg.pending = false; pvBusy.value = false
        setHeroMood('miss')
      },
      (meta) => { aiMsg.cls = meta.kb_hit ? 'pv-kb' : 'pv-miss'; aiMsg.kbHit = !!meta.kb_hit })
  } catch (e) {
    aiMsg.text = (e && e.message) || '请求失败'
    aiMsg.cls = 'pv-miss'
    aiMsg.isErr = true
    aiMsg.pending = false
    pvBusy.value = false
    setHeroMood('miss')
  }
}
const onPvEnter = enterToSend(pvSend)

onMounted(async () => {
  window.addEventListener('beforeunload', onBeforeUnload)
  startBlink()
  // 深链：?tab=materials|persona|stats（非法值静默落默认）
  const qt = String(route.query.tab || '')
  if (TABS.some(t => t.id === qt)) activeTab.value = qt
  load()
  try {
    const { data } = await materialApi.list()
    allReady.value = data.filter(m => m.parsed_status === 'success')
  } catch (e) { /* 追加区为空即可 */ }
  try {
    const { data } = await kbApi.overview()
    allNotes.value = data.notes || []
  } catch (e) { /* 笔记区为空即可，不挡配置主流程 */ }
  try {
    const { data } = await settingsApi.get()
    models.value = data.llm_models || []
    globalChatModelId.value = data.chat_model_id || ''
  } catch (e) { /* 隐藏模型行 */ }
})
onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
  if (heroMoodTimer) clearTimeout(heroMoodTimer)
  clearTimeout(feedTimer)
  clearInterval(blinkIv)
  clearTimeout(blinkOff)
})
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

/* 页级 tab：下划线式（页级主导航范式；分段控件是栏内二级导航用的，不混用） */
.fe-tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--asc-divider); margin-bottom: 16px; }
.fe-tab { position: relative; border: none; background: transparent; font-family: inherit;
  font-size: 13.5px; font-weight: 500; color: var(--asc-text-2);
  padding: 9px 14px 11px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
  border-radius: 8px 8px 0 0; transition: color .16s ease, background .16s ease; }
.fe-tab:hover { color: var(--asc-text); background: var(--asc-surface-2); }
.fe-tab.on { color: var(--asc-primary); font-weight: 600; }
.fe-tab.on::after { content: ''; position: absolute; left: 10px; right: 10px; bottom: -1px;
  height: 2px; border-radius: 2px; background: var(--asc-primary); }
.fe-tab:focus-visible { outline: 2px solid var(--asc-primary); outline-offset: -2px; }
.fe-tab-dot { min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px;
  background: #fdf6ec; color: #b45309; font-size: 10.5px; font-weight: 600;
  display: inline-flex; align-items: center; justify-content: center;
  font-variant-numeric: tabular-nums; }

.fe-pane { min-height: 200px; }
.fe-grid { display: grid; grid-template-columns: 1fr 340px; gap: 14px; align-items: start; }
.card { background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: var(--asc-radius); padding: 18px 20px; margin-bottom: 14px; }
.ctitle { font-size: 14px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
/* 卡片内第二分区（笔记区，2026-10-30 笔记绑定）：与卡顶拉开距离、字号略收一档 */
.ctitle-sub { margin-top: 16px; font-size: 13px; }
/* 随已绑资料自动纳入的笔记：置灰禁点（避免重复绑定困惑），hover 不再高亮 */
.mat-chip.add.auto { opacity: .5; cursor: not-allowed; }
.mat-chip.add.auto:hover { border-color: var(--asc-border); color: var(--asc-text-2); background: transparent; }
.fh { font-size: 12px; color: var(--asc-text-2); font-weight: 400; }
.unsaved-tag { font-size: 11px; font-weight: 500; color: #b45309; background: #fdf6ec; border-radius: 4px; padding: 1px 6px; }
.fnote { font-size: 12px; color: var(--asc-text-2); margin-top: 10px; line-height: 1.6; }

/* 资料绑定：左=当前状态（白底主卡），中=流向提示，右=追加池（灰底次级卡， recessed 表「仓库」） */
.mat-grid { display: grid; grid-template-columns: minmax(0, 1fr) 28px minmax(0, 1fr); gap: 14px; align-items: start; }
.mat-flow { display: flex; align-items: center; justify-content: center; align-self: stretch; color: var(--asc-text-2); opacity: .7; }
.mat-pool { background: var(--asc-surface-2); border-color: transparent; }
/* 投喂小丹灵：⚠️ 位图原始 600×825（竖幅，透明底），必须显式限宽（漏写样式 = 渲染原尺寸）。
   52px→72px（2026-11-02 三次反馈后改法：独立成列 + 卡片主人位，不再是标题行末尾的小贴纸）。
   ⚠️ 加宽时行高按 1.375 倍涨（72 × 825/600 ≈ 99px），故必须 flex 让文字列收窄，不能只改 width。 */
.mat-head { display: flex; align-items: center; gap: 14px; }
.mat-head-txt { flex: 1; min-width: 0; }
/* 文字列被丹灵收窄后，副标题会在末尾甩出「除。」孤字 → balance 让两行等长（Chrome 114+）；
   再配 .nw 锁住「可增可减」，否则 balance 会从词中间断成「可增可／减」 */
.mat-head-txt .mat-sub { text-wrap: balance; }
.nw { white-space: nowrap; }
.mat-feed-spirit { width: 72px; flex: none; display: block; }
.mat-count { font-size: 11.5px; font-weight: 500; color: var(--asc-primary);
  background: var(--asc-primary-soft); border-radius: 999px; padding: 1px 9px;
  font-variant-numeric: tabular-nums; }
.mat-sub { margin: -6px 0 12px; }
.mb-search { width: 100%; margin-bottom: 10px; }
.mb-row { display: flex; flex-wrap: wrap; gap: 8px; }
.mat-chip { display: inline-flex; align-items: center; gap: 6px; border-radius: 7px; padding: 5px 12px; font-size: 13px; line-height: 1.5; color: var(--asc-text-2); }
.mat-chip.mb { background: #fff; border: 1px solid var(--asc-border); color: var(--asc-text); padding-right: 8px; }
.mb-x { border: none; background: transparent; color: var(--asc-text-2); cursor: pointer; font-size: 14px; padding: 2px 4px; border-radius: 4px; line-height: 1; }
.mb-x:hover:not(:disabled) { color: #d85a30; background: #fdf0ea; }
.mb-x:disabled { opacity: .35; cursor: not-allowed; }
.mat-chip.add { border: 1px dashed var(--asc-border); background: transparent; cursor: pointer; transition: border-color .15s ease, color .15s ease, background .15s ease; }
.mat-chip.add:hover { border-color: var(--asc-primary); color: var(--asc-primary); background: var(--asc-primary-soft); }
.pool-toggle { font-weight: 500; color: var(--asc-primary); border-color: rgba(124, 92, 252, .4); }

/* 表单分区小标题：长表单靠分组扫读，不靠颜色堆层级 */
.fsec { font-size: 12px; font-weight: 600; color: var(--asc-text-2); letter-spacing: .04em;
  margin: 18px 0 12px; padding-top: 14px; border-top: 1px dashed var(--asc-divider); }
.fsec:first-of-type { margin-top: 2px; padding-top: 0; border-top: none; }
.frow { margin-bottom: 14px; }
.frow label { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; margin-bottom: 6px; }
.frow label i { color: #d85a30; font-style: normal; }
.fq { margin-bottom: 8px; }
.cfg-row { display: flex; justify-content: space-between; align-items: center; font-size: 13.5px; gap: 10px; }
.mini-btn { border: 1px solid rgba(124, 92, 252, .4); background: var(--asc-primary-soft); color: var(--asc-primary); font-size: 11.5px; padding: 2px 9px; border-radius: 999px; cursor: pointer; font-family: inherit; font-weight: 400; }
.frow label .mini-btn { margin-left: auto; }
.mini-btn:hover { background: rgba(124, 92, 252, .2); }
.mini-btn:disabled { opacity: .5; cursor: default; }

/* 数据看板：指标卡网格 + 加高趋势图 */
.stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin-bottom: 16px; }
.stat { background: var(--asc-surface-2); border-radius: 10px; padding: 14px 18px; }
.stat-n { font-size: 26px; font-weight: 600; color: var(--asc-text); font-variant-numeric: tabular-nums; }
.stat-n.stat-sm { font-size: 15px; padding-top: 9px; }
.stat-l { font-size: 12px; color: var(--asc-text-2); margin-top: 3px; }
.trend { display: flex; align-items: flex-end; gap: 8px; padding: 16px 4px 4px; border-top: 1px dashed var(--asc-border); }
.trend-col { display: flex; flex-direction: column; align-items: center; gap: 5px; }
.trend-bar { width: 26px; border-radius: 4px 4px 0 0; background: var(--asc-primary-soft); border: 1px solid rgba(124, 92, 252, .35); border-bottom: none; }
.trend-d { font-size: 10.5px; color: var(--asc-text-2); font-variant-numeric: tabular-nums; }
.trend-cap { margin-left: auto; align-self: center; }
.stats-empty { text-align: center; padding: 48px 20px; }
.stats-empty .el-button { margin-top: 4px; }
/* C3 miss 清单（补料循环）：问题可点读全文（title），时间右对齐弱化 */
.miss-list { display: flex; flex-direction: column; }
.miss-row { display: flex; align-items: baseline; gap: 12px; padding: 7px 0; border-bottom: 1px dashed var(--asc-divider); }
.miss-row:last-child { border-bottom: none; }
.miss-q { flex: 1; min-width: 0; font-size: 13px; color: var(--asc-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.miss-n { font-size: 11px; font-weight: 600; color: #b45309; background: #fdf6ec;
  border-radius: 4px; padding: 0 5px; margin-left: 6px; }
.miss-at { flex-shrink: 0; font-size: 11.5px; color: var(--asc-text-2); font-variant-numeric: tabular-nums; }
.miss-ask { flex-shrink: 0; border: 1px solid rgba(124, 92, 252, .4); background: transparent;
  color: var(--asc-primary); font-size: 11.5px; padding: 2px 10px; border-radius: 999px;
  cursor: pointer; font-family: inherit; transition: all .15s ease; }
.miss-ask:hover { background: var(--asc-primary-soft); }
.miss-ops { margin-top: 12px; display: flex; justify-content: flex-end; }

/* 人格版本 */
.ver-list { display: flex; flex-direction: column; gap: 8px; }
.ver-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border: 1px solid var(--asc-border); border-radius: 10px; }
.ver-tag { flex-shrink: 0; font-size: 11px; font-weight: 600; color: var(--asc-primary); background: var(--asc-primary-soft); border-radius: 4px; padding: 2px 7px; }
.ver-body { flex: 1; min-width: 0; }
.ver-head { font-size: 12.5px; font-weight: 500; }
.ver-head .fh { margin-left: 6px; }
.ver-persona { font-size: 12px; color: var(--asc-text-2); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 形象：左舞台拟人卡 + 右 2×3 气质选项（位图版） */
.spirit-stage { display: grid; grid-template-columns: 224px 1fr; gap: 14px; align-items: stretch; }
.spirit-hero { border: 1px solid var(--asc-border); border-radius: 12px; padding: 16px 14px 13px;
  display: flex; flex-direction: column; align-items: center; text-align: center; }
.spirit-hero-img { width: 96px; display: block; }
/* 影子跟呼吸同频：身体放大时影子略宽略淡 → 「站着」而不是「飘着」（P0） */
.spirit-shadow { width: 62px; height: 9px; border-radius: 50%; margin-top: -5px;
  background: radial-gradient(closest-side, rgba(58, 54, 48, .22), transparent);
  animation: shadow-breathe 3.2s ease-in-out infinite; }
@keyframes shadow-breathe { 0%, 100% { transform: scaleX(1); opacity: 1; } 50% { transform: scaleX(1.08); opacity: .82; } }
.spirit-hero-name { font-size: 14.5px; font-weight: 600; margin-top: 7px; }
.spirit-hero-blurb { font-size: 11.5px; color: var(--asc-text-2); line-height: 1.55; margin-top: 3px; min-height: 36px; }
.spirit-hero-hint { margin-top: 8px; }
.spirit-opts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-content: stretch; }
.spirit-breathe { animation: breathe 3.2s ease-in-out infinite; transform-origin: 50% 88%; }
@keyframes breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.035); } }
@media (prefers-reduced-motion: reduce) {
  .spirit-breathe, .spirit-shadow { animation: none; }
}
.spirit-opt { border: 1.5px solid var(--asc-border); border-radius: 10px; background: #fff;
  padding: 9px 12px; cursor: pointer; display: flex; align-items: center; gap: 10px;
  text-align: left; transition: all .15s ease; }
.spirit-opt:hover { border-color: #c9c4e8; }
.spirit-opt.on { border-color: var(--asc-primary); background: var(--asc-primary-soft); }
.spirit-opt-img { width: 40px; display: block; flex-shrink: 0; transition: transform .18s ease; }
/* hover 歪头（P0）：选项无 breathe 动画，直接作用在 img 上 */
.spirit-opt:hover .spirit-opt-img { transform: rotate(3deg) translateY(-2px); }
.spirit-opt-tx { min-width: 0; }
.spirit-opt-tx b { display: block; font-size: 12.5px; font-weight: 500; color: var(--asc-text); }
.spirit-opt.on .spirit-opt-tx b { color: var(--asc-primary); }
.spirit-opt-tx i { display: block; font-style: normal; font-size: 11px; color: var(--asc-text-2); margin-top: 1px; line-height: 1.45; }
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
@media (max-width: 1100px) {
  .fe-grid { grid-template-columns: 1fr; }
  .pv-card { position: static; }
  .mat-grid { grid-template-columns: 1fr; }
  .mat-flow { display: none; }
  .spirit-stage { grid-template-columns: 1fr; }
}
</style>
