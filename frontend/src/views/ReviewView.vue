<template>
  <div class="page review-page" v-loading="loading">
    <div class="page-header">
      <div class="header-title">
        <h2>复习巩固</h2>
        <p class="header-sub">用间隔重复对抗遗忘 · 每天清空待复习卡片</p>
      </div>
      <div class="stats-row" v-if="stats.total">
        <span class="stat-chip due clickable" title="点击查看待复习卡片" @click="openStatus('due')"><b>{{ stats.due }}</b><i>待复习</i></span>
        <span class="stat-chip clickable" title="点击查看今日已复习" @click="openStatus('reviewed_today')"><b>{{ stats.reviewed_today }}</b><i>今日已复习</i></span>
        <span class="stat-chip clickable" title="点击查看全部卡片" @click="openStatus('all')"><b>{{ stats.total }}</b><i>卡片总数</i></span>
        <span class="stat-chip mastered clickable" title="点击查看已掌握卡片" @click="openStatus('mastered')"><b>{{ stats.mastered }}</b><i>已掌握</i></span>
        <span v-if="stats.weak" class="stat-chip weak clickable" title="点击查看易忘卡片" @click="openStatus('weak')"><b>{{ stats.weak }}</b><i>易忘</i></span>
      </div>
    </div>

    <!-- 筛选栏：按资料 / 按分类 -->
    <div v-if="stats.total" class="filter-bar">
      <span class="filter-label">筛选</span>
      <el-select v-model="materialFilter" placeholder="全部资料" clearable size="small"
        style="width: 210px" @change="onFilterChange">
        <el-option v-for="m in filterOptions.materials" :key="m.id" :label="m.title" :value="m.id" />
      </el-select>
      <el-select v-model="tagFilter" placeholder="全部分类" clearable size="small"
        style="width: 150px" @change="onFilterChange">
        <el-option v-for="t in filterOptions.tags" :key="t" :label="t" :value="t" />
      </el-select>
      <el-select v-model="typeFilter" placeholder="全部题型" clearable size="small"
        style="width: 120px" @change="onFilterChange">
        <el-option label="选择题" value="choice" />
        <el-option label="复述题" value="recall" />
      </el-select>
      <span v-if="(materialFilter || tagFilter || typeFilter) && quota" class="filter-count">
        当前筛选 {{ queue.length }} / {{ quota }} 张
      </span>
    </div>

    <!-- 完全空状态 -->
    <el-empty v-if="!loading && stats.total === 0" :image="mascot" :image-size="120"
      description="还没有复习卡片。在学习页打开笔记，点「加入复习」由 AI 出题生成">
      <el-button type="primary" @click="$router.push('/')">去学习</el-button>
    </el-empty>

    <!-- 左右两栏：左=打卡+遗忘曲线，右=问答测试题 -->
    <div v-else-if="!loading" class="review-grid">
      <div class="review-col review-left">
        <div class="insight-card">
      <!-- 打卡里程碑：火焰 + 连续天数 + 本周点阵 + 记得率 -->
      <div class="insight-sec">
      <div class="sec-title">学习打卡</div>
      <div class="streak-row">
        <div class="streak-flame" :class="{ lit: stats.streak > 0 }">
          <svg viewBox="0 0 24 24" width="40" height="40">
            <defs>
              <linearGradient id="flame-outer" x1="0" y1="1" x2="0" y2="0">
                <stop offset="0" stop-color="#7c5cfc"/>
                <stop offset=".55" stop-color="#a78bfa"/>
                <stop offset="1" stop-color="#f0abfc"/>
              </linearGradient>
              <linearGradient id="flame-inner" x1="0" y1="1" x2="0" y2="0">
                <stop offset="0" stop-color="#5b3df5"/>
                <stop offset="1" stop-color="#8b5cf6"/>
              </linearGradient>
            </defs>
            <!-- 外焰 -->
            <path d="M12 2c1.5 3.9 4.9 5.8 4.9 9.6a4.9 4.9 0 0 1-9.8 0c0-1.6.6-3 1.4-4.1.4 1.4 1.8 2.2 2.9 2.1.2-2.1.2-5.3.6-7.6z"
              fill="url(#flame-outer)"/>
            <!-- 内焰 -->
            <path d="M12 7.5c.9 2.3 2.9 3.4 2.9 5.7a2.9 2.9 0 0 1-5.8 0c0-1 .4-1.8.8-2.4.2.8 1 1.3 1.7 1.2.1-1.2.1-3.2.4-4.5z"
              fill="url(#flame-inner)"/>
          </svg>
        </div>
        <div class="streak-main">
          <div class="streak-num">连续打卡 <b>{{ stats.streak }}</b> 天</div>
          <div class="streak-dots">
            <template v-for="(d, di) in streakDots" :key="d.date">
              <span v-if="di > 0" class="dot-connector" :class="{ filled: d.active }"></span>
              <span class="streak-dot"
                :class="{ active: d.active, today: d.isToday }"
                :title="d.date + (d.active ? '：复习 ' + d.count + ' 次' : '：未复习')">
                <span v-if="d.active" class="dot-check">✓</span>
              </span>
            </template>
          </div>
        </div>
        <div class="streak-rate">
          <span class="rate-label">本周记得率</span>
          <span class="rate-num">{{ keepRate }}<i>%</i></span>
        </div>
      </div>
      </div>

      <!-- 近 7 天复习量 -->
      <div class="insight-sec">
      <div class="sec-title">近 7 天复习量</div>
      <div class="bar-chart">
        <div v-for="(d, di) in stats.daily" :key="d.date" class="bar-col">
          <div class="bar-track">
            <div class="bar" :class="{ today: di === stats.daily.length - 1 }"
              :style="{ height: barHeight(d.count) }" :title="d.date + '：复习 ' + d.count + ' 次'"></div>
          </div>
          <span class="bar-count" v-if="d.count">{{ d.count }}</span>
          <span class="bar-label">{{ d.date.slice(5) }}</span>
        </div>
      </div>
      </div>

      <!-- 遗忘曲线：间隔 → 记得率（SVG 折线） -->
      <div v-if="hasCurve && curveView" class="insight-sec">
        <div class="sec-title">你的遗忘曲线</div>
        <p v-if="curveEnough" class="curve-conclusion">{{ curveTakeaway }}</p>
        <svg v-if="curveEnough" class="curve-svg" role="img" :aria-label="curveSummary"
          :viewBox="`0 0 ${CURVE_W} ${CURVE_H}`">
          <defs>
            <linearGradient id="curve-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#2dd4a0" stop-opacity=".30"/>
              <stop offset="1" stop-color="#2dd4a0" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <!-- 网格参考线 -->
          <line v-for="g in [0, 50, 100]" :key="g"
            :x1="C_PAD_L" :x2="CURVE_W - C_PAD_R"
            :y1="C_PAD_T + (1 - g / 100) * curveView.plotH"
            :y2="C_PAD_T + (1 - g / 100) * curveView.plotH"
            class="curve-grid" :class="{ mid: g === 50 }" />
          <text v-for="g in [0, 50, 100]" :key="'t' + g"
            :x="C_PAD_L - 8" :y="C_PAD_T + (1 - g / 100) * curveView.plotH + 3"
            class="curve-axis" text-anchor="end">{{ g }}%</text>

          <!-- 参考虚线：不复习的自然遗忘（艾宾浩斯近似衰减） -->
          <path v-if="curveView.refLine" :d="curveView.refLine" class="curve-ref" />

          <!-- 面积渐变 + 折线 -->
          <path v-if="curveView.area" :d="curveView.area" class="curve-area" />
          <path v-if="curveView.line" :d="curveView.line" class="curve-line" />

          <!-- 数据点 + 数值标签 -->
          <g v-for="p in curveView.pts" :key="p.days">
            <circle v-if="p.y != null" :cx="p.x" :cy="p.y" r="4" class="curve-dot" :class="{ low: (p.total || 0) < 3 }">
              <title>间隔 {{ p.days }} 天复习时，你记得 {{ p.rate }}%（{{ p.total }} 张卡，{{ (p.total || 0) < 3 ? '样本不足仅供参考' : '样本充足' }}）</title>
            </circle>
            <text :x="p.x" :y="(p.y != null ? p.y : curveView.base) - 10" class="curve-val"
              :class="{ dim: p.y == null || (p.total || 0) < 3 }" text-anchor="middle">
              {{ p.rate == null ? '—' : p.rate + '%' }}
            </text>
            <text :x="p.x" :y="curveView.base + 18" class="curve-x" text-anchor="middle">{{ p.days }}天</text>
          </g>
        </svg>
        <div v-if="curveEnough" class="curve-legend">
          <span class="legend-item"><i class="lg lg-you"></i>你的保持率</span>
          <span class="legend-item"><i class="lg lg-ref"></i>不复习的自然遗忘（参考）</span>
          <span class="legend-axis">横轴 = 复习间隔天数</span>
        </div>
        <div v-else class="curve-empty">
          复习记录还不满 2 个间隔，多复习几次后曲线会更完整
        </div>
      </div>
        </div>
      </div>

      <!-- 右栏：问答测试题 -->
      <div class="review-col review-right">
        <!-- 今日完成（队列清空 or 全部评完） -->
        <div v-if="!current" class="all-done">
      <img :src="mascot" class="done-mascot" alt="伴学猫头鹰" />
      <p class="done-title">今日复习完成，伴伴为你点赞</p>
      <p class="done-sub">知识又巩固了一层，明天见</p>
      <p v-if="stats.reviewed_today" class="done-stats">今日复习 {{ stats.reviewed_today }} 次 · 今日记得率 {{ todayRate }}%</p>
        <el-button @click="showAll = !showAll">{{ showAll ? '收起全部卡片' : '查看全部卡片' }}</el-button>
      </div>

      <!-- 复习卡 -->
      <div v-else-if="current" class="card-stage">
      <div class="stage-progress">
        <div class="stage-progress-fill" :style="{ width: progressPct + '%' }"></div>
      </div>
      <div class="card-meta-row">
        <span class="card-src">{{ current.material_title || '笔记' }}</span>
        <span class="card-type">{{ { choice: '选择题', recall: '复述卡' }[current.type] || '简答' }}</span>
        <span class="card-level">
          <span class="lv-dots" :title="'第 ' + (current.level + 1) + ' 级 / 共 6 级'">
            <i v-for="n in 6" :key="n" :class="{ on: n <= current.level + 1 }"></i>
          </span>
          已复习 {{ current.review_count }} 次
        </span>
      </div>

      <!-- 题干 -->
      <div class="card-question">
        <div class="q-ask">
          <img :src="mascot" class="q-mascot" alt="" />
          <span class="q-ask-label">伴伴考你</span>
        </div>
        <div class="q-text">{{ current.question }}</div>
      </div>

      <!-- 选择题选项 -->
      <div v-if="current.type === 'choice'" class="choice-opts">
        <div v-for="(opt, i) in current.options" :key="i" class="opt"
          :class="{
            correct: picked !== null && i === current.correct_index,
            wrong: picked !== null && i === picked && i !== current.correct_index,
          }" @click="pick(i)">
          <span class="opt-key">{{ 'ABCD'[i] }}</span>
          <span class="opt-text">{{ opt }}</span>
          <span v-if="picked !== null && i === current.correct_index" class="opt-mark">✓</span>
          <span v-else-if="picked !== null && i === picked && i !== current.correct_index" class="opt-mark">✗</span>
        </div>
      </div>

      <!-- 简答/复述卡：翻面看答案 -->
      <div v-else class="qa-body">
        <div v-if="flipped" class="qa-answer md-body" v-html="renderMd(current.answer)"></div>
        <div v-else class="qa-hint">{{ current.type === 'recall' ? '先自己讲一遍，再对照参考答案' : '按空格 / 点击下方按钮查看答案' }}</div>
      </div>

      <!-- 解析（选择题作答后显示） -->
      <div v-if="current.type === 'choice' && picked !== null" class="explain">
        <div class="explain-label">解析</div>
        <div class="md-body" v-html="renderMd(current.answer)"></div>
      </div>

      <!-- 自评按钮（作答/翻面后出现） -->
      <div class="grade-row" v-if="revealed">
        <el-button class="grade-btn forgot" @click="grade('forgot')">忘了<small>1 · 明天再来</small></el-button>
        <el-button class="grade-btn fuzzy" @click="grade('fuzzy')">模糊<small>2 · 回退一档</small></el-button>
        <el-button class="grade-btn easy" @click="grade('easy')">记得<small>3 · 间隔拉长</small></el-button>
        <el-button class="grade-btn simple" @click="grade('simple')">简单<small>4 · 跳级</small></el-button>
      </div>

      <div class="reveal-hint" v-if="!revealed">
        <el-button v-if="current.type !== 'choice'" text type="primary" @click="flipped = true">
          {{ current.type === 'recall' ? '我讲完了，看答案（空格）' : '查看答案（空格）' }}
        </el-button>
      </div>
      <div class="queue-progress">第 {{ idx + 1 }} / {{ queue.length }} 张<span v-if="quota > queue.length">（今日上限 {{ limit }} 张，剩余 {{ quota - queue.length }} 张顺延）</span></div>
      <div class="kbd-hint"><span class="kbd">空格</span>翻卡 · <span class="kbd">1</span>忘了 <span class="kbd">2</span>模糊 <span class="kbd">3</span>记得 <span class="kbd">4</span>简单</div>
      </div>
      </div>
    </div>

    <!-- 全部卡片列表 -->
    <div v-if="showAll && allCards.length" class="all-list">
      <div class="all-head">
        <span class="all-title">全部卡片（{{ allCards.length }}）</span>
        <el-input v-model="allSearch" size="small" placeholder="搜索题干关键词" clearable style="width: 220px" />
      </div>
      <div v-if="!filteredCards.length" class="all-empty">没有匹配的卡片</div>
      <div v-for="c in filteredCards" :key="c.id" class="all-item">
        <div class="all-q">{{ c.question }}</div>
        <div class="all-meta">
          <el-tag size="small" :type="c.type === 'choice' ? 'primary' : 'info'" effect="plain">
            {{ { choice: '选择题', recall: '复述卡' }[c.type] || '简答' }}
          </el-tag>
          <span>{{ c.material_title }}</span>
          <span class="lv-dots" :title="'第 ' + (c.level + 1) + ' 级 / 共 6 级'">
            <i v-for="n in 6" :key="'l' + n" :class="{ on: n <= c.level + 1 }"></i>
          </span>
          <el-tag v-if="isWeak(c)" size="small" type="danger" effect="plain">易忘</el-tag>
          <span>下次复习 {{ c.next_review_at?.slice(0, 10) }}</span>
          <el-button v-if="c.type === 'qa'" size="small" text type="primary"
            :loading="convertingId === c.id" @click.stop="convertCard(c)">转选择题</el-button>
          <el-button size="small" text type="primary" @click.stop="openEdit(c)">编辑</el-button>
          <el-popconfirm title="删除这张复习卡片？（笔记本身保留）" width="240"
            confirm-button-text="删除" confirm-button-type="danger" cancel-button-text="取消"
            @confirm="removeCard(c.id)">
            <template #reference>
              <el-button size="small" text type="danger" @click.stop>删除</el-button>
            </template>
          </el-popconfirm>
        </div>
      </div>
    </div>

    <!-- 评分撤销条（防误触：5 秒内可撤销最近一次自评） -->
    <transition name="undo-fade">
      <div v-if="undoBar.show" class="undo-bar">
        <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>
        <span>{{ undoBar.text }}</span>
        <button class="undo-btn" @click="undoLast">撤销</button>
      </div>
    </transition>

    <!-- 出题校对弹窗 -->
    <el-dialog v-model="editDialog.show" title="编辑题目" width="540px">
      <el-form label-position="top">
        <el-form-item label="题干">
          <el-input v-model="editDialog.question" type="textarea" :rows="2" />
        </el-form-item>
        <template v-if="editDialog.type === 'choice'">
          <el-form-item v-for="(o, i) in editDialog.options" :key="i" :label="'选项 ' + 'ABCD'[i]">
            <div class="opt-edit-row">
              <el-input v-model="editDialog.options[i]" />
              <el-radio v-model="editDialog.correct_index" :value="i">正确</el-radio>
            </div>
          </el-form-item>
        </template>
        <el-form-item label="解析 / 答案">
          <el-input v-model="editDialog.answer" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog.show = false">取消</el-button>
        <el-button type="primary" :loading="editSaving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 统计下钻弹窗：点击统计数字查看对应卡片，可展开复习 -->
    <el-dialog v-model="statusDialog.show" :title="statusDialog.title" width="640px">
      <div v-loading="statusDialog.loading" class="status-list">
        <el-empty v-if="!statusDialog.loading && statusCards.length === 0"
          :image="mascot" :image-size="96" description="这里还没有卡片" />
        <div v-for="c in statusCards" :key="c.id" class="status-item" :class="{ expanded: expandedId === c.id }">
          <div class="status-q" @click="toggleExpand(c)">
            <span class="status-q-text">{{ c.question }}</span>
            <span class="status-q-arrow">{{ expandedId === c.id ? '收起' : '展开复习' }}</span>
          </div>
          <div class="status-meta">
            <el-tag size="small" :type="c.type === 'choice' ? 'primary' : 'info'" effect="plain">
              {{ { choice: '选择题', recall: '复述卡' }[c.type] || '简答' }}
            </el-tag>
            <span>{{ c.material_title || '笔记' }}</span>
            <span class="lv-dots" :title="'第 ' + (c.level + 1) + ' 级 / 共 6 级'">
              <i v-for="n in 6" :key="'s' + n" :class="{ on: n <= c.level + 1 }"></i>
            </span>
            <span>已复习 {{ c.review_count }} 次</span>
            <el-tag v-if="isWeak(c)" size="small" type="danger" effect="plain">易忘</el-tag>
            <span v-if="c.next_review_at">下次 {{ c.next_review_at.slice(0, 10) }}</span>
          </div>

          <!-- 展开的复习区 -->
          <div v-if="expandedId === c.id" class="status-review">
            <div v-if="c.type === 'choice'" class="status-opts">
              <div v-for="(opt, i) in c.options" :key="i" class="status-opt"
                :class="{
                  correct: c._picked !== undefined && i === c.correct_index,
                  wrong: c._picked !== undefined && i === c._picked && i !== c.correct_index,
                }" @click="pickStatusOpt(c, i)">
                <span class="opt-key">{{ 'ABCD'[i] }}</span>
                <span class="opt-text">{{ opt }}</span>
                <span v-if="c._picked !== undefined && i === c.correct_index" class="opt-mark">✓</span>
                <span v-else-if="c._picked !== undefined && i === c._picked" class="opt-mark wrong-mark">✗</span>
              </div>
            </div>
            <div v-else class="status-qa">
              <div v-if="c._revealed" class="qa-answer md-body" v-html="renderMd(c.answer)"></div>
              <div v-else class="status-qa-hint">
                <el-button text type="primary" size="small" @click="c._revealed = true">查看答案</el-button>
              </div>
            </div>

            <div v-if="c.type === 'choice' && c._picked !== undefined" class="explain md-body" v-html="renderMd(c.answer)"></div>

            <div v-if="statusRevealed(c)" class="status-grade">
              <el-button size="small" class="grade-btn forgot" @click="gradeStatus(c, 'forgot')">忘了<small>明天再来</small></el-button>
              <el-button size="small" class="grade-btn fuzzy" @click="gradeStatus(c, 'fuzzy')">模糊<small>回退一档</small></el-button>
              <el-button size="small" class="grade-btn easy" @click="gradeStatus(c, 'easy')">记得<small>间隔拉长</small></el-button>
              <el-button size="small" class="grade-btn simple" @click="gradeStatus(c, 'simple')">简单<small>跳级</small></el-button>
            </div>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import mascot from '../assets/mascot.png'
