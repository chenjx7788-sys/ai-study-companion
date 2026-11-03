<template>
  <div class="page furnace-page">
    <!-- 页头与全站 PageHead 同规格（2026-11-02 加图标）：图标沿用侧栏炼丹炉烧瓶 -->
    <PageHead title="炼丹炉" sub="把任意资料炼成独立的 AI 应用 —— 专属人格、知识边界、独立会话，为 IP 孵化而生。">
      <template #icon>
        <svg width="22" height="22" viewBox="0 0 16 16" fill="none"><path d="M6.2 1.8h3.6M7.1 1.8v3.9L3.3 12a1.9 1.9 0 0 0 1.7 2.8h6a1.9 1.9 0 0 0 1.7-2.8L8.9 5.7V1.8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/><circle cx="6.4" cy="11" r=".8" fill="currentColor"/><circle cx="9.4" cy="12.4" r=".6" fill="currentColor"/></svg>
      </template>
      <el-button type="primary" @click="openWizard">＋ 开炉炼应用</el-button>
    </PageHead>

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
            <div class="ac-spirit"><img class="ac-spirit-img spirit-breathe" :src="spiritImg(a.spirit)" alt="" /></div>
            <div class="ac-name">{{ a.name }}</div>
          </div>
          <div class="ac-body">
            <p class="ac-persona">{{ (a.persona_head || '').slice(0, 46) }}<template v-if="(a.persona_head || '').length >= 300">…</template></p>
            <div class="ac-meta"><span class="ac-bond" :title="bondTip(a)">{{ a.bond?.label || '初识' }}</span>绑定 {{ a.material_count }} 份资料<template v-if="a.note_count"> · {{ a.note_count }} 条笔记</template> · 已聊 {{ a.session_count }} 场<template v-if="activeLabel(a)"> · {{ activeLabel(a) }}</template></div>
            <div class="ac-ops">
              <el-button v-if="a.status === 'draft'" type="primary" class="ac-go"
                @click="$router.push(`/furnace/app/${a.id}/edit`)">补料后复活</el-button>
              <el-button v-else type="primary" class="ac-go" @click="$router.push(`/furnace/app/${a.id}`)">进入对话</el-button>
              <el-button v-if="a.status !== 'draft'" text @click="$router.push(`/furnace/app/${a.id}/edit`)">配置</el-button>
              <el-button v-else text @click="$router.push(`/furnace/app/${a.id}`)">进入对话</el-button>
              <el-button text title="生成应用名片" @click="openShareCard(a)">名片</el-button>
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

        <!-- ---------- ① 投料（2026-11-02 改双栏：资料/笔记各成一栏、栏内自滚，不再长页翻找） ---------- -->
        <div v-if="wiz.step === 1">
          <p class="wz-sub">选择要炼入的资料与笔记（合计至少 1 项）。这个应用的一切回答只来自选中的内容。</p>
          <div class="wz-cols">
            <section class="wz-col">
              <div class="wz-col-head">
                资料 <span class="wz-col-count">{{ readyMaterials.length }}</span>
                <button v-if="wiz.sel.size" type="button" class="wz-sel-chip" :class="{ on: matOnlySel }"
                  @click="matOnlySel = !matOnlySel">只看已选 {{ wiz.sel.size }}</button>
                <el-input v-if="readyMaterials.length > 6" v-model="matQuery" size="small" clearable
                  class="wz-search" placeholder="搜索资料标题…" />
              </div>
              <div class="wz-col-body">
                <div v-for="m in visibleMats" :key="m.id" class="pick" :class="{ on: wiz.sel.has(m.id) }" @click="togglePick(m.id)">
                  <div class="pick-check">✓</div>
                  <div class="pick-name">{{ m.title }}</div>
                  <div class="pick-meta">{{ (m.format || '').toUpperCase() }}<template v-if="m.page_count"> · {{ m.page_count }} 页</template></div>
                </div>
                <el-empty v-if="!readyMaterials.length" description="没有已解析完成的资料，请先到材料库导入" :image-size="60" />
                <div v-else-if="!visibleMats.length" class="wz-empty">没有匹配「{{ matQuery }}」的资料</div>
              </div>
            </section>

            <!-- 笔记区：挂在已选资料下的笔记自动随资料入语料 → 置灰标注，不占名额 -->
            <section class="wz-col">
              <div class="wz-col-head">
                笔记 <span class="wz-col-count">{{ allNotes.length }}</span>
                <button v-if="wiz.selNote.size" type="button" class="wz-sel-chip" :class="{ on: noteOnlySel }"
                  @click="noteOnlySel = !noteOnlySel">只看已选 {{ wiz.selNote.size }}</button>
                <el-input v-if="allNotes.length > 6" v-model="noteQuery" size="small" clearable
                  class="wz-search" placeholder="搜索笔记标题…" />
              </div>
              <div class="wz-col-body">
                <p class="wz-col-note">随资料自动纳入的已置灰；材料无关笔记只能在这里显式选入</p>
                <div v-for="n in visibleNotes" :key="n.id" class="pick pick-note"
                  :class="{ on: wiz.selNote.has(n.id), auto: autoCovered(n) }"
                  :title="autoCovered(n) ? '该笔记挂在已选资料下，已自动纳入语料' : ''"
                  @click="toggleNote(n)">
                  <div class="pick-check">✓</div>
                  <div class="pick-name">{{ n.title }}</div>
                  <div class="pick-meta">{{ autoCovered(n) ? '已随资料纳入' : (n.material_title || '材料无关笔记') }}</div>
                </div>
                <div v-if="!allNotes.length" class="wz-empty">暂无笔记</div>
                <div v-else-if="!visibleNotes.length" class="wz-empty">没有匹配「{{ noteQuery }}」的笔记</div>
              </div>
            </section>
          </div>

          <div class="wz-bar">
            <span class="wz-count">已选 <b>{{ wiz.sel.size }}</b> 份资料<template v-if="wiz.selNote.size"> · <b>{{ wiz.selNote.size }}</b> 条笔记</template></span>
            <span v-if="!wiz.sel.size && !wiz.selNote.size" class="wz-hint">至少选择 1 份资料或 1 条笔记才能继续</span>
            <div class="wz-ops">
              <el-button @click="closeWizard()">取消</el-button>
              <el-button type="primary" :disabled="!wiz.sel.size && !wiz.selNote.size" @click="wiz.step = 2">下一步：定型</el-button>
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
                <img class="wiz-sp-img" :src="spiritImg('g:' + sp.id + ':0')" :alt="sp.label" />{{ sp.label }}
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
              <!-- 哨兵 default = 跟随全局（空串会被 EP 视为未选）；创建时映射回 '' -->
              <el-option :label="globalModelLabel ? `跟随全局默认（${globalModelLabel}）` : '跟随全局默认'" value="default" />
              <el-option v-for="m in models" :key="m.id" :label="`${m.name}（${m.model}）`" :value="m.id" />
            </el-select>
          </div>

          <div class="wz-bar">
            <!-- 起草入口同一时刻只留一个（2026-11-02）：未起草 → 顶部引导条；已起草 → 这里保留覆盖重起草 -->
            <el-button v-if="wiz.draftBanner" :loading="wiz.drafting" @click="runDraft">✨ AI 草稿（覆盖当前填写）</el-button>
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
            <div class="sum-icon"><img class="sum-icon-img" :src="spiritImg(rollSpiritPreview(wiz.form.name, 0, wiz.form.spirit))" alt="" /></div>
            <div>
              <div class="sum-name">{{ wiz.form.name }}</div>
              <div class="sum-sub">绑定 {{ wiz.sel.size }} 份资料<template v-if="wiz.selNote.size"> · {{ wiz.selNote.size }} 条笔记</template> · {{ modelLabel }}</div>
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
              <span v-if="!pickedMaterials.length" class="fh">（未选资料）</span>
            </span>
            <button class="sum-edit" @click="wiz.step = 1">改</button>
          </div>
          <div class="sum-row" v-if="pickedNotes.length">
            <span class="sum-k">绑定笔记</span>
            <span class="sum-v">
              <span v-for="n in pickedNotes" :key="n.id" class="mat-chip">{{ n.title }}</span>
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
      <div class="ritual" :class="['brew-s' + ritual.stageIdx, { play: ritual.play, brewing: !ritual.play }]">
        <button class="ritual-x" title="关闭" @click="dismissRitual">×</button>
        <div class="ritual-stage">
          <div class="ritual-glow rv"></div>
          <!-- 炼制期炉光气晕（呼吸感，揭炉即隐） -->
          <div v-if="!ritual.play" class="brew-aura" aria-hidden="true"></div>
          <div class="furnace-wrap" :class="{ 'can-open': ritual.canOpen }" @click="openLid" v-html="ritualFurnace">
          </div>
          <!-- ① 投料入炉：真实资料名做成丹丸落入炉口（炼制第一段可见）。
               ⚠️ 必须在 furnace-wrap 之后（丹丸在炉体前方落下，否则全程被炉身挡住看不见） -->
          <div v-if="!ritual.play && ritual.stageIdx === 0" class="feed-chips" aria-hidden="true">
            <span v-for="(t, i) in ritual.mats" :key="t" class="feed-chip"
              :style="{ '--dx': (['-26px', '0px', '26px', '-13px'][i % 4]), animationDelay: (i * 0.16) + 's' }">{{ t }}</span>
          </div>
          <!-- 炼制期蒸汽（揭炉即散）；叠放次序：炉 → 汽 → 灵 -->
          <div v-if="!ritual.play" class="steam" aria-hidden="true"><i></i><i></i><i></i></div>
          <!-- ③ 丹光爆闪（揭炉瞬间一圈金光炸开） -->
          <div class="burst" aria-hidden="true"></div>
          <!-- ④ 冲霄光柱：揭炉瞬间炉口金光柱冲起（是光不是火），旋即消散 -->
          <div class="beam" aria-hidden="true"></div>
          <div class="ritual-spirit rv"><img class="ritual-spirit-img" :src="ritual.spiritImg" alt="" /></div>
          <!-- ⑤ 星尘：丹灵现身后的金粉环绕（常驻微闪） -->
          <div class="sparkles" aria-hidden="true">
            <i v-for="i in 5" :key="i" :class="'sp' + i"></i>
          </div>
        </div>
        <!-- :key 重挂载触发每次入场动效；进度点给「炼到哪了」的感知 -->
        <div class="brew-stage" :key="ritual.stageText">{{ ritual.stageText }}</div>
        <div class="brew-dots" aria-hidden="true">
          <i v-for="(s, i) in BREW_STAGES" :key="i" :class="{ on: i <= ritual.stageIdx, cur: i === ritual.stageIdx }"></i>
        </div>
        <!-- 炼制期占位下半区（丹成文案区此刻是空的 → 给期待感而不是留白） -->
        <div v-if="!ritual.play" class="brew-expect">丹成后揭晓真身</div>
        <div class="ritual-hint" :class="{ vis: ritual.canOpen }">点击揭炉</div>
        <div class="ritual-text rv">丹成！</div>
        <div class="ritual-name rv">{{ ritual.appName }}</div>
        <div class="ritual-sub rv">人格与知识边界已定型 —— 从现在起，它只为这些资料开口</div>
        <div class="ritual-ops">
          <!-- 2026-10-30：隐藏「跳过动画」——仪式全程仅约 6s 且是核心时刻，不留出口（右上角 × 仍可关） -->
          <el-button class="rv ritual-btn" type="primary" @click="enterApp">进入对话</el-button>
        </div>
      </div>
    </div>

    <!-- ===== 形象名片（P1）：Canvas 合成预览 + 下载 ===== -->
    <el-dialog v-model="shareCard.open" title="应用名片" width="420px" append-to-body>
      <div class="share-card-wrap">
        <img v-if="shareCard.url" :src="shareCard.url" class="share-card-img" alt="应用名片" />
      </div>
      <template #footer>
        <el-button @click="shareCard.open = false">关闭</el-button>
        <el-button type="primary" @click="downloadShareCard">下载 PNG</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { appsApi, materialApi, settingsApi, kbApi } from '../api'
