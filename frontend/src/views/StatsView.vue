<template>
  <div class="page stats-page">
    <PageHead title="数据统计" sub="你的学习成果，AI 来帮你分析 · 数据实时聚合自本机行为">
      <template #icon>
        <svg viewBox="0 0 16 16" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round">
          <path d="M3 13h2V8H3v5zM7 13h2V3H7v10zM11 13h2V6h-2v7z" />
        </svg>
      </template>
    </PageHead>

    <!-- L1 概览层 -->
    <div class="stats-hero" v-loading="loading">
      <div class="streak-card">
        <div class="streak-label">连续学习天数</div>
        <div class="streak-num">{{ ov.streak || 0 }}</div>
        <div class="streak-unit">天</div>
        <div class="streak-dots">
          <span v-for="(d, i) in recentActive" :key="d.date" class="sdot"
            :class="{ on: d.active, today: i === recentActive.length - 1 }" :title="d.date"></span>
        </div>
        <div class="streak-dots-label">近 14 天活跃轨迹</div>
      </div>
      <div class="kpi-wrap">
        <div class="kpi-group">
          <div class="kpi-group-label">今日</div>
          <div class="kpi-grid">
            <div class="kpi-card"><b>{{ fmtHours(ov.today?.duration_seconds) }}</b><span>学习时长</span></div>
            <div class="kpi-card"><b>{{ ov.today?.note_count ?? 0 }}</b><span>新增笔记</span></div>
            <div class="kpi-card"><b>{{ ov.today?.review_count ?? 0 }}</b><span>已复习</span></div>
            <div class="kpi-card"><b>{{ ov.today?.hit_rate ?? 0 }}%</b><span>问答命中</span></div>
          </div>
        </div>
        <div class="kpi-group">
          <div class="kpi-group-label">本周</div>
          <div class="kpi-grid">
            <div class="kpi-card"><b>{{ ov.week?.days ?? 0 }}</b><span>学习天数</span></div>
            <div class="kpi-card"><b>{{ fmtHours(ov.week?.duration_seconds) }}</b><span>总时长</span></div>
            <div class="kpi-card"><b>{{ ov.week?.note_count ?? 0 }}</b><span>新增笔记</span></div>
            <div class="kpi-card"><b>{{ ov.week?.qa_count ?? 0 }}</b><span>问答量</span></div>
          </div>
        </div>
        <div class="kpi-group">
          <div class="kpi-group-label">累计</div>
          <div class="kpi-grid">
            <div class="kpi-card"><b>{{ ov.total?.material_count ?? 0 }}</b><span>学习材料</span></div>
            <div class="kpi-card"><b>{{ ov.total?.note_count ?? 0 }}</b><span>笔记</span></div>
            <div class="kpi-card"><b>{{ ov.total?.review_card_count ?? 0 }}</b><span>复习卡</span></div>
            <div class="kpi-card"><b>{{ ov.total?.qa_count ?? 0 }}</b><span>问答</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- L2 维度层 -->
    <div class="dim-tabs">
      <div v-for="t in dims" :key="t.key" class="dim-tab"
        :class="{ active: activeDim === t.key }" @click="activeDim = t.key">{{ t.label }}</div>
    </div>

    <!-- 学习投入 -->
    <div v-show="activeDim === 'invest'" class="dim-pane">
      <div class="chart-row">
        <div class="chart-card">
          <h4>学习时长趋势</h4>
          <div class="chart-sub">近 14 天 · 单位：分钟</div>
          <LineChart v-if="investLine" :pts="investLine" ylabel="分钟" />
          <el-empty v-else description="暂无学习时长数据" :image-size="60" />
        </div>
        <div class="chart-card">
          <h4>材料分布</h4>
          <div class="chart-sub">按格式归类 · 共 {{ ov.total?.material_count ?? 0 }} 份</div>
          <FormatRose v-if="formats.length" :items="formats" />
          <el-empty v-else description="暂无材料" :image-size="60" />
        </div>
      </div>
    </div>

    <!-- 内容沉淀 -->
    <div v-show="activeDim === 'sediment'" class="dim-pane">
      <div class="chart-row">
        <div class="chart-card">
          <h4>笔记周趋势</h4>
          <div class="chart-sub">近 8 周 · 新增条数</div>
          <VBars v-if="noteWeekly.length" :items="noteWeekly" />
          <el-empty v-else description="暂无笔记数据" :image-size="60" />
        </div>
        <div class="chart-card">
          <h4>笔记来源构成</h4>
          <div class="chart-sub">AI 转笔记 vs 手动笔记 · 转笔记率 {{ ov.dimensions?.sediment?.convert_rate ?? 0 }}%</div>
          <Donut v-if="noteSource.total" :a="noteSource.ai" :b="noteSource.manual" label-a="AI 转笔记" label-b="手动笔记" />
          <div class="mini-stat" v-if="(ov.dimensions?.sediment?.highlight_count ?? 0) > 0">
            主动划线 <b>{{ ov.dimensions.sediment.highlight_count }}</b> 处
          </div>        </div>
      </div>
    </div>

    <!-- 知识调用 -->
    <div v-show="activeDim === 'recall'" class="dim-pane">
      <div class="chart-row">
        <div class="chart-card">
          <h4>问答量趋势</h4>
          <div class="chart-sub">近 14 天 · 每日提问数</div>
          <VBars v-if="qaDaily.length" :items="qaDaily" />
          <el-empty v-else description="暂无问答数据" :image-size="60" />
        </div>
        <div class="chart-card">
          <h4>知识库命中率</h4>
          <div class="chart-sub">本周 {{ ov.dimensions?.recall?.week_hit_rate ?? 0 }}% · 累计 {{ ov.dimensions?.recall?.hit_rate ?? 0 }}%</div>
          <Donut v-if="hitRate > 0" :a="hitRate" :b="100 - hitRate" label-a="命中" label-b="通用补充" />
          <div v-else-if="hitAbnormal" class="hit-warn">
            问答 {{ ov.total?.qa_count ?? 0 }} 次但知识库命中异常，建议检查向量模型配置或回填知识库
          </div>
          <el-empty v-else description="暂无问答数据" :image-size="60" />
        </div>
      </div>
    </div>

    <!-- 复习记忆 -->
    <div v-show="activeDim === 'memory'" class="dim-pane">
      <div class="chart-row">
        <div class="chart-card">
          <h4>遗忘曲线</h4>
          <div class="chart-sub">按复习间隔档位统计记得率</div>
          <LineChart v-if="curveLine" :pts="curveLine" ylabel="记得率 %" />
          <el-empty v-else description="暂无遗忘曲线数据" :image-size="60" />
        </div>
        <div class="chart-card">
          <h4>复习概览</h4>
          <div class="chart-sub">卡片状态 + 自评分布 · 共复习 {{ gradeTotal }} 次</div>
          <div class="mem-grid">
            <div class="mem-cell"><b>{{ reviewStats.total ?? 0 }}</b><span>卡片总数</span></div>
            <div class="mem-cell"><b class="accent">{{ reviewStats.due ?? 0 }}</b><span>待复习</span></div>
            <div class="mem-cell"><b class="green">{{ reviewStats.mastered ?? 0 }}</b><span>已掌握</span></div>
            <div class="mem-cell"><b class="red">{{ reviewStats.weak ?? 0 }}</b><span>易忘</span></div>
          </div>
          <div class="mem-grade" v-if="gradeTotal">
            <div class="grade-bar">
              <span class="g-easy" :style="{ width: gradePct.easy + '%' }"></span>
              <span class="g-fuzzy" :style="{ width: gradePct.fuzzy + '%' }"></span>
              <span class="g-forgot" :style="{ width: gradePct.forgot + '%' }"></span>
            </div>
            <div class="grade-legend">
              <span><i class="dot" style="background:#639922"></i>记得/简单 <b>{{ gradePct.easy }}%</b></span>
              <span><i class="dot" style="background:#d97706"></i>模糊 <b>{{ gradePct.fuzzy }}%</b></span>
              <span><i class="dot" style="background:#d24e4e"></i>忘了 <b>{{ gradePct.forgot }}%</b></span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- L3 AI 分析报告 -->
    <div class="report-section">
      <div class="report-head">
        <div>
          <h3>AI 分析报告</h3>
          <div class="report-desc">规则引擎诊断 + AI 生成解释与建议 · 每句结论可溯源</div>
        </div>
        <el-button type="primary" :loading="reporting" @click="generateReport">✨ 生成 AI 分析报告</el-button>
      </div>
      <div class="report-card">
        <div v-if="reporting && !streamReport.content" class="report-loading"><span class="spinner"></span> AI 正在分析你的学习数据…</div>
        <el-empty v-else-if="!currentReport && !reporting" description="点击右侧「生成 AI 分析报告」，基于你的学习数据生成专属诊断" :image-size="80" />
        <div v-else class="report-body" :class="{ streaming: reporting }">
          <div class="report-meta">
            <span>{{ displayReport.title }}</span>
            <span>{{ issueTitles(displayReport.issues) || (reporting ? '诊断中…' : '无异常') }}</span>
          </div>
          <div class="report-diag" v-if="(displayReport.issues || []).length">
            <div v-for="(it, idx) in displayReport.issues" :key="idx" class="diag-chip">
              <span class="diag-chip-tag">{{ it.title }}</span>
              <span class="diag-chip-detail">{{ it.detail }}</span>
            </div>
          </div>
          <div class="md-preview" v-html="renderedReport"></div>
          <span v-if="reporting" class="stream-cursor"></span>

          <!-- 转笔记：把这份 AI 报告沉淀成笔记（材料无关，落知识库「按笔记」） -->
          <div class="report-ops" v-if="!reporting && currentReport">
            <el-button v-if="!weeklyNote" size="small" text type="primary"
              :disabled="noteBusy" @click="reportToNote">转笔记</el-button>
            <template v-else>
              <el-button size="small" text type="success"
                @click="openWeeklyNote(weeklyNote.id)">✓ 已转笔记 · 查看</el-button>
              <el-button v-if="weeklyStale" size="small" text type="warning"
                :disabled="noteBusy" @click="reportToNote">报告已更新 · 更新笔记</el-button>
            </template>
          </div>
        </div>
      </div>
      <div class="report-history" v-if="history.length">
        <h4>历史报告</h4>
        <div v-for="r in history" :key="r.id" class="history-item" @click="openReport(r)">
          <span class="h-title">{{ r.title }}</span>
          <span class="h-meta">{{ issueTitles(r.issues) || '无异常' }}</span>
        </div>
      </div>
    </div>
  </div>
    <!-- 笔记查看弹窗：转笔记后「查看」在当前页打开，不再跳转知识库 -->
    <NoteEditorDialog v-model="noteView.show" :note-id="noteView.id" @changed="loadAiNotes" />