import { reviewApi } from '../api'
import { errMsg } from '../api/http'

const md = new MarkdownIt({ breaks: true })
const renderMd = (t) => md.render(t || '')

const loading = ref(true)
const queue = ref([])          // 今日待复习
const allCards = ref([])
const stats = ref({ total: 0, due: 0, reviewed_today: 0, mastered: 0, streak: 0, daily: [] })
const editDialog = reactive({ show: false, id: null, question: '', answer: '', type: 'choice', options: [], correct_index: 0 })
const editSaving = ref(false)
const statusDialog = reactive({ show: false, title: '', key: null, loading: false })
const statusCards = ref([])
const expandedId = ref(null)
const STATUS_LABELS = { due: '待复习', reviewed_today: '今日已复习', all: '全部卡片', mastered: '已掌握', weak: '易忘' }
const idx = ref(0)
const flipped = ref(false)
const picked = ref(null)       // 选择题已选下标
const showAll = ref(false)
const convertingId = ref(null)
const quota = ref(0)       // 到期总卡数
const limit = ref(30)      // 每日上限

const current = computed(() => queue.value[idx.value] || null)
const revealed = computed(() => current.value?.type === 'choice' ? picked.value !== null : flipped.value)
const progressPct = computed(() => queue.value.length ? Math.round((idx.value + 1) / queue.value.length * 100) : 0)