import { SPIRIT_OPTIONS, spiritImg, rollSpiritPreview, spiritTheme, parseSpirit, furnaceSVG } from '../utils/spirit'
import PageHead from '../components/PageHead.vue'

function themeOf(spirit) {
  const t = spiritTheme(spirit)
  return { '--app-body': t.body, '--app-edge': t.edge }
}

const router = useRouter()
const apps = ref([])
const loading = ref(true)
const readyMaterials = ref([])
const allNotes = ref([])           // 知识库全部笔记（含材料无关；随资料纳入的前端置灰）
const models = ref([])
const globalChatModelId = ref('')   // 管理中心「对话模型」引用：用于把「跟随全局默认」解析成当前生效的具体模型名

const emptyForm = () => ({ name: '', spirit: 'flame', persona: '', boundaries: '', miss_text: '', welcome: '', questions: ['', '', ''], model_id: 'default' })   // 形象默认元气丹灵（2026-11-02）
const wiz = reactive({ open: false, step: 1, sel: new Set(), selNote: new Set(), form: emptyForm(), drafting: false, polishing: false, creating: false, draftBanner: false })
const ritual = reactive({ open: false, play: false, canOpen: false, appName: '', spiritImg: '', stageText: '', stageIdx: 0, appId: null, spirit: 'book', mats: [] })
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
const pickedNotes = computed(() => allNotes.value.filter(n => wiz.selNote.has(n.id)))
// 全局对话模型名：跟随全局时展示当前生效的是谁（每次进页重拉设置，管理中心改了就变）
const globalModelLabel = computed(() => {
  const m = models.value.find(x => x.id === globalChatModelId.value)
  return m ? `${m.name}（${m.model}）` : ''
})
const modelLabel = computed(() => {
  if (!wiz.form.model_id || wiz.form.model_id === 'default')
    return globalModelLabel.value ? `跟随全局 · ${globalModelLabel.value}` : '跟随全局默认'
  const m = models.value.find(x => x.id === wiz.form.model_id)
  return m ? m.name : '跟随全局默认'
})