</template>

<script setup>
import { ref, reactive, computed, onMounted, h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { statsApi, reviewApi, noteApi } from '../api'
import { errMsg } from '../api/http'
import NoteEditorDialog from '../components/NoteEditorDialog.vue'
import { streamSSE } from '../utils/sse'
import PageHead from '../components/PageHead.vue'

const md = new MarkdownIt({ breaks: true })

// ---------- 子组件：折线图 / 柱状图 / 环形图 ----------
const LineChart = {
  props: { pts: Array, ylabel: String },
  setup(props) {
    return () => {
      const W = 560, H = 210, L = 44, R = 18, T = 24, B = 36
      const valid = props.pts.filter(p => p.y != null)
      if (!valid.length) return h('div')
      const n = props.pts.length
      const slotW = (W - L - R) / Math.max(1, n - 1)
      const plotH = H - T - B
      const max = Math.max(1, ...valid.map(p => p.y))
      const px = i => L + i * slotW
      const py = y => T + (1 - y / max) * plotH
      const xEvery = n > 10 ? 3 : (n > 6 ? 2 : 1)   // 标签跳隔，避免重叠堆积
      const line = valid.map((p, i) => (i === 0 ? 'M' : 'L') + px(p.i).toFixed(1) + ' ' + py(p.y).toFixed(1)).join(' ')
      const area = line + ' L ' + px(valid[valid.length - 1].i).toFixed(1) + ' ' + (H - B)
        + ' L ' + px(valid[0].i).toFixed(1) + ' ' + (H - B) + ' Z'
      // 浅色水平网格线（25% / 50% / 75% 三档）
      const gridYs = [0.25, 0.5, 0.75].map(f => T + (1 - f) * plotH)
      return h('svg', { class: 'line-svg', viewBox: `0 0 ${W} ${H}`, role: 'img' }, [
        h('defs', [h('linearGradient', { id: 'lg1', x1: 0, y1: 0, x2: 0, y2: 1 }, [
          h('stop', { offset: '0%', 'stop-color': '#7c5cfc', 'stop-opacity': 0.14 }),
          h('stop', { offset: '100%', 'stop-color': '#7c5cfc', 'stop-opacity': 0 }),
        ])]),
        ...gridYs.map(y => h('line', { x1: L, x2: W - R, y1: y, y2: y, stroke: '#eeeef2', 'stroke-width': 1, 'stroke-dasharray': '3 5' })),
        h('line', { x1: L, x2: W - R, y1: H - B, y2: H - B, stroke: '#e2e2e8', 'stroke-width': 1 }),
        h('path', { d: area, fill: 'url(#lg1)' }),
        h('path', { d: line, fill: 'none', stroke: '#7c5cfc', 'stroke-width': 2.5, 'stroke-linecap': 'round' }),
        ...valid.map(p => h('circle', { cx: px(p.i), cy: py(p.y), r: 4, fill: '#fff', stroke: '#7c5cfc', 'stroke-width': 2 })),
        ...props.pts.filter(p => p.i % xEvery === 0).map(p => h('text', { x: px(p.i), y: H - 14, 'text-anchor': 'middle', class: 'line-x' }, p.x)),
      ])
    }
  }
}

const HBars = {
  props: { items: Array },
  setup(props) {
    return () => {
      const max = Math.max(1, ...props.items.map(i => i.value))
      return h('div', { class: 'h-bars' }, props.items.map(i =>
        h('div', { class: 'h-bar-row' }, [
          h('div', { class: 'h-bar-label' }, i.name),
          h('div', { class: 'h-bar-track' }, [
            h('div', { class: 'h-bar-fill', style: { width: Math.round(i.value / max * 100) + '%' } }),
          ]),
          h('div', { class: 'h-bar-val' }, String(i.value)),
        ])
      ))
    }
  }
}

const VBars = {
  props: { items: Array, color: { type: String, default: '#7c5cfc' } },
  setup(props) {
    return () => {
      const max = Math.max(1, ...props.items.map(i => i.count || 0))
      const n = props.items.length
      const xEvery = n > 10 ? 3 : (n > 6 ? 2 : 1)   // 标签跳隔，避免重叠
      const c = props.color
      return h('div', { style: 'display:flex;gap:6px;align-items:flex-end;height:190px;padding-top:22px;border-bottom:1px solid #ececee' },
        props.items.map((i, idx) => {
          const v = i.count || 0
          const barH = Math.max(v ? 8 : 3, Math.round(v / max * 128))
          return h('div', {
            style: 'flex:1;display:flex;flex-direction:column;align-items:center;gap:5px;height:100%;justify-content:flex-end;min-width:0',
            class: 'v-bar-col',
          }, [
            h('div', { style: `font-size:10.5px;font-weight:600;color:${v ? c : 'transparent'};font-variant-numeric:tabular-nums;line-height:1` }, v || '0'),
            h('div', {
              class: 'v-bar-fill',
              style: `width:100%;max-width:26px;height:${barH}px;border-radius:5px 5px 0 0;transition:filter .18s;` +
                (v ? `background:linear-gradient(180deg,${c},${c}cc)` : 'background:#ececee'),
            }),
            h('div', { style: 'font-size:9.5px;color:#a0a0a0;white-space:nowrap;transform:scale(.92)' }, (idx % xEvery === 0) ? (i.date || i.week) : ''),
          ])
        }))
    }
  }
}

const Donut = {
  props: { a: Number, b: Number, labelA: String, labelB: String },
  setup(props) {
    return () => {
      const total = props.a + props.b
      const pct = total ? Math.round(props.a / total * 100) : 0
      const R = 46, C = 2 * Math.PI * R
      const legendRow = (color, name, val, p) =>
        h('div', { style: 'display:flex;align-items:center;gap:9px;font-size:13px' }, [
          h('i', { style: `width:10px;height:10px;border-radius:50%;flex-shrink:0;background:${color}` }),
          h('span', { style: 'color:#5c5c66' }, name),
          h('span', { style: 'margin-left:auto;font-weight:600;color:#222226;font-variant-numeric:tabular-nums' }, val),
          h('span', { style: 'color:#a0a0a0;width:42px;text-align:right;font-variant-numeric:tabular-nums' }, p + '%'),
        ])
      return h('div', { style: 'display:flex;align-items:center;gap:28px' }, [
        h('svg', { viewBox: '0 0 120 120', style: 'width:168px;flex-shrink:0' }, [
          h('circle', { cx: 60, cy: 60, r: R, fill: 'none', stroke: '#efeff2', 'stroke-width': 13 }),
          h('circle', { cx: 60, cy: 60, r: R, fill: 'none', stroke: '#7c5cfc', 'stroke-width': 13,
            'stroke-dasharray': `${pct / 100 * C} ${C}`, 'stroke-linecap': 'round',
            transform: 'rotate(-90 60 60)' }),
          h('text', { x: 60, y: 57, 'text-anchor': 'middle', style: 'font-size:23px;font-weight:700;fill:#222226' }, pct + '%'),
          h('text', { x: 60, y: 76, 'text-anchor': 'middle', style: 'font-size:10px;fill:#a0a0a0' }, props.labelA),
        ]),
        h('div', { style: 'display:flex;flex-direction:column;gap:13px;flex:1' }, [
          legendRow('#7c5cfc', props.labelA, props.a, pct),
          legendRow('#e2e2e6', props.labelB, props.b, 100 - pct),
        ]),
      ])
    }
  }
}

// ---------- 材料分布：南丁格尔玫瑰图 + 图例（按格式大类，面积 ∝ 数量） ----------
const FMT_COLORS = { 文档: '#7c5cfc', 视频: '#E4567B', 演示: '#85B7EB', 图片: '#5DCAA5', 音频: '#EF9F27', 其他: '#b8b8bf' }
const FormatRose = {
  props: { items: Array },
  setup(props) {
    // 环形扇区路径：中心 (cx,cy)，内径 r0 外径 r1，角度 a1→a2（度，0=正上方，顺时针）
    function sectorPath(cx, cy, r0, r1, a1, a2) {
      const rad = d => (d - 90) * Math.PI / 180
      const pt = (r, a) => (cx + r * Math.cos(rad(a))).toFixed(2) + ' ' + (cy + r * Math.sin(rad(a))).toFixed(2)
      const large = (a2 - a1) > 180 ? 1 : 0
      return `M ${pt(r1, a1)} A ${r1} ${r1} 0 ${large} 1 ${pt(r1, a2)}`
        + ` L ${pt(r0, a2)} A ${r0} ${r0} 0 ${large} 0 ${pt(r0, a1)} Z`
    }
    return () => {
      const total = props.items.reduce((s, i) => s + i.value, 0)
      if (!total) return h('div')
      const segs = props.items.map(i => ({
        ...i, color: FMT_COLORS[i.name] || FMT_COLORS.其他,
        pct: Math.round(i.value / total * 100),
      }))
      const W = 220, H = 220, cx = 110, cy = 110
      const R = 100, r0 = 26, GAP = 5
      const max = Math.max(...segs.map(s => s.value))
      const n = segs.length
      const step = 360 / n
      const petals = segs.map((s, i) => {
        const r1 = r0 + (R - r0) * Math.sqrt(s.value / max)   // 面积 ∝ 数量
        const a1 = i * step + GAP / 2, a2 = (i + 1) * step - GAP / 2
        // 数值标签放在花瓣外侧
        const mid = (a1 + a2) / 2, rad = (mid - 90) * Math.PI / 180
        const lx = cx + (r1 + 16) * Math.cos(rad), ly = cy + (r1 + 16) * Math.sin(rad)
        return h('g', { key: s.name }, [
          h('path', { d: sectorPath(cx, cy, r0, r1, a1, a2), fill: s.color, class: 'rose-petal' }),
          h('text', { x: lx.toFixed(1), y: ly.toFixed(1), 'text-anchor': 'middle', 'dominant-baseline': 'middle', class: 'rose-val', style: 'font-size:12px;font-weight:600;fill:#5c5c66' }, s.value),
        ])
      })
      return h('div', { class: 'fmt-rose' }, [
        h('svg', { viewBox: `0 0 ${W} ${H}`, class: 'rose-svg', role: 'img', style: 'width:210px;flex-shrink:0' }, [
          ...petals,
          h('circle', { cx, cy, r: r0 - 6, fill: '#fff' }),
          h('text', { x: cx, y: cy - 4, 'text-anchor': 'middle', class: 'rose-total', style: 'font-size:26px;font-weight:700;fill:#222226' }, total),
          h('text', { x: cx, y: cy + 12, 'text-anchor': 'middle', class: 'rose-total-sub', style: 'font-size:10px;fill:#a0a0a0' }, '份材料'),
        ]),
        h('div', { class: 'fmt-legend', style: 'display:flex;flex-direction:column;gap:11px;flex:1' }, segs.map(s =>
          h('div', { style: 'display:flex;align-items:center;gap:9px;font-size:13px' }, [
            h('i', { style: `width:10px;height:10px;border-radius:50%;flex-shrink:0;background:${s.color}` }),
            h('span', { style: 'color:#5c5c66' }, s.name),
            h('span', { style: 'margin-left:auto;font-weight:600;color:#222226' }, s.value),
            h('span', { style: 'color:#a0a0a0;width:42px;text-align:right;font-variant-numeric:tabular-nums' }, s.pct + '%'),
          ]))),
      ])
    }
  }
}

// ---------- 状态 ----------
const dims = [
  { key: 'invest', label: '学习投入' },
  { key: 'sediment', label: '内容沉淀' },
  { key: 'recall', label: '知识调用' },
  { key: 'memory', label: '复习记忆' },
]
const activeDim = ref('invest')
const loading = ref(false)
const ov = reactive({ today: {}, week: {}, total: {}, dimensions: {}, streak: 0 })
const reviewStats = ref({})
const reporting = ref(false)
const currentReport = ref(null)
const history = ref([])
// 流式生成中的报告（meta 事件填充标题/诊断，token 事件累积正文）
const streamReport = reactive({ title: '', summary: '', issues: [], content: '' })
const displayReport = computed(() => reporting.value ? streamReport : (currentReport.value || streamReport))

function fmtHours(sec) {
  if (!sec) return '0h'
  const h = sec / 3600
  return (h >= 1 ? h.toFixed(1) : Math.round(sec / 60) + 'm')
}

// 学习投入：时长趋势折线
const investLine = computed(() => {
  const d = ov.dimensions?.invest?.duration_daily || []
  if (!d.length) return null
  return d.map((x, i) => ({ i, x: x.date, y: x.minutes || 0 }))
})
const tags = computed(() => ov.dimensions?.invest?.material_by_tag || [])
const formats = computed(() => ov.dimensions?.invest?.material_by_format || [])

// 内容沉淀
const noteWeekly = computed(() => ov.dimensions?.sediment?.note_weekly || [])
const noteSource = computed(() => {
  const s = ov.dimensions?.sediment?.note_source
  return { ai: s?.ai || 0, manual: s?.manual || 0, total: (s?.ai || 0) + (s?.manual || 0) }
})

// 知识调用
const qaDaily = computed(() => ov.dimensions?.recall?.qa_daily || [])
const hitRate = computed(() => ov.dimensions?.recall?.hit_rate || 0)
// 命中异常：有问答量但命中率过低（<30%），提示检查向量模型/回填知识库
const hitAbnormal = computed(() => (ov.total?.qa_count || 0) > 0 && hitRate.value < 30)
// 近 14 天活跃轨迹（打卡圆点）
const recentActive = computed(() => ov.recent_active || [])

// 复习记忆：遗忘曲线折线
const curveLine = computed(() => {
  const fc = reviewStats.value.forgetting_curve || []
  if (!fc.length) return null
  return fc.map((f, i) => ({ i, x: f.interval_days + '天', y: f.rate }))
})
const gradeTotal = computed(() => {
  const d = reviewStats.value.daily || []
  return d.reduce((s, x) => s + (x.count || 0), 0)
})
const gradePct = computed(() => {
  const d = reviewStats.value.daily || []
  const easy = d.reduce((s, x) => s + (x.easy || 0), 0)
  const fuzzy = d.reduce((s, x) => s + (x.fuzzy || 0), 0)
  const forgot = d.reduce((s, x) => s + (x.forgot || 0), 0)
  const t = gradeTotal.value || 1
  return {
    easy: Math.round(easy / t * 100), fuzzy: Math.round(fuzzy / t * 100), forgot: Math.round(forgot / t * 100),
  }
})

const renderedReport = computed(() => {
  const content = displayReport.value?.content || ''
  return content ? md.render(content) : ''
})

// ---------- 报告转笔记 ----------
// 报告是「材料无关」的产物（不归属任何单一材料），所以笔记落 material_id=NULL，
// 只能在知识库「按笔记」里看到 —— 与 AI 问答转笔记同一类。
const aiNotes = ref([])          // 材料无关笔记（问答 / 周报 / 播客脚本）
const noteBusy = ref(false)
// 「已转笔记 · 查看」→ 当前页弹窗打开（共用组件）
const noteView = reactive({ show: false, id: null })

async function loadAiNotes() {
  try {
    // ⚠️ 这里只用 anchor 判断「本周报告转过笔记没有」，不需要正文 →
    // 带 slim=1，否则会把全部周报 / 播客脚本 / 问答沉淀的正文一起拉下来。
    // （「查看」走 NoteEditorDialog，按 id 单独拉全文，不受影响。）
    const { data } = await noteApi.list(null, { slim: 1 })
    aiNotes.value = data
  } catch { /* 静默：拿不到笔记列表不影响看统计 */ }
}

// 状态口径用「ISO 周」这个稳定业务键，**不是 report id** —— 周报「再生成」会换 id
// （同一 ISO 周只保留最新一份），按 id 判重会让按钮重置回「转笔记」→ 笔记重复沉淀。
const weeklyNote = computed(() => {
  const wk = currentReport.value?.week
  if (!wk) return null
  return aiNotes.value.find(
    n => n.anchor?.kind === 'weekly_report' && n.anchor?.week === wk) || null
})
// 「报告已更新」= 本周报告被重新生成过（id 变了）。用版本/指纹比对，不用正文比对 ——
// 后者会被「用户编辑过笔记」误判为过期，覆盖时毁掉编辑。
const weeklyStale = computed(() =>
  !!weeklyNote.value && weeklyNote.value.anchor?.report_id !== currentReport.value?.id)

function reportNoteContent(r) {
  const parts = []
  if (r?.summary) parts.push(`**本周概览**：${r.summary}`)
  if (r?.content) parts.push(r.content)
  return parts.join('\n\n')
}

async function reportToNote() {
  const r = currentReport.value
  if (!r) return
  // 防御：没有 week 就没有稳定业务键，此时写下去会让不同周的报告撞同一个键
  if (!r.week) return ElMessage.warning('这份报告缺少周次信息，请重新生成后再转笔记')
  const content = reportNoteContent(r)
  if (content.trim().length < 2) return ElMessage.warning('报告内容为空，无法转笔记')

  const anchor = {
    kind: 'weekly_report',
    week: r.week,
    report_id: r.id,
    key: `weekly_report:${r.week}`,
  }
  noteBusy.value = true
  try {
    if (weeklyNote.value) {
      // 覆盖更新：同一条笔记推进到最新版，不新建（避免重复沉淀）
      await ElMessageBox.confirm(
        '本周报告已重新生成。更新后，你在笔记里做过的编辑会被最新报告内容覆盖。',
        '更新笔记', { type: 'warning', confirmButtonText: '覆盖更新', cancelButtonText: '取消' })
      const { data } = await noteApi.update(weeklyNote.value.id, { content, anchor })
      if (data?.reindex_warning) ElMessage.warning(data.reindex_warning)
      else ElMessage.success('笔记已更新')
    } else {
      const { data } = await noteApi.fromAi({
        title: r.title || '学习周报', content, source_type: 'weekly_report', anchor,
      })
      if (data?.note?.reindex_warning) ElMessage.warning(data.note.reindex_warning)
      else ElMessage.success('已转成笔记')
    }
    await loadAiNotes()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(errMsg(e, '转笔记失败'))
  } finally {
    noteBusy.value = false
  }
}

// 「查看」跳转到知识库页并直接打开这条笔记的编辑器 ——
// 复用那份功能最全的笔记编辑器（含 AI 改写），不必在这里再复制一份。
// 转笔记后「查看」：在当前页弹窗打开该笔记（与知识库同一份编辑器组件）
function openWeeklyNote(id) {
  noteView.id = Number(id)
  noteView.show = true
}
function issueTitles(issues) {
  return (issues || []).map(i => (typeof i === 'string' ? i : i.title)).join(' · ')
}

async function loadOverview() {
  loading.value = true
  try {
    const { data } = await statsApi.overview()
    Object.assign(ov, data)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '统计加载失败')
  } finally {
    loading.value = false
  }
}