function resetState() {
  flipped.value = false
  picked.value = null
}

// 筛选：按资料 / 按材料分类标签 / 按题型
const materialFilter = ref(null)
const tagFilter = ref('')
const typeFilter = ref('')
const filterOptions = reactive({ materials: [], tags: [] })
function filterParams() {
  const p = {}
  if (materialFilter.value) p.material_id = materialFilter.value
  if (tagFilter.value) p.tag = tagFilter.value
  if (typeFilter.value) p.type = typeFilter.value
  return p
}
function onFilterChange() { load(); loadAll() }
async function loadFilters() {
  try {
    const { data } = await reviewApi.filters()
    filterOptions.materials = data.materials
    filterOptions.tags = data.tags
  } catch { /* 静默 */ }
}

async function load() {
  loading.value = true
  try {
    const [t, s] = await Promise.all([reviewApi.today(filterParams()), reviewApi.stats()])
    queue.value = t.data.cards
    quota.value = t.data.total_due
    limit.value = t.data.limit
    stats.value = s.data
    idx.value = 0
    resetState()
  } finally {
    loading.value = false
  }
}

// 仅刷新统计（不重置队列/翻卡进度），供复习后即时联动所有统计数字
async function refreshStats() {
  try {
    const { data } = await reviewApi.stats()
    stats.value = data
  } catch { /* 静默 */ }
}