// ---- ① 投料：搜索 + 已选置顶 + 只看已选 ----
const matQuery = ref('')
const noteQuery = ref('')
const matOnlySel = ref(false)      // 只看已选（资料栏）
const noteOnlySel = ref(false)     // 只看已选（笔记栏）
const visibleMats = computed(() => {
  const kw = matQuery.value.trim().toLowerCase()
  let list = kw ? readyMaterials.value.filter(m => (m.title || '').toLowerCase().includes(kw)) : readyMaterials.value
  if (matOnlySel.value && wiz.sel.size) list = list.filter(m => wiz.sel.has(m.id))
  // sort 稳定（ES2019+）：同组内保持材料库原顺序，勾选后只是「跳到前面」
  return [...list].sort((a, b) => (wiz.sel.has(b.id) ? 1 : 0) - (wiz.sel.has(a.id) ? 1 : 0))
})
// 笔记区：挂在已选资料下的笔记自动随资料入语料（向量 metadata 带 material_id）→ 置灰不占名额；
// 材料无关笔记（material_id 空）只能在这里显式选入 —— 这是它们进语料的唯一通道。
function autoCovered(n) { return !!n.material_id && wiz.sel.has(n.material_id) }
const visibleNotes = computed(() => {
  const kw = noteQuery.value.trim().toLowerCase()
  let list = kw ? allNotes.value.filter(n => (n.title || '').toLowerCase().includes(kw)) : allNotes.value
  if (noteOnlySel.value && wiz.selNote.size) list = list.filter(n => wiz.selNote.has(n.id))
  return [...list].sort((a, b) => {
    const d = (autoCovered(a) ? 1 : 0) - (autoCovered(b) ? 1 : 0)   // 可选的在前，置灰的沉底
    if (d) return d
    return (wiz.selNote.has(b.id) ? 1 : 0) - (wiz.selNote.has(a.id) ? 1 : 0)
  })
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

// ---- 列表卡片：默契徽章提示 + 最近活跃（A3，2026-10-31）----
// 后端 last_active 是 datetime.utcnow().isoformat()（无时区标记）：不补 Z 会被当本地时间解析，
// 比真实早 8 小时（与 FurnaceChatView.parseTs 同口径）。
function activeLabel(a) {
  const raw = a?.last_active
  if (!raw) return ''
  const s = String(raw).replace(' ', 'T')
  const d = new Date(/([Zz]|[+-]\d{2}:?\d{2})$/.test(s) ? s : s + 'Z')
  if (isNaN(d.getTime())) return ''
  const days = Math.floor((Date.now() - d.getTime()) / 86400000)
  if (days <= 0) return '今天聊过'
  if (days === 1) return '昨天聊过'
  if (days < 30) return `${days} 天前聊过`
  // 跨年必须带年份：单「12-20」在次年看会误读成今年（2026-10-31 自测登记）
  if (d.getFullYear() !== new Date().getFullYear()) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')} 聊过`
  }
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 聊过`
}
function bondTip(a) {
  const b = a?.bond
  if (!b) return ''
  return `已聊 ${b.sessions} 场 · ${b.questions} 问 · ${b.days} 个活跃日`
}

async function loadMaterials() {
  try {
    const { data } = await materialApi.list()
    readyMaterials.value = data.filter(m => m.parsed_status === 'success')
  } catch (e) { /* 列表失败时选择区为空，el-empty 已兜底 */ }
}

async function loadNotes() {
  try {
    const { data } = await kbApi.overview()
    allNotes.value = data.notes || []
  } catch (e) { /* 笔记加载失败时笔记区隐藏，不挡投料主流程 */ }
}

function hasDraftInput() {
  if (wiz.step > 1 || wiz.sel.size || wiz.selNote.size) return true
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
    wiz.selNote = stash.selNote || new Set()
    wiz.draftBanner = stash.draftBanner
    Object.assign(undoText, stash.undoText)
    stash = null
  } else {
    wiz.step = 1; wiz.sel = new Set(); wiz.selNote = new Set(); wiz.form = emptyForm(); wiz.draftBanner = false
    Object.assign(undoText, { persona: '', bounds: '' })
  }
  matQuery.value = ''
  noteQuery.value = ''
  matOnlySel.value = false
  noteOnlySel.value = false
  // 每次开炉都重取材料列表（2026-10-29 修）：原实现只在「列表为空」时请求一次，
  // 本次会话里新导入的资料因此永远不会出现在投料区，必须刷新页面才可见。
  await loadMaterials()
  await loadNotes()
  // 恢复暂存时，勾选的资料可能已被删除 —— 不清理会让「已选 3 份」实际只绑到 2 份
  if (wiz.sel.size) {
    const alive = new Set(readyMaterials.value.map(m => m.id))
    const kept = new Set([...wiz.sel].filter(id => alive.has(id)))
    if (kept.size !== wiz.sel.size) {
      wiz.sel = kept
      ElMessage.warning('有资料已从材料库移除，已自动取消勾选')
    }
  }
  // 笔记同理：暂存的勾选可能已被删除
  if (wiz.selNote.size) {
    const aliveN = new Set(allNotes.value.map(n => n.id))
    const keptN = new Set([...wiz.selNote].filter(id => aliveN.has(id)))
    if (keptN.size !== wiz.selNote.size) {
      wiz.selNote = keptN
      ElMessage.warning('有笔记已被删除，已自动取消勾选')
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
      selNote: new Set(wiz.selNote),
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

function toggleNote(n) {
  if (autoCovered(n)) return          // 已随资料纳入：置灰不可点（避免重复绑定的困惑）
  const s = new Set(wiz.selNote)
  if (s.has(n.id)) s.delete(n.id); else s.add(n.id)
  wiz.selNote = s
}

async function runDraft() {
  if (!wiz.sel.size && !wiz.selNote.size) { ElMessage.warning('请先回到第①步选择资料或笔记'); return }
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
    const { data } = await appsApi.assist({ kind: 'draft', material_ids: [...wiz.sel], note_ids: [...wiz.selNote] })
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
      model_id: wiz.form.model_id === 'default' ? '' : wiz.form.model_id,
      questions: wiz.form.questions.filter(q => q.trim()),
      material_ids: [...wiz.sel],
      note_ids: [...wiz.selNote],
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
  ritual.appName = app.name; ritual.appId = app.id; ritual.spiritImg = ''
  // 直接存创建接口返回的 spirit（2026-10-29 修）：原来 openLid 里从 apps.value 反查，
  // 而 loadApps 是异步的 —— 列表还没回来时反查不到 → 兜底成 book，用户选的形象没显示。
  ritual.spirit = app.spirit || 'book'
  ritual.stageText = BREW_STAGES[0]; ritual.stageIdx = 0
  // 投料丹丸用真实资料名（创建接口返回 materials），最多 4 颗防拥挤
  ritual.mats = (app.materials || []).map(m => m.title).slice(0, 4)
  brewTimers.forEach(clearTimeout); brewTimers = []
  BREW_STAGES.forEach((s, i) => {
    if (i > 0) brewTimers.push(setTimeout(() => {
      ritual.stageText = s; ritual.stageIdx = i
      if (i === BREW_STAGES.length - 1) ritual.canOpen = true
    }, i * 900))
  })
}
function openLid() {
  if (!ritual.canOpen || ritual.play) return
  brewTimers.forEach(clearTimeout); brewTimers = []
  ritual.canOpen = false; ritual.stageText = ''
  ritual.spiritImg = spiritImg(ritual.spirit, 'happy')   // 丹成即开心（P0）：升起定格 1.6s 后回常态
  ritual.play = true
  brewTimers.push(setTimeout(() => { ritual.spiritImg = spiritImg(ritual.spirit, 'normal') }, 1600))
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

// ---------------- 形象名片（P1） ----------------
// Canvas 本地合成，不发新请求：数据只用列表已有字段；webp 同源，画布不污染可直接 toDataURL。
const shareCard = reactive({ open: false, url: '', name: '' })
async function openShareCard(a) {
  const t = spiritTheme(a.spirit)
  const W = 720, H = 960
  const cv = document.createElement('canvas')
  cv.width = W; cv.height = H
  const ctx = cv.getContext('2d')
  const g = ctx.createLinearGradient(0, 0, 0, H)
  g.addColorStop(0, t.body); g.addColorStop(1, '#ffffff')
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H)
  ctx.fillStyle = t.edge; ctx.fillRect(0, 0, W, 10)          // 顶部气质条
  const img = await new Promise((res, rej) => {
    const i = new Image()
    i.onload = () => res(i)
    i.onerror = () => rej(new Error('img load failed'))
    i.src = spiritImg(a.spirit)
  })
  const ih = 430, iw = ih * img.width / img.height
  ctx.drawImage(img, (W - iw) / 2, 56, iw, ih)
  ctx.textAlign = 'center'
  ctx.fillStyle = '#222222'; ctx.font = '600 44px sans-serif'
  ctx.fillText(a.name || '', W / 2, 560)
  const meta = SPIRIT_OPTIONS.find(o => o.id === parseSpirit(a.spirit).deco)
  ctx.fillStyle = t.edge; ctx.font = '500 26px sans-serif'
  ctx.fillText(meta ? `${meta.label} · ${meta.blurb}` : '', W / 2, 608)
  ctx.fillStyle = '#6e6e6e'; ctx.font = '24px sans-serif'
  ctx.fillText(`绑定 ${a.material_count} 份资料${a.note_count ? ` · ${a.note_count} 条笔记` : ''} · 已聊 ${a.session_count} 场 · ${a.bond?.label || '初识'}`, W / 2, 662)
  ctx.font = '20px sans-serif'
  ctx.fillText(`知萤AI知识中枢 · 炼丹炉 · ${new Date().toISOString().slice(0, 10)}`, W / 2, H - 40)
  shareCard.url = cv.toDataURL('image/png')
  shareCard.name = a.name || 'app'
  shareCard.open = true
}
function downloadShareCard() {
  const link = document.createElement('a')
  link.href = shareCard.url
  link.download = `${shareCard.name}-名片.png`
  link.click()
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
    globalChatModelId.value = data.chat_model_id || ''
  } catch (e) { /* 模型清单拉取失败 → 隐藏模型选择行 */ }
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  clearBrew()
  document.body.style.overflow = ''
})
</script>

<style scoped>
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
.ac-spirit { position: relative; height: 62px; display: flex; align-items: flex-end; justify-content: center;
  transition: transform .18s ease; }
/* hover 抬头（P0）：作用在容器上，不与 img 的 breathe transform 打架 */
.app-card:hover .ac-spirit { transform: translateY(-2px); }
.ac-spirit::before { content: ''; position: absolute; bottom: -4px; left: 50%; margin-left: -44px; width: 88px; height: 88px;
  border-radius: 50%; background: var(--app-body); opacity: .5; filter: blur(14px); }
/* ⚠️ .ac-spirit-img 的 position:relative 不能省：::before 光晕是定位元素（且 blur/opacity 建层叠上下文），
   非定位 img 会被它压在底下 = 「泛白像盖了蒙版」（2026-10-30 实测）。原 :deep(svg) 规则随 SVG 版下线。 */
.ac-name { position: relative; font-size: 15px; font-weight: 600; margin-top: 8px; }
.tag-ok { font-size: 11px; font-weight: 500; border-radius: 4px; padding: 1px 7px; background: #f0f9eb; color: #67c23a; }
.tag-draft { font-size: 11px; font-weight: 500; border-radius: 4px; padding: 1px 7px; background: #fdf6ec; color: #b45309; }
.ac-body { padding: 10px 16px 14px; }
.ac-persona { margin: 0 0 8px; font-size: 12.5px; color: var(--asc-text-2); line-height: 1.6; height: 40px; overflow: hidden;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.ac-meta { font-size: 12px; color: var(--asc-text-2); margin-bottom: 12px; display: flex; align-items: center; flex-wrap: wrap; gap: 0; }
/* 默契阶段徽章（B1）：气质色描边小章，与「运行中/待补料」状态标同尺寸 */
.ac-bond { font-size: 11px; font-weight: 500; color: var(--app-edge, var(--asc-primary));
  border: 1px solid var(--app-edge, var(--asc-primary)); border-radius: 999px;
  padding: 0 7px; margin-right: 7px; line-height: 17px; opacity: .85; flex-shrink: 0; cursor: default; }
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
.spirit-breathe { animation: breathe 3.2s ease-in-out infinite; transform-origin: 50% 88%; }
/* 位图形象尺寸：应用卡按高 62、向导选项按宽 44、汇总 52、揭炉 80 */
.ac-spirit-img { height: 62px; display: block; position: relative; }
.wiz-sp-img { width: 44px; display: block; margin: 0 auto; }
.sum-icon-img { height: 52px; display: block; }
.ritual-spirit-img { width: 80px; display: block; }
@keyframes breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.035); } }

/* 向导 */
.wiz-mask { position: fixed; inset: 0; background: rgba(30, 27, 46, .45); z-index: 100; display: flex; align-items: center; justify-content: center; }
.wiz-card { background: var(--asc-card); border-radius: 14px; width: 780px; max-width: calc(100vw - 64px); max-height: 86vh; overflow-y: auto; padding: 22px 26px; }
/* 步骤条吸顶（2026-11-02）：.wiz-card 是滚动容器，步骤条须钉在顶部不随内容滚动；
   负 margin 抵消卡片 padding 让底色铺到边，gutter 保持原节奏 */
.steps { display: flex; gap: 8px; margin: -22px -26px 16px; padding: 22px 26px 10px; align-items: center;
  position: sticky; top: 0; z-index: 5; background: var(--asc-card); border-radius: 14px 14px 0 0; }
.step { padding: 6px 14px; border-radius: 999px; background: var(--asc-surface-2); color: var(--asc-text-2); font-size: 12.5px; }
.step.on { background: var(--asc-primary-soft); color: var(--asc-primary); font-weight: 500; }
.wiz-x { margin-left: auto; cursor: pointer; color: var(--asc-text-2); font-size: 18px; padding: 0 6px; }
.wiz-x:hover { color: var(--asc-text); }
/* 投料双栏（2026-11-02）：资料/笔记各一栏、栏内自滚，长列表不再把另一区顶出视口 */
.wz-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; margin-bottom: 14px; }
.wz-col { border: 1px solid var(--asc-border); border-radius: 10px; overflow: hidden; background: var(--asc-card); }
.wz-col-head { display: flex; align-items: center; gap: 8px; padding: 9px 12px;
  background: var(--asc-surface-2); border-bottom: 1px solid var(--asc-divider);
  font-size: 13px; font-weight: 600; }
.wz-col-count { font-size: 11.5px; font-weight: 500; color: var(--asc-primary);
  background: var(--asc-primary-soft); border-radius: 999px; padding: 1px 9px; font-variant-numeric: tabular-nums; }
/* 只看已选开关（2026-11-02）：已选置顶之外的「过滤」视角 */
.wz-sel-chip { border: 1px solid var(--asc-border); background: var(--asc-card); color: var(--asc-text-2);
  font-size: 11.5px; padding: 2px 10px; border-radius: 999px; cursor: pointer; font-family: inherit;
  transition: all .15s ease; }
.wz-sel-chip:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
.wz-sel-chip.on { background: var(--asc-primary-soft); border-color: var(--asc-primary); color: var(--asc-primary); font-weight: 500; }
.wz-col-head .wz-search { margin-left: auto; }
.wz-col-body { max-height: 44vh; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
.wz-col-body .pick { margin: 0; }
.wz-col-note { font-size: 11.5px; color: var(--asc-text-2); margin: 0 0 2px; line-height: 1.5; }
@media (max-width: 720px) { .wz-cols { grid-template-columns: 1fr; } .wz-col-body { max-height: 32vh; } }
.wz-sub-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.wz-sub { margin: 0 0 12px; font-size: 13px; color: var(--asc-text-2); }
.wz-search { width: 160px; flex-shrink: 0; }
.pick { position: relative; background: var(--asc-card); border: 1.5px solid var(--asc-border); border-radius: 10px; padding: 12px 14px; cursor: pointer; transition: all .15s ease; }
.pick:hover { border-color: #c9c4e8; }
.pick.on { border-color: var(--asc-primary); background: var(--asc-primary-soft); }
.pick-check { position: absolute; top: 8px; right: 8px; width: 18px; height: 18px; border-radius: 50%; border: 1.5px solid var(--asc-border); background: #fff; font-size: 11px; color: #fff; display: flex; align-items: center; justify-content: center; }
.pick.on .pick-check { background: var(--asc-primary); border-color: var(--asc-primary); }
.pick-name { font-size: 13.5px; font-weight: 500; margin-bottom: 3px; padding-right: 22px; }
.pick-meta { font-size: 12px; color: var(--asc-text-2); }
/* 笔记卡（2026-10-30 笔记绑定）：随资料自动纳入的置灰禁点，避免重复绑定的困惑 */
.pick-note.auto { opacity: .55; cursor: not-allowed; border-style: dashed; }
.pick-note.auto .pick-check { display: none; }
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

/* 揭炉仪式（2026-10-30 二轮：白卡太大太空 → 收紧 + 氛围光） */
.ritual-mask { position: fixed; inset: 0; background: rgba(30, 27, 46, .5); z-index: 110; display: flex; align-items: center; justify-content: center; }
/* 卡面氛围：炼制期=炉光映照的暖晕（呼吸），丹成=品牌紫光晕；都是「火光」不是装饰 */
.ritual { position: relative; text-align: center; border-radius: 16px; padding: 24px 36px 24px; min-width: 400px;
  background: radial-gradient(circle at 50% 36%, rgba(239, 159, 39, .13), transparent 62%), var(--asc-card);
  border: 1px solid rgba(239, 159, 39, .28);
  box-shadow: 0 18px 50px rgba(30, 27, 46, .18);
  transition: background .8s ease, border-color .8s ease;
  animation: ritualIn .38s cubic-bezier(.2, .8, .3, 1.1); }
.ritual.play { background: radial-gradient(circle at 50% 36%, rgba(124, 92, 252, .12), transparent 62%), var(--asc-card);
  border-color: rgba(124, 92, 252, .22); }
@keyframes ritualIn { from { opacity: 0; transform: scale(.93) translateY(12px); } to { opacity: 1; transform: scale(1) translateY(0); } }
.ritual-x { position: absolute; top: 10px; right: 12px; border: none; background: transparent; color: var(--asc-text-2);
  font-size: 18px; cursor: pointer; line-height: 1; padding: 2px 6px; border-radius: 6px; }
.ritual-x:hover { color: var(--asc-text); background: var(--asc-surface-2); }
.ritual-stage { position: relative; height: 214px; margin-top: 10px; }
/* 炼制期炉光气晕（呼吸）：让「在烧」铺满卡面，不只是炉子自己在动 */
.brew-aura { position: absolute; left: 50%; bottom: 4px; width: 300px; height: 160px; margin-left: -150px;
  background: radial-gradient(ellipse, rgba(239, 159, 39, .17), transparent 70%);
  pointer-events: none; animation: auraBreath 2.4s ease-in-out infinite; }
@keyframes auraBreath { 0%, 100% { opacity: .6; transform: scale(1); } 50% { opacity: 1; transform: scale(1.08); } }
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
/* ===== 炼制期动画（2026-10-30：原来只有文字在换、炉子静坐着 =「没有看到动画」） ===== */
.ritual-mask { animation: maskIn .3s ease; }
@keyframes maskIn { from { opacity: 0; } to { opacity: 1; } }
/* 炉身轻颤（像里头在咕嘟）；可与点击揭炉的 shakeHint 共存 → 未可揭时才生效 */
.brewing .furnace-wrap:not(.can-open) :deep(.furnace) { animation: brewRumble .5s ease-in-out infinite; }
@keyframes brewRumble { 0%, 100% { transform: translateX(0) rotate(0); } 25% { transform: translateX(-.8px) rotate(-.5deg); } 75% { transform: translateX(.8px) rotate(.5deg); } }
/* 腹窗/炉口火光呼吸（明暗起伏 = 火在烧） */
.brewing :deep(.furnace-fire) { animation: brewFire 1.6s ease-in-out infinite; }
@keyframes brewFire { 0%, 100% { opacity: .55; } 50% { opacity: .95; } }
/* 丹气蒸汽：三道热气上升飘散 */
.steam { position: absolute; left: 50%; bottom: 118px; transform: translateX(-50%); display: flex; gap: 14px; pointer-events: none; }
.steam i { width: 9px; height: 28px; border-radius: 50%; background: #b9a48e; opacity: 0; filter: blur(2.5px); animation: steamUp 2.2s ease-in-out infinite; }
.steam i:nth-child(2) { animation-delay: .7s; height: 34px; }
.steam i:nth-child(3) { animation-delay: 1.4s; }
@keyframes steamUp { 0% { transform: translateY(10px) scaleY(.6); opacity: 0; } 35% { opacity: .72; } 100% { transform: translateY(-30px) scaleY(1.15); opacity: 0; } }
/* 阶段文本入场（:key 重挂载每次触发） */
.brew-stage { animation: stageIn .35s ease; }
@keyframes stageIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
/* 四段进度点 */
.brew-dots { display: flex; justify-content: center; gap: 6px; margin: 8px 0 4px; }
.brew-dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--asc-divider); transition: background .3s, transform .3s; }
.brew-dots i.on { background: var(--asc-primary); }
.brew-dots i.cur { transform: scale(1.35); }
/* 炼制期下半区占位（期待感填空，丹成后由标题/名称/副文案接管）。
   2026-11-02 改绝对定位：原来在文档流里把白卡撑得比丹成态高 ~62px（margin 14+30 + 行高），
   悬到下方文案区上方 → 不占高度，炼制期/丹成态卡面严格同高 */
.brew-expect { position: absolute; left: 0; right: 0; bottom: 102px; margin: 0; font-size: 12.5px; color: var(--asc-text-3); letter-spacing: 2px; animation: expectPulse 2.4s ease-in-out infinite; }
@keyframes expectPulse { 0%, 100% { opacity: .55; } 50% { opacity: 1; } }
/* ① 投料丹丸：从上方旋转落入炉口，缩小没入（每颗横向错位，--dx 由内联样式给） */
.feed-chips { position: absolute; inset: 0; pointer-events: none; }
.feed-chip { position: absolute; top: -8px; left: 50%; max-width: 76px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  padding: 3px 10px; border-radius: 999px; font-size: 10.5px; font-weight: 500;
  background: linear-gradient(180deg, #f7e3bd, #e8c384); color: #6e4f33; border: 1px solid #c89a5e;
  box-shadow: 0 2px 6px rgba(110, 79, 51, .25);
  opacity: 0; animation: feedDrop .62s ease-in forwards; }
@keyframes feedDrop {
  0% { transform: translate(calc(-50% + var(--dx)), -8px) rotate(-10deg) scale(1); opacity: 0; }
  14% { opacity: 1; }
  78% { transform: translate(calc(-50% + var(--dx)), 96px) rotate(7deg) scale(.92); opacity: 1; }
  100% { transform: translate(calc(-50% + var(--dx)), 104px) rotate(0deg) scale(.18); opacity: 0; }
}
/* ② 阶段渐强：丹香段蒸汽转金；临近丹成火势转急 */
.brew-s2 .steam i { background: #d9ab66; }
.brew-s3 :deep(.furnace-fire) { animation-duration: .9s; }
/* ③ 丹光爆闪：揭炉瞬间一圈金光炸开扩散 */
.burst { position: absolute; left: 50%; bottom: 44px; width: 170px; height: 170px; margin-left: -85px;
  border-radius: 50%; border: 3px solid rgba(239, 159, 39, .85); opacity: 0; pointer-events: none; }
.play .burst { animation: burstOut .75s .5s ease-out forwards; }
@keyframes burstOut { 0% { transform: scale(.22); opacity: .9; } 100% { transform: scale(1.65); opacity: 0; } }
.ritual-ops { display: flex; justify-content: center; align-items: center; gap: 10px; }
.rv { opacity: 0; }
.play :deep(.furnace-lid) { animation: lidOpen 1s .15s ease forwards; transform-origin: 56px 58px; }
.play :deep(.furnace-fire) { animation: fireReveal .7s .55s ease forwards; }
.play .ritual-glow { animation: fadeIn .8s .9s forwards; }
.play .ritual-spirit { animation: riseUp .9s .85s cubic-bezier(.2, .8, .3, 1.25) forwards, bob 3.2s 1.9s ease-in-out infinite; }
.play .ritual-text { animation: textPop .55s 1.5s cubic-bezier(.2, .8, .3, 1.3) forwards; }
/* ④ 冲霄光柱：双层（亮芯 + 外晕），底缘沉进炉口下 10px（悬在口上会像浮着的） */
.beam { position: absolute; left: 50%; bottom: 96px; width: 22px; height: 178px; margin-left: -11px;
  background: linear-gradient(180deg, transparent 0%, rgba(255, 233, 168, .95) 42%, rgba(239, 159, 39, .95) 78%, rgba(239, 159, 39, .6) 100%);
  border-radius: 999px; opacity: 0; pointer-events: none; transform-origin: 50% 100%; }
.beam::before { content: ''; position: absolute; inset: -10px -16px; border-radius: 999px;
  background: linear-gradient(180deg, transparent 0%, rgba(255, 217, 138, .5) 45%, rgba(239, 159, 39, .45) 80%, transparent 100%);
  filter: blur(7px); }
.play .beam { animation: beamShoot 1.15s .5s ease-out forwards; }
@keyframes beamShoot { 0% { transform: scaleY(.12); opacity: 0; } 20% { opacity: 1; } 55% { transform: scaleY(1); opacity: .9; } 100% { transform: scaleY(1.06); opacity: 0; } }
/* ⑤ 星尘：四角星芒（菱形+光晕），丹灵现身后常驻微闪 */
.sparkles { position: absolute; inset: 0; pointer-events: none; opacity: 0; }
.play .sparkles { animation: fadeIn .6s 1.25s forwards; }
.sparkles i { position: absolute; width: 7px; height: 7px; transform: rotate(45deg);
  background: radial-gradient(circle, #fff8e6 0%, #ffd98a 45%, rgba(255, 176, 58, .9) 100%);
  box-shadow: 0 0 6px 1px rgba(255, 176, 58, .65);
  animation: spTwinkle 2.2s ease-in-out infinite; }
.sparkles .sp1 { left: calc(50% - 56px); top: 56px; }
.sparkles .sp2 { left: calc(50% + 50px); top: 70px; animation-delay: .5s; }
.sparkles .sp3 { left: calc(50% - 48px); top: 102px; animation-delay: 1s; width: 5.5px; height: 5.5px; }
.sparkles .sp4 { left: calc(50% + 54px); top: 106px; animation-delay: 1.5s; width: 5.5px; height: 5.5px; }
.sparkles .sp5 { left: calc(50% + 10px); top: 34px; animation-delay: .8s; width: 5px; height: 5px; }
@keyframes spTwinkle { 0%, 100% { opacity: .3; transform: rotate(45deg) scale(.75); } 50% { opacity: 1; transform: rotate(45deg) scale(1.2); } }
.play .ritual-name { animation: fadeUp .5s 1.75s forwards; }
.play .ritual-sub { animation: fadeUp .5s 1.95s forwards; }
.play .ritual-btn { animation: fadeUp .5s 2.25s forwards; }
.ritual-text { font-size: 26px; font-weight: 600; color: var(--asc-primary); letter-spacing: 2px; }
.ritual-name { font-size: 17px; font-weight: 600; margin-top: 8px; }
.ritual-sub { font-size: 13px; color: var(--asc-text-2); margin: 8px 0 18px; }
.ritual-btn { min-width: 140px; }
/* 形象名片 */
.share-card-wrap { display: flex; justify-content: center; }
.share-card-img { width: 320px; border-radius: 12px; border: 1px solid var(--asc-border); display: block; }
@keyframes lidOpen { 0% { transform: rotate(0); } 20% { transform: rotate(-3deg); } 34% { transform: rotate(3deg); } 46% { transform: rotate(-1.5deg); } 100% { transform: rotate(-32deg); } }
@keyframes riseUp { from { opacity: 0; transform: translateY(64px) scale(.6); } to { opacity: 1; transform: translateY(-4px) scale(1); } }
@keyframes bob { 0%, 100% { transform: translateY(-4px); } 50% { transform: translateY(-12px); } }
@keyframes fadeUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
@keyframes textPop { 0% { opacity: 0; transform: scale(.7); } 70% { opacity: 1; transform: scale(1.08); } 100% { opacity: 1; transform: scale(1); } }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes fireReveal { from { opacity: 0; } to { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .spirit-breathe, .furnace-wrap.can-open :deep(.furnace), .ritual-hint.vis { animation: none; }
  .play .rv { opacity: 1; transform: none; animation: none; }
  .play :deep(.furnace-lid) { animation: none; transform: rotate(-32deg); }
  .play :deep(.furnace-fire) { animation: none; opacity: 1; }
  .ritual-mask, .ritual { animation: none; }
  .brewing .furnace-wrap :deep(.furnace), .brewing :deep(.furnace-fire), .steam i, .brew-stage { animation: none; }
  .steam, .feed-chips { display: none; }
  .play .burst { animation: none; opacity: 0; }
  .play .beam { animation: none; opacity: 0; }
  .play .sparkles { animation: none; opacity: 1; }
  .sparkles i { animation: none; opacity: .6; }
  .play .ritual-text { animation: none; }
  .brew-aura { display: none; }
}
</style>