async function loadReview() {
  try {
    const { data } = await reviewApi.stats()
    reviewStats.value = data
  } catch { /* 静默 */ }
}

async function loadHistory() {
  try {
    const { data } = await statsApi.reports()
    history.value = data
  } catch { /* 静默 */ }
}

async function generateReport() {
  reporting.value = true
  Object.assign(streamReport, { title: '', summary: '', issues: [], content: '' })
  try {
    await streamSSE('/api/stats/report/stream', {},
      (t) => { streamReport.content += t },                       // token：逐字追加
      (payload) => {                                              // done：落库后的完整报告
        currentReport.value = payload.report
        loadHistory()
        ElMessage.success('AI 分析报告已生成')
      },
      (msg) => { ElMessage.error(msg || '报告生成失败，请检查 LLM 配置') },  // error
      (meta) => {                                                 // meta：标题/摘要/诊断先到
        streamReport.title = meta.title
        streamReport.summary = meta.summary
        streamReport.issues = meta.issues || []
      })
  } catch (e) {
    ElMessage.error(e.message || '报告生成失败，请检查 LLM 配置')
  } finally {
    reporting.value = false
  }
}

async function openReport(r) {
  try {
    const { data } = await statsApi.getReport(r.id)
    currentReport.value = data
  } catch { /* 静默 */ }
}