function pick(i) {
  if (picked.value !== null) return   // 已作答
  picked.value = i
}

function reveal() {
  if (current.value?.type === 'qa') flipped.value = true
}

async function grade(result) {
  const card = current.value
  if (!card || !revealed.value) return
  let graded = false
  try {
    const { data } = await reviewApi.grade(card.id, result)
    showUndo(card, data, result)
    graded = true
  } catch (e) {
    ElMessage.error(errMsg(e, '记录失败'))
  }
  // 忘了：本组末尾再见一次（当日重学，符合记忆规律）
  if (graded && result === 'forgot') queue.value.push(card)
  resetState()
  idx.value += 1
  refreshStats()   // 复习后刷新完整统计（reviewed_today/due/mastered/weak/streak/daily）
  if (idx.value >= queue.value.length) loadAll()
}

// ===== 评分撤销（防误触）：评分后 5 秒内可撤销 =====
const undoBar = reactive({ show: false, cardId: null, text: '', requeued: false, fromDialog: false })
let undoTimer = null
function showUndo(card, data, result, extra = {}) {
  clearTimeout(undoTimer)
  undoBar.show = true
  undoBar.cardId = card.id
  undoBar.requeued = extra.requeued !== undefined ? extra.requeued : result === 'forgot'
  undoBar.fromDialog = !!extra.fromDialog
  undoBar.text = (result === 'forgot' && undoBar.requeued)
    ? '已标记遗忘，稍后本组末尾再考一次'
    : `已记录，${data.next_interval_days} 天后复习`
  undoTimer = setTimeout(() => { undoBar.show = false }, 5000)
}
async function undoLast() {
  const fromDialog = undoBar.fromDialog
  const statusKey = statusDialog.key
  clearTimeout(undoTimer)
  undoBar.show = false
  try {
    await reviewApi.undoGrade(undoBar.cardId)
    if (fromDialog) {
      // 弹窗场景撤销：卡已回到到期 → 全量刷新主队列与弹窗列表，保证两边口径一致
      await load()
      if (statusDialog.show) await openStatus(statusKey || 'due')
      ElMessage.success('已撤销上一次评分')
      return
    }
    const { data } = await reviewApi.undoGrade(undoBar.cardId)
    const i = queue.value.findIndex(x => x.id === undoBar.cardId)
    if (i >= 0) queue.value[i] = data
    if (undoBar.requeued) {
      // 撤掉 forgot 时补推的队尾重考副本
      const last = queue.value[queue.value.length - 1]
      if (last && last.id === undoBar.cardId) queue.value.pop()
    }
    idx.value = Math.max(0, idx.value - 1)   // 回到被误评的那张卡
    resetState()
    refreshStats()
    ElMessage.success('已撤销上一次评分')
  } catch (e) {
    ElMessage.error(errMsg(e, '撤销失败'))
  }
}

// 键盘快捷键：空格翻卡/作答，1-4 自评（下钻弹窗打开时不响应，避免误评主队列卡片）
function onKey(e) {
  if (statusDialog.show) return
  if (!current.value) return
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
  if (e.code === 'Space') { e.preventDefault(); reveal() }
  else if (e.key === '1') grade('forgot')
  else if (e.key === '2') grade('fuzzy')
  else if (e.key === '3') grade('easy')
  else if (e.key === '4') grade('simple')
}

const isWeak = (c) => c.review_count >= 2 && c.level <= 1

async function loadAll() {
  const { data } = await reviewApi.cards(filterParams())
  allCards.value = data
}
// 全部卡片列表：题干关键词搜索（纯前端过滤）
const allSearch = ref('')
const filteredCards = computed(() => {
  const kw = allSearch.value.trim()
  if (!kw) return allCards.value
  return allCards.value.filter(c => (c.question || '').includes(kw))
})

async function convertCard(c) {
  convertingId.value = c.id
  try {
    const { data } = await reviewApi.convert(c.id)
    Object.assign(c, data)
    ElMessage.success('已转成选择题')
  } catch (e) {
    ElMessage.error(errMsg(e, '转换失败'))
  } finally {
    convertingId.value = null
  }
}

const keepRate = computed(() => {
  const d = stats.value.daily || []
  const easy = d.reduce((s, x) => s + (x.easy || 0), 0)
  const total = d.reduce((s, x) => s + (x.count || 0), 0)
  return total ? Math.round(easy / total * 100) : 0
})
// 今日记得率（daily 末项即今天，北京口径由后端聚合）
const todayRate = computed(() => {
  const t = (stats.value.daily || [])[(stats.value.daily || []).length - 1]
  return t && t.count ? Math.round((t.easy || 0) / t.count * 100) : 0
})
const hasCurve = computed(() => (stats.value.forgetting_curve || []).some(f => f.rate != null))
const barHeight = (count) => {
  const d = stats.value.daily || []
  const max = Math.max(1, ...d.map(x => x.count || 0))
  return Math.max(count ? 8 : 2, Math.round((count || 0) / max * 56)) + 'px'
}
// 一周打卡轨迹：今天在最左，往右是更早的日期（连续打卡段从左往右延伸）
const streakDots = computed(() => {
  const daily = (stats.value.daily || []).map(d => ({
    date: d.date, active: (d.count || 0) > 0, count: d.count || 0, isToday: false,
  }))
  if (daily.length) daily[daily.length - 1].isToday = true
  return daily.reverse()
})

// 遗忘曲线 SVG 折线图数据（间隔 → 记得率）
const CURVE_W = 600, CURVE_H = 190, C_PAD_L = 46, C_PAD_R = 22, C_PAD_T = 26, C_PAD_B = 34
const curveView = computed(() => {
  const fc = stats.value.forgetting_curve || []
  if (!fc.length) return null
  const n = fc.length
  const slotW = (CURVE_W - C_PAD_L - C_PAD_R) / Math.max(1, n - 1)
  const plotH = CURVE_H - C_PAD_T - C_PAD_B
  const pts = fc.map((f, i) => ({
    x: C_PAD_L + i * slotW,
    y: f.rate == null ? null : C_PAD_T + (1 - f.rate / 100) * plotH,
    rate: f.rate, days: f.interval_days, total: f.total,
  }))
  const valid = pts.filter(p => p.y != null)
  let line = '', area = ''
  if (valid.length) {
    line = valid.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ')
    const base = (CURVE_H - C_PAD_B).toFixed(1)
    area = line + ' L ' + valid[valid.length - 1].x.toFixed(1) + ' ' + base
      + ' L ' + valid[0].x.toFixed(1) + ' ' + base + ' Z'
  }
  const maxRate = Math.max(100, ...valid.map(p => p.rate))
  // 参考线：不复习的自然遗忘（艾宾浩斯近似，R = e^(-t/2.5)），与「你的保持率」对比凸显复习价值
  const refLine = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p.x.toFixed(1) + ' '
    + (C_PAD_T + (1 - Math.exp(-p.days / 2.5)) * plotH).toFixed(1)).join(' ')
  return { pts, valid, line, area, plotH, slotW, base: CURVE_H - C_PAD_B, refLine }
})
// 曲线是否足以呈现趋势（至少 2 个有效间隔点）
const curveEnough = computed(() => curveView.value && curveView.value.valid.length >= 2)
// 屏幕阅读器摘要：描述曲线关键洞察
const curveSummary = computed(() => {
  const v = curveView.value
  if (!v || !v.valid.length) return '暂无遗忘曲线数据'
  const first = v.valid[0], last = v.valid[v.valid.length - 1]
  const diff = last.rate - first.rate
  const trend = diff < -10 ? '随间隔拉长，记得率下降' : diff > 10 ? '随间隔拉长，记得率上升' : '随间隔拉长，记得率保持稳定'
  return `遗忘曲线：${first.days} 天间隔记得率 ${first.rate}%，${last.days} 天间隔记得率 ${last.rate}%，${trend}`
})
// 一句话结论：把曲线翻译成用户能懂的判断 + 行动建议
const curveTakeaway = computed(() => {
  const v = curveView.value
  if (!v) return ''
  // 仅用样本充足的间隔档（≥3 张卡）下结论，避免 1 张卡决定 0%/100% 的误导
  const reliable = v.valid.filter(p => (p.total || 0) >= 3)
  if (reliable.length < 2) return '样本还比较少，多复习几天后给你更准的解读'
  const first = reliable[0], last = reliable[reliable.length - 1]
  const diff = last.rate - first.rate
  if (last.rate < 50) return `间隔 ${last.days} 天时只记得 ${last.rate}% —— 长间隔卡片忘得多，答题时选「模糊 / 忘了」降档巩固`
  if (diff >= -15) return `间隔 ${last.days} 天你还记得 ${last.rate}% —— 曲线平缓 = 记忆已固化，保持当前节奏就好`
  return `间隔拉到 ${last.days} 天时记得率降到 ${last.rate}% —— 长间隔卡片建议选「模糊」回退一档`
})

async function openStatus(status) {
  statusDialog.show = true
  statusDialog.key = status
  statusDialog.title = STATUS_LABELS[status] || '卡片'
  statusDialog.loading = true
  statusCards.value = []
  expandedId.value = null
  try {
    const { data } = await reviewApi.cardsByStatus(status, filterParams())
    statusCards.value = data.map(c => ({ ...c, _picked: undefined, _revealed: false }))
  } catch (e) {
    ElMessage.error(errMsg(e, '加载失败'))
  } finally {
    statusDialog.loading = false
  }
}

function toggleExpand(c) {
  if (expandedId.value === c.id) {
    expandedId.value = null
  } else {
    expandedId.value = c.id
    c._picked = undefined
    c._revealed = false
  }
}

function pickStatusOpt(c, i) {
  if (c._picked !== undefined) return   // 已作答
  c._picked = i
}

function statusRevealed(c) {
  return c.type === 'choice' ? c._picked !== undefined : !!c._revealed
}

async function gradeStatus(c, result) {
  try {
    const { data } = await reviewApi.grade(c.id, result)
    showUndo(c, data, result, { requeued: false, fromDialog: true })   // 弹窗里不重入队，仍可撤销
    statusCards.value = statusCards.value.filter(x => x.id !== c.id)
    if (expandedId.value === c.id) expandedId.value = null
    // 主队列快照同步：该卡已被提前评完 → 从快照移除并校准序号（勿用 load() 重置，会打断主流程进度）
    const qi = queue.value.findIndex(x => x.id === c.id)
    if (qi >= 0) {
      queue.value.splice(qi, 1)
      if (qi < idx.value) idx.value -= 1
    }
    refreshStats()
    loadAll()
  } catch (e) {
    ElMessage.error(errMsg(e, '记录失败'))
  }
}

function openEdit(c) {
  editDialog.id = c.id
  editDialog.question = c.question
  editDialog.answer = c.answer || ''
  editDialog.type = c.type
  editDialog.options = [...(c.options || [])]
  editDialog.correct_index = c.correct_index ?? 0
  editDialog.show = true
}
async function saveEdit() {
  editSaving.value = true
  try {
    const payload = { question: editDialog.question, answer: editDialog.answer }
    if (editDialog.type === 'choice') {
      payload.options = editDialog.options
      payload.correct_index = editDialog.correct_index
    }
    const { data } = await reviewApi.update(editDialog.id, payload)
    const upd = (list) => { const i = list.findIndex(x => x.id === data.id); if (i >= 0) list[i] = data }
    upd(allCards.value); upd(queue.value)
    editDialog.show = false
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    editSaving.value = false
  }
}

async function removeCard(id) {
  await reviewApi.remove(id)
  allCards.value = allCards.value.filter(c => c.id !== id)
  stats.value.total -= 1
  ElMessage.success('已删除')
}

onMounted(async () => {
  await load()
  loadAll()
  loadFilters()
  document.addEventListener('keydown', onKey)
})
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<style scoped>
/* ===== 页头：标题在左、统计在右，靠留白分层（去掉分割线，减少线框感） ===== */
.page-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 18px; flex-wrap: wrap; margin-bottom: 22px; }
.page-header h2 { margin: 0; font-size: 21px; font-weight: 700; letter-spacing: .2px; }
.header-title { display: flex; flex-direction: column; gap: 5px; }
.header-sub { margin: 0; font-size: 13px; color: var(--asc-text-3); }