onMounted(() => {
  loadOverview()
  loadReview()
  loadHistory()
  loadAiNotes()
})
</script>

<style scoped>
.stats-page { max-width: 1280px; }

/* ===== L1 概览层：留白分层，去卡片边框 ===== */
.stats-hero { display: flex; gap: 56px; margin-bottom: 56px; align-items: stretch; }

/* streak 大卡：品牌紫色签名，更精致 */
.streak-card {
  width: 210px; flex-shrink: 0;
  background: linear-gradient(150deg, #7c5cfc, #8f7bff); color: #fff;
  border-radius: 16px; padding: 28px 26px;
  display: flex; flex-direction: column; justify-content: center;
}
.streak-label { font-size: 12.5px; opacity: .82; font-weight: 500; letter-spacing: .02em; }
.streak-num { font-size: 54px; font-weight: 700; line-height: 1.02; margin: 10px 0 0; letter-spacing: -0.02em; }
.streak-unit { font-size: 13px; opacity: .85; margin-top: 4px; }
.streak-dots { display: flex; gap: 6px; margin-top: 22px; }
.sdot { width: 9px; height: 9px; border-radius: 50%; background: rgba(255,255,255,.26); }
.sdot.on { background: #fff; }
.sdot.today { box-shadow: 0 0 0 3px rgba(255,255,255,.36); }
.streak-dots-label { font-size: 11px; opacity: .68; margin-top: 12px; }

/* KPI：纯排版，无卡片，留白分隔 */
.kpi-wrap { flex: 1; display: flex; flex-direction: column; gap: 34px; justify-content: center; }
.kpi-group-label {
  font-size: 11.5px; color: var(--asc-text-3); font-weight: 600; letter-spacing: .06em;
  margin-bottom: 16px;
}
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 40px; }
.kpi-card { display: flex; flex-direction: column; gap: 5px; }
.kpi-card b { font-size: 27px; color: var(--asc-text); font-weight: 700; letter-spacing: -0.02em; line-height: 1.05; }
.kpi-card span { font-size: 12px; color: var(--asc-text-3); }

/* ===== L2 维度层：Tab 改胶囊式，图表去边框 ===== */
.dim-tabs { display: flex; gap: 4px; margin-bottom: 32px; }
.dim-tab {
  padding: 7px 18px; font-size: 13.5px; color: var(--asc-text-3); cursor: pointer;
  border-radius: 9px; transition: color .18s, background .18s;
}
.dim-tab:hover { color: var(--asc-text-2); background: var(--asc-surface-2); }
.dim-tab.active { color: var(--asc-primary); font-weight: 600; background: var(--asc-primary-soft); }

.chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 48px; }
.chart-card h4 { font-size: 15px; font-weight: 600; margin: 0 0 4px; letter-spacing: -0.01em; }
.chart-sub { font-size: 12.5px; color: var(--asc-text-3); margin-bottom: 22px; }