/* ===== 统计块：大数字 + 小标签，轻边框浅投影，hover 浮起可下钻 ===== */
.stats-row { display: flex; gap: 10px; flex-wrap: wrap; }
.stat-chip {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  min-width: 78px; padding: 9px 16px 8px;
  background: var(--asc-card); border: 1px solid rgba(227, 227, 230, .7);
  border-radius: 12px; box-shadow: 0 1px 2px rgba(31, 24, 68, .04);
}
.stat-chip b { font-size: 19px; font-weight: 700; line-height: 1.25; color: var(--asc-text); }
.stat-chip i { font-style: normal; font-size: 11px; color: var(--asc-text-3); }
.stat-chip.due { background: linear-gradient(150deg, rgba(124, 92, 252, .12), rgba(124, 92, 252, .04)); border-color: rgba(124, 92, 252, .28); }
.stat-chip.due b { color: var(--asc-primary); }
.stat-chip.mastered b { color: #0f6e56; }
.stat-chip.weak b { color: #d24e4e; }
.stat-chip.clickable { cursor: pointer; transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease; }
.stat-chip.clickable:hover { transform: translateY(-2px); border-color: rgba(124, 92, 252, .45); box-shadow: var(--asc-shadow-hover); }

/* ===== 筛选栏：无边框浮层 ===== */
.filter-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 16px; margin-bottom: 18px;
  background: var(--asc-card); border-radius: 12px;
  box-shadow: 0 1px 2px rgba(31, 24, 68, .04);
}
.filter-label { font-size: 12px; color: var(--asc-text-3); }
.filter-count { font-size: 12px; color: var(--asc-text-2); margin-left: 4px; }

/* ===== 统计下钻弹窗 ===== */
.status-list { max-height: 62vh; overflow-y: auto; }
.status-item { border: 1px solid rgba(227, 227, 230, .7); border-radius: 12px; padding: 12px 16px; margin-bottom: 10px; transition: border-color .15s, box-shadow .15s; }
.status-item:hover { box-shadow: 0 2px 10px rgba(31, 24, 68, .05); }
.status-item.expanded { border-color: rgba(124, 92, 252, .5); }
.status-q { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; font-weight: 500; margin-bottom: 6px; line-height: 1.5; }
.status-q-text { flex: 1; }
.status-q-arrow { flex-shrink: 0; font-size: 11px; font-weight: 400; color: var(--asc-primary); }
.status-meta { display: flex; gap: 10px; align-items: center; font-size: 12px; color: var(--asc-text-3); flex-wrap: wrap; }
.status-review { margin-top: 10px; padding-top: 12px; border-top: 1px dashed var(--asc-border); }
.status-opts { display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; }
.status-opt {
  display: flex; align-items: center; gap: 10px; padding: 9px 12px; border-radius: 8px;
  background: var(--asc-surface-2); border: 1px solid transparent; cursor: pointer; transition: all .15s;
}
.status-opt:hover { background: var(--asc-primary-soft); border-color: var(--asc-primary); }
.status-opt.correct { background: rgba(15, 110, 86, .08); border-color: #0f6e56; }
.status-opt.wrong { background: rgba(210, 78, 78, .08); border-color: #d24e4e; }
.status-opt .opt-text { flex: 1; }
.opt-mark.wrong-mark { color: #d24e4e; }
.status-qa-hint { text-align: center; padding: 6px 0; }
.status-grade { display: flex; gap: 8px; margin-top: 10px; }
.status-grade .grade-btn { flex: 1; height: auto; padding: 8px 4px; }

/* ===== 今日完成：渐变底 + 大卡 ===== */
.all-done {
  text-align: center; padding: 56px 24px;
  background: linear-gradient(165deg, #ffffff 30%, rgba(124, 92, 252, .05));
  border: 1px solid rgba(227, 227, 230, .6); border-radius: 18px;
  box-shadow: 0 8px 28px rgba(31, 24, 68, .06);
}
.done-mascot { width: 104px; height: 104px; margin: 0 auto 18px; animation: mascot-float 3.6s ease-in-out infinite; }
@keyframes mascot-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-6px); } }
:deep(.el-empty__image) { border-radius: 16px; }
.done-title { font-size: 18px; font-weight: 700; margin: 0 0 6px; }
.done-sub { font-size: 13px; color: var(--asc-text-3); margin: 0 0 20px; }

/* ===== 复习卡舞台：整卡白底大卡，进度条贴顶融入卡片 ===== */
.card-stage {
  width: 100%; overflow: hidden;
  background: var(--asc-card); border-radius: 18px;
  border: 1px solid rgba(227, 227, 230, .6);
  box-shadow: 0 8px 28px rgba(31, 24, 68, .07);
  padding: 0 26px 24px;
}
.stage-progress {
  height: 6px; margin: 16px 0 18px; border-radius: 3px; overflow: hidden;
  background: var(--asc-surface-2);
}
.stage-progress-fill {
  height: 100%; border-radius: 3px;
  background: linear-gradient(90deg, #8f7bff, #7c5cfc);
  transition: width .4s cubic-bezier(.4, 0, .2, 1);
}
.card-meta-row { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
.card-src { font-size: 12px; color: var(--asc-primary); background: var(--asc-primary-soft); border-radius: 6px; padding: 2px 8px; }
.card-type { font-size: 11px; color: var(--asc-text-2); }
.card-level { font-size: 11px; color: var(--asc-text-3); margin-left: auto; }

/* 题干：浅紫渐变焦点区，无边框、无投影，与选项拉开层级 */
.card-question {
  background: linear-gradient(140deg, rgba(124, 92, 252, .07), rgba(143, 123, 255, .025));
  border-radius: 14px; padding: 18px 20px; margin-bottom: 18px;
}
.q-ask { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.q-mascot { width: 24px; height: 24px; border-radius: 7px; background: var(--asc-card); padding: 1px; box-shadow: 0 1px 3px rgba(124, 92, 252, .2); }
.q-ask-label { font-size: 12px; color: var(--asc-primary); font-weight: 600; letter-spacing: .5px; }
.q-text { font-size: 19px; font-weight: 600; line-height: 1.75; color: var(--asc-text); }

/* 选择题选项 */
.choice-opts { display: flex; flex-direction: column; gap: 10px; }
.opt {
  display: flex; align-items: center; gap: 12px;
  background: var(--asc-card); border: 1px solid rgba(227, 227, 230, .8);
  border-radius: 12px; padding: 14px 16px; cursor: pointer;
  transition: all .18s cubic-bezier(.4, 0, .2, 1);
}
.opt:not(.correct):not(.wrong):hover {
  border-color: var(--asc-primary); background: var(--asc-primary-soft);
  transform: translateX(4px); box-shadow: 0 4px 12px rgba(124, 92, 252, .12);
}
.opt.correct { border-color: #0f6e56; background: rgba(15, 110, 86, .07); }
.opt.wrong { border-color: #d24e4e; background: rgba(210, 78, 78, .07); animation: shake .4s ease; }
.opt-key {
  width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0;
  background: var(--asc-surface-2); color: var(--asc-text-2);
  font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center;
  transition: all .18s;
}
.opt:not(.correct):not(.wrong):hover .opt-key { background: var(--asc-primary); color: #fff; }
.opt.correct .opt-key { background: #0f6e56; color: #fff; }
.opt.wrong .opt-key { background: #d24e4e; color: #fff; }
.opt-text { font-size: 14.5px; line-height: 1.55; flex: 1; }
.opt-mark { font-size: 17px; font-weight: 700; color: #0f6e56; animation: mark-in .25s ease; }
.opt-mark.wrong-mark { color: #d24e4e; }
@keyframes mark-in { from { transform: scale(.4); opacity: 0; } to { transform: scale(1); opacity: 1; } }
@keyframes shake { 0%, 100% { transform: translateX(0); } 25% { transform: translateX(-4px); } 75% { transform: translateX(4px); } }

/* 简答/复述卡答案区：灰底与白卡区分 */
.qa-body { margin-bottom: 6px; }
.qa-answer {
  background: var(--asc-surface-2);
  border-radius: 12px; padding: 16px 18px; font-size: 14px; line-height: 1.8;
  animation: answer-in .28s cubic-bezier(.4, 0, .2, 1);
}
@keyframes answer-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.qa-hint { text-align: center; font-size: 12px; color: var(--asc-text-3); padding: 22px 0; }

/* 解析 */
.explain {
  margin-top: 16px; background: var(--asc-surface-2);
  border-radius: 12px; padding: 14px 18px; font-size: 13.5px; line-height: 1.7;
}
.explain-label {
  font-size: 11px; color: var(--asc-primary); font-weight: 600;
  letter-spacing: 1.5px; margin-bottom: 6px;
}
.md-body :deep(p) { margin: 4px 0; }
.md-body :deep(ul), .md-body :deep(ol) { padding-left: 20px; margin: 4px 0; }
.md-body :deep(code) { background: rgba(255, 255, 255, .7); padding: 1px 5px; border-radius: 4px; font-size: 12px; }

.reveal-hint { text-align: center; margin-top: 12px; }

/* 自评按钮：语义色浅底填充，实体按键感 */
.grade-row { display: flex; gap: 10px; margin-top: 22px; }
.grade-btn {
  flex: 1; height: 62px; display: flex; flex-direction: column; gap: 3px;
  font-size: 15px; font-weight: 600; border-radius: 12px; border: none;
  transition: all .18s cubic-bezier(.4, 0, .2, 1);
}
.grade-btn small { font-size: 11px; font-weight: 400; opacity: .68; }
.grade-btn:hover { transform: translateY(-2px); }
.grade-btn:active { transform: translateY(0); }
.grade-btn.forgot { color: #c04545; background: rgba(210, 78, 78, .08); }
.grade-btn.forgot:hover { background: rgba(210, 78, 78, .15); }
.grade-btn.fuzzy { color: #a5660f; background: rgba(186, 117, 23, .09); }
.grade-btn.fuzzy:hover { background: rgba(186, 117, 23, .16); }
.grade-btn.easy { color: #0f6e56; background: rgba(15, 110, 86, .09); }
.grade-btn.easy:hover { background: rgba(15, 110, 86, .16); }
.grade-btn.simple { color: #185fa5; background: rgba(24, 95, 165, .09); }
.grade-btn.simple:hover { background: rgba(24, 95, 165, .16); }
.queue-progress { text-align: center; font-size: 12px; color: var(--asc-text-3); margin-top: 16px; }

/* ===== 全部卡片列表 ===== */
.all-list { max-width: 760px; margin: 26px auto 0; }
.all-item {
  background: var(--asc-card); border: 1px solid rgba(227, 227, 230, .6);
  border-radius: 12px; padding: 14px 18px; margin-bottom: 10px;
  transition: box-shadow .16s ease, transform .16s ease;
}
.all-item:hover { box-shadow: var(--asc-shadow-hover); transform: translateY(-1px); }
.all-q { font-size: 14px; font-weight: 500; margin-bottom: 6px; }
.all-meta { display: flex; gap: 12px; align-items: center; font-size: 12px; color: var(--asc-text-3); flex-wrap: wrap; }

/* ===== 左右两栏布局：左=数据洞察，右=问答测试题（自然高度） ===== */
.review-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: start; }
.review-col { min-width: 0; }
.insight-card {
  background: var(--asc-card); border-radius: 16px;
  border: 1px solid rgba(227, 227, 230, .6);
  box-shadow: 0 4px 20px rgba(31, 24, 68, .05);
  padding: 20px 22px 22px;
  display: flex; flex-direction: column; gap: 22px;
}
.insight-sec { display: flex; flex-direction: column; gap: 12px; }
.sec-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; font-weight: 600; color: var(--asc-text-2); letter-spacing: 1px;
}
.sec-title::before { content: ''; width: 3px; height: 12px; border-radius: 2px; background: var(--asc-primary); flex-shrink: 0; }

/* ===== 打卡里程碑 ===== */
.streak-row { display: flex; align-items: center; gap: 16px; }
.streak-flame {
  width: 60px; height: 60px; border-radius: 18px; flex-shrink: 0;
  background: var(--asc-surface-2); display: flex; align-items: center; justify-content: center;
  position: relative; transition: all .3s ease;
}
.streak-flame.lit {
  background: linear-gradient(135deg, rgba(124, 92, 252, .14), rgba(240, 171, 252, .10));
  box-shadow: 0 4px 18px rgba(124, 92, 252, .28);
}
.streak-flame.lit::after {
  content: ''; position: absolute; inset: 8px; border-radius: 12px;
  background: radial-gradient(circle, rgba(124, 92, 252, .22), transparent 70%);
}
.streak-flame.lit svg { animation: flame-flicker 2s ease-in-out infinite; transform-origin: 50% 88%; position: relative; z-index: 1; }
@keyframes flame-flicker { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.07) translateY(-1.5px); } }
.streak-main { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.streak-num { font-size: 14px; color: var(--asc-text-2); }
.streak-num b { color: var(--asc-primary); font-size: 22px; font-weight: 700; margin: 0 2px; }
.streak-dots { display: flex; align-items: center; }
.streak-dot {
  width: 24px; height: 24px; border-radius: 50%; flex-shrink: 0;
  border: 2px solid var(--asc-border); background: var(--asc-card);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; color: #fff; transition: all .3s cubic-bezier(.4, 0, .2, 1);
}
.streak-dot.active {
  background: linear-gradient(135deg, #8f7bff, #7c5cfc);
  border-color: #7c5cfc;
  box-shadow: 0 2px 6px rgba(124, 92, 252, .35);
  animation: dot-pop .3s ease;
}
.streak-dot.today { box-shadow: 0 0 0 4px var(--asc-primary-soft); }
.dot-connector { width: 10px; height: 2px; border-radius: 1px; flex-shrink: 0; background: var(--asc-border); transition: background .3s ease; }
.dot-connector.filled { background: var(--asc-primary); }
.dot-check { font-weight: 700; line-height: 1; }
@keyframes dot-pop { from { transform: scale(.4); } to { transform: scale(1); } }
.streak-rate {
  display: flex; flex-direction: column; align-items: flex-end; gap: 2px;
  padding-left: 16px; border-left: 1px solid var(--asc-divider); flex-shrink: 0;
}
.rate-label { font-size: 11px; color: var(--asc-text-3); }
.rate-num { font-size: 22px; font-weight: 700; color: #0f6e56; line-height: 1; }
.rate-num i { font-style: normal; font-size: 13px; font-weight: 500; margin-left: 1px; }

/* ===== 近 7 天复习量柱状图 ===== */
.bar-chart { display: flex; gap: 8px; align-items: flex-end; height: 84px; padding-top: 4px; }
.bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.bar-track { height: 52px; width: 100%; display: flex; align-items: flex-end; justify-content: center; }
.bar {
  width: 56%; background: rgba(124, 92, 252, .16); border-radius: 5px 5px 2px 2px;
  min-height: 4px; transition: all .25s ease;
}
.bar:hover { background: rgba(124, 92, 252, .32); }
.bar.today, .bar.today:hover { background: linear-gradient(180deg, #8f7bff, #7c5cfc); box-shadow: 0 3px 10px rgba(124, 92, 252, .3); }
.bar-count { font-size: 11px; color: var(--asc-text-2); }
.bar-label { font-size: 10px; color: var(--asc-text-3); }

/* ===== 遗忘曲线（SVG 折线） ===== */
.curve-conclusion { margin: -4px 0 10px; font-size: 12.5px; color: var(--asc-text-2); line-height: 1.6; }
.curve-svg { display: block; width: 100%; height: auto; aspect-ratio: 600 / 190; }
.curve-grid { stroke: var(--asc-divider); stroke-width: 1; }
.curve-grid.mid { stroke: var(--asc-border); stroke-dasharray: 3 3; }
.curve-axis { font-size: 10px; fill: var(--asc-text-3); }
.curve-area { fill: url(#curve-fill); }
.curve-ref { fill: none; stroke: #b9b9c0; stroke-width: 1.5; stroke-dasharray: 4 4; }
.curve-line { fill: none; stroke: #0f6e56; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
.curve-dot { fill: #0f6e56; stroke: #fff; stroke-width: 2; }
.curve-val { font-size: 11px; font-weight: 700; fill: #0f6e56; }
.curve-val.dim { fill: var(--asc-text-3); font-weight: 400; }
.curve-x { font-size: 10.5px; fill: var(--asc-text-3); }
.curve-legend { display: flex; gap: 14px; align-items: center; margin-top: 8px; font-size: 11px; color: var(--asc-text-3); }
.legend-item { display: inline-flex; align-items: center; gap: 5px; }
.lg { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
.lg-you { background: #0f6e56; }
.lg-ref { background: repeating-linear-gradient(90deg, #b9b9c0 0 4px, transparent 4px 7px); }
.legend-axis { margin-left: auto; }
.curve-empty {
  text-align: center; font-size: 12.5px; color: var(--asc-text-3);
  padding: 28px 0; background: var(--asc-surface-2);
  border-radius: 10px; line-height: 1.6;
}
.curve-dot.low { fill: #fff; stroke: rgba(15, 110, 86, .5); stroke-width: 1.5; }

/* ===== 等级进度点（共 6 级） ===== */
.lv-dots { display: inline-flex; gap: 3px; align-items: center; vertical-align: 1px; }
.lv-dots i { width: 7px; height: 7px; border-radius: 50%; background: var(--asc-border); transition: background .2s ease; }
.lv-dots i.on { background: linear-gradient(135deg, #8f7bff, #7c5cfc); }

/* ===== 快捷键提示 ===== */
.kbd-hint { text-align: center; font-size: 11px; color: var(--asc-text-3); margin-top: 10px; }
.kbd {
  display: inline-block; min-width: 16px; padding: 0 4px; margin: 0 1px 0 6px;
  font-size: 10px; line-height: 16px; text-align: center;
  background: var(--asc-surface-2); border: 1px solid var(--asc-border);
  border-bottom-width: 2px; border-radius: 4px; color: var(--asc-text-2);
}
.kbd:first-of-type { margin-left: 0; }
.kbd-hint .kbd + .kbd { margin-left: 4px; }

/* ===== 完成页小结 ===== */
.done-stats { font-size: 13px; color: var(--asc-primary); font-weight: 600; margin: -8px 0 20px; font-variant-numeric: tabular-nums; }

/* ===== 全部卡片列表（搜索头） ===== */
.all-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; padding: 0 2px; }
.all-title { font-size: 13px; font-weight: 600; color: var(--asc-text-2); }
.all-empty { text-align: center; padding: 26px 0; font-size: 12.5px; color: var(--asc-text-3); background: var(--asc-card); border: 1px dashed var(--asc-border); border-radius: 12px; }

/* ===== 评分撤销条（悬浮底部） ===== */
.undo-bar {
  position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%);
  z-index: 3000;
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px 9px 14px; border-radius: 12px;
  background: #2b2b33; color: #fff; font-size: 12.5px;
  box-shadow: 0 8px 26px rgba(0, 0, 0, .22);
}
.undo-bar svg { opacity: .8; flex-shrink: 0; }
.undo-btn {
  border: none; background: rgba(255, 255, 255, .16); color: #fff;
  font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 7px;
  cursor: pointer; transition: background .15s ease;
}
.undo-btn:hover { background: var(--asc-primary); }
.undo-fade-enter-active, .undo-fade-leave-active { transition: opacity .22s ease, transform .22s ease; }
.undo-fade-enter-from, .undo-fade-leave-to { opacity: 0; transform: translate(-50%, 8px); }

.opt-edit-row { display: flex; gap: 12px; align-items: center; width: 100%; }
.opt-edit-row .el-input { flex: 1; }

/* ===== UX 细节：数据数字等宽，避免刷新时宽度跳动 ===== */
.streak-num b, .rate-num, .curve-val, .bar-count, .stat-chip b {
  font-variant-numeric: tabular-nums;
}

/* ===== 响应式：窄屏回到单列堆叠 ===== */
@media (max-width: 1024px) {
  .review-grid { grid-template-columns: 1fr; }
  .card-stage { max-width: 560px; margin: 0 auto; }
}

/* ===== 无障碍：尊重系统「减少动态」偏好 ===== */
@media (prefers-reduced-motion: reduce) {
  .streak-flame.lit svg,
  .done-mascot,
  .qa-answer,
  .opt-mark,
  .opt.wrong,
  .streak-dot.active {
    animation: none !important;
  }
  .stage-progress-fill { transition: none; }
  .opt, .grade-btn, .stat-chip.clickable { transition: none; }
}
</style>