/* 折线图 */
.line-svg { display: block; width: 100%; }
.line-x { font-size: 10px; fill: #a0a0a0; }

/* 横向柱状 */
.h-bars { display: flex; flex-direction: column; gap: 16px; }
.h-bar-row { display: flex; align-items: center; gap: 12px; }
.h-bar-label { width: 68px; font-size: 12.5px; color: var(--asc-text-2); text-align: right; flex-shrink: 0; }
.h-bar-track { flex: 1; height: 11px; background: #f0f0f2; border-radius: 6px; overflow: hidden; }
.h-bar-fill { height: 100%; background: #7c5cfc; border-radius: 6px; transition: width .3s; }
.h-bar-val { width: 26px; font-size: 12.5px; color: var(--asc-text-2); }

/* 材料分布：南丁格尔玫瑰图 + 图例 */
.fmt-rose { display: flex; align-items: center; gap: 24px; }
.rose-petal { transition: opacity .2s; }
.rose-petal:hover { opacity: .82; }
.fmt-legend { display: flex; flex-direction: column; gap: 11px; flex: 1; }
.fmt-legend-item { display: flex; align-items: center; gap: 9px; font-size: 13px; }
.fmt-legend-item .dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.fmt-name { color: var(--asc-text-2); }
.fmt-count { margin-left: auto; font-weight: 600; color: var(--asc-text); }
.fmt-pct { color: var(--asc-text-3); width: 42px; text-align: right; font-variant-numeric: tabular-nums; }

/* 纵向柱状（柱体/标签样式已内联在组件中，这里仅保留 hover） */
.v-bar-col:hover .v-bar-fill { filter: brightness(1.12); }

/* 环形图（布局样式已内联在组件中） */
.mini-stat {
  margin-top: 18px; padding-top: 14px; border-top: 1px solid var(--asc-divider);
  font-size: 12.5px; color: var(--asc-text-2);
}
.mini-stat b { color: var(--asc-primary); }
.hit-warn {
  margin-top: 14px; padding: 12px 15px;
  background: #fef3e2; border: 1px solid #f5d9a8; color: #b45309;
  border-radius: 8px; font-size: 12.5px; line-height: 1.6;
}

/* 复习概览：分隔线式数字 + 分段评级条 */
.mem-grid { display: grid; grid-template-columns: repeat(4, 1fr); margin-bottom: 26px; }
.mem-cell { display: flex; flex-direction: column; gap: 4px; padding: 2px 0 2px 20px; border-left: 1px solid var(--asc-divider); }
.mem-cell:first-child { border-left: none; padding-left: 0; }
.mem-cell b { font-size: 26px; color: var(--asc-text); font-weight: 700; letter-spacing: -0.02em; line-height: 1.1; }
.mem-cell b.accent { color: var(--asc-primary); }
.mem-cell b.green { color: #0f6e56; }
.mem-cell b.red { color: #d24e4e; }
.mem-cell span { font-size: 11.5px; color: var(--asc-text-3); }
.mem-grade { margin-top: 4px; }
.grade-bar { display: flex; gap: 3px; height: 12px; }
.grade-bar span { border-radius: 6px; transition: width .3s; }
.grade-bar .g-easy { background: #639922; }
.grade-bar .g-fuzzy { background: #d97706; }
.grade-bar .g-forgot { background: #d24e4e; }
.grade-legend { display: flex; gap: 18px; font-size: 12px; color: var(--asc-text-2); margin-top: 12px; flex-wrap: wrap; }
.grade-legend b { font-weight: 600; color: var(--asc-text); font-variant-numeric: tabular-nums; }
.grade-legend .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }

/* ===== L3 AI 报告：成片内容保留浅色容器，内部疏朗 ===== */
.report-section { margin-top: 56px; }
.report-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 20px; }
.report-head h3 { font-size: 16px; font-weight: 600; margin: 0 0 4px; }
.report-desc { font-size: 12.5px; color: var(--asc-text-3); }
.report-card {
  background: var(--asc-card); border: none; border-radius: 14px;
  padding: 26px 28px; min-height: 120px;
  box-shadow: 0 1px 2px rgba(0,0,0,.03);
}
.report-loading { text-align: center; color: var(--asc-text-2); padding: 44px 0; }
.report-loading .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #d8cefe; border-top-color: var(--asc-primary); border-radius: 50%; animation: spin .9s linear infinite; margin-right: 8px; vertical-align: -2px; }
@keyframes spin { to { transform: rotate(360deg); } }
.stream-cursor {
  display: inline-block; width: 8px; height: 16px; margin-left: 2px; vertical-align: -2px;
  background: var(--asc-primary); border-radius: 2px; animation: blink .8s steps(1) infinite;
}
@keyframes blink { 50% { opacity: 0; } }
.report-meta {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding-bottom: 14px; margin-bottom: 16px; border-bottom: 1px solid var(--asc-divider);
  font-size: 12px; color: var(--asc-text-3);
}
.report-diag { display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px; }
.diag-chip {
  display: flex; align-items: baseline; gap: 10px;
  padding: 9px 13px; background: #fef3e2; border: 1px solid #f5d9a8; border-radius: 8px;
}
.diag-chip-tag {
  flex-shrink: 0; font-size: 12px; font-weight: 600; color: #b45309;
  background: #fff; border-radius: 4px; padding: 1px 8px;
}
.diag-chip-detail { font-size: 12.5px; color: #7c5a2a; line-height: 1.5; }
.report-ops {
  display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
  margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--asc-divider);
}
.report-history { margin-top: 20px; }
.report-history h4 { font-size: 13px; font-weight: 600; color: var(--asc-text-2); margin: 0 0 10px; }
.history-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 2px; margin-bottom: 2px; cursor: pointer;
  border-bottom: 1px solid var(--asc-divider); transition: color .18s;
}
.history-item:hover .h-title { color: var(--asc-primary); }
.history-item:last-child { border-bottom: none; }
.h-title { font-size: 13.5px; font-weight: 500; }
.h-meta { font-size: 12px; color: var(--asc-text-3); }

@media (max-width: 900px) {
  .stats-hero { flex-direction: column; gap: 32px; }
  .streak-card { width: 100%; }
  .chart-row { grid-template-columns: 1fr; gap: 32px; }
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .mem-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
