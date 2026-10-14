<template>
  <div class="page drill-page">
    <!-- ==================== ① 列表态 ==================== -->
    <template v-if="view === 'list'">
      <PageHead title="AI 陪练" sub="选一份材料，让 AI 当考官：逐题作答、即时评分、错题自动进复习">
        <template #icon>
          <svg viewBox="0 0 16 16" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.3">
            <circle cx="8" cy="8" r="5.6" /><circle cx="8" cy="8" r="2.6" />
            <circle cx="8" cy="8" r=".9" fill="currentColor" />
          </svg>
        </template>
        <el-button type="primary" @click="gotoCreate">新建陪练</el-button>
      </PageHead>

      <div class="list-bar">
        <el-input v-model="sessionSearch" placeholder="搜索陪练记录" clearable size="default"
          class="list-search" @keyup.enter="loadSessions" @clear="loadSessions">
          <template #prefix>
            <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5 14 14" stroke-linecap="round" />
            </svg>
          </template>
        </el-input>
        <span v-if="total" class="list-count">共 {{ total }} 场</span>
      </div>

      <!-- 跨场次趋势（优化 3）：报告原先只在前端算 →「短板是否在收敛」在架构上做不到 -->
      <div v-if="!sessionSearch && trend && trend.n_sessions >= 3" class="td-card">
        <div class="td-head">
          <span class="td-title">跨场次趋势</span>
          <span class="td-sub">最近 {{ trend.n_sessions }} 场</span>
        </div>
        <div class="td-body">
          <div class="td-col">
            <div class="td-label">薄弱要点是否在收敛</div>
            <ul v-if="trend.weak.length" class="td-list">
              <li v-for="(w, i) in trend.weak.slice(0, 5)" :key="i">
                <span class="td-delta" :class="w.delta > 0 ? 'up' : (w.delta < 0 ? 'down' : 'flat')">{{ w.delta > 0 ? '↑ 变多' : (w.delta < 0 ? '↓ 收敛' : '→ 持平') }}</span>
                <span class="td-text" :title="w.text">{{ w.text }}</span>
                <span class="td-cnt">近 {{ w.recent }} / 前 {{ w.earlier }}</span>
              </li>
            </ul>
            <p v-else class="td-empty">暂无可统计的漏点（多完成几场陪练后自动汇总）</p>
          </div>
          <div class="td-col td-col-bar">
            <div class="td-label">每场总分</div>
            <div class="td-bars">
              <span v-for="s in trend.sessions" :key="s.id" class="td-bar-wrap"
                :title="s.title + ' · ' + (s.score == null ? '—' : s.score + ' 分')">
                <i class="td-bar" :style="{ height: (s.score == null ? 4 : Math.max(6, s.score)) + '%', background: scoreColor(s.score) }"></i>
              </span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="listLoading" class="list-loading">正在加载…</div>

      <el-empty v-else-if="!sessions.length" :image="mascot" :image-size="120"
        :description="sessionSearch ? '没有匹配的陪练记录' : '还没有陪练记录。选一份材料，让 AI 出题考考你'">
        <el-button v-if="sessionSearch" @click="clearSearch">清除搜索</el-button>
        <el-button v-else type="primary" @click="gotoCreate">新建陪练</el-button>
      </el-empty>

      <div v-else class="session-grid">
        <div v-for="s in sessions" :key="s.id" class="session-card" @click="openSession(s.id)">
          <div class="sc-top">
            <span class="sc-title" :title="s.title">{{ s.title }}</span>
            <span class="sc-status" :class="statusOf(s.status).cls">{{ statusOf(s.status).label }}</span>
          </div>
          <div class="sc-meta">
            <span class="sc-scope">{{ scopeSummary(s) }}</span>
            <span class="sc-sep">·</span>
            <span>{{ (s.config || {}).question_count || 8 }} 题</span>
            <template v-if="s.total_score != null">
              <span class="sc-sep">·</span>
              <span class="sc-score" :style="{ color: scoreColor(s.total_score) }">{{ s.total_score }} 分</span>
            </template>
          </div>
          <div class="sc-foot">
            <span class="sc-time">{{ relTime(s.updated_at) }}</span>
            <span class="sc-del" title="删除该记录" @click.stop="removeSession(s)">删除</span>
          </div>
        </div>
      </div>

      <div v-if="hasMore" class="list-more">
        <el-button :loading="loadingMore" @click="loadMore">
          加载更多（已显示 {{ sessions.length }} / {{ total }} 场）
        </el-button>
      </div>
    </template>

    <!-- ==================== ② 新建态 ==================== -->
    <template v-else-if="view === 'create'">
      <div class="cr-head">
        <span class="cr-back" @click="backToList">← 返回</span>
        <h3>新建陪练</h3>
      </div>

      <div class="cr-card">
        <div class="cr-row">
          <label class="cr-label">陪练名称</label>
          <el-input v-model="form.title" maxlength="50" show-word-limit
            placeholder="留空将按取材范围自动命名" class="cr-input" />
        </div>

        <div class="cr-row cr-row-block">
          <label class="cr-label">取材范围</label>
          <div class="cr-scope">
            <ScopePicker v-model:scope="form.scope" v-model:material-ids="form.materialIds"
              v-model:note-ids="form.noteIds" v-model:folder-ids="form.folderIds"
              :material-options="materialOptions" :note-options="noteOptions"
              :folder-options="folderOptions" :allow-empty-all="false" />
            <div class="cr-preview" :class="{ warn: !!scopeHint }">
              <template v-if="previewing">正在统计范围内容…</template>
              <template v-else-if="scopeHint">{{ scopeHint }}</template>
              <template v-else-if="preview">
                本范围含 <b>{{ preview.material_count }}</b> 份材料<template
                  v-if="preview.note_count">、<b>{{ preview.note_count }}</b> 条笔记</template>，
                约可出 <b>{{ estimateQuestions }}</b> 题
              </template>
            </div>
          </div>
        </div>

        <div class="cr-row">
          <label class="cr-label">题量</label>
          <el-input-number v-model="form.questionCount" :min="3" :max="20" :step="1" size="default" />
          <span class="cr-tip">3–20 题，默认 8 题</span>
        </div>

        <div class="cr-row">
          <label class="cr-label">追问策略</label>
          <el-radio-group v-model="form.followUp" size="default" class="cr-seg">
            <el-radio-button value="shallow">答得浅才追问</el-radio-button>
            <el-radio-button value="always">每题都追问</el-radio-button>
            <el-radio-button value="never">不追问</el-radio-button>
          </el-radio-group>
          <span class="cr-tip">追问针对同一考点再挖一层，每个考点最多 1 次</span>
        </div>

        <div class="cr-row">
          <label class="cr-label">参考答案</label>
          <el-switch v-model="form.showAnswer" />
          <span class="cr-tip">评分后展示标准要点，便于对照复盘</span>
        </div>

        <div class="cr-row">
          <label class="cr-label">难度自适应</label>
          <el-switch v-model="form.adaptive" />
          <span class="cr-tip">连续 2 题答得好就给后续题加难、答得差就降难（关掉＝按题纲难度出题）</span>
        </div>
      </div>

      <div class="cr-actions">
        <el-button text @click="backToList">取消</el-button>
        <el-button type="primary" class="cr-start" :disabled="!canStart" :loading="creating" @click="createAndStart">
          {{ creating ? '正在创建…' : '开始陪练' }}
        </el-button>
      </div>
    </template>

    <!-- ==================== ③ 会话态 / ④ 成绩单（共用逐题区） ==================== -->
    <template v-else>
      <div class="flow-head">
        <span class="flow-back" @click="backToList">← 返回</span>
        <span class="flow-title" :title="(current || {}).title">{{ (current || {}).title }}</span>
        <span class="flow-progress" :class="{ done: view === 'result' }">
          <template v-if="view === 'session' && phase !== 'outline'">第 {{ progress.cur }} / {{ progress.total }} 题</template>
          <template v-else-if="view === 'result'">已完成</template>
        </span>
        <el-button v-if="view === 'session' && phase !== 'failed'" size="small" plain
          :disabled="phase === 'outline'" @click="finishEarly">提前结束</el-button>
      </div>

      <!-- 成绩单英雄区 -->
      <div v-if="view === 'result'" class="rs-hero">
        <div class="rs-score" :style="{ color: scoreColor(resultStats.total_score) }">
          {{ resultStats.total_score != null ? resultStats.total_score : '—' }}<span class="rs-unit">分</span>
        </div>
        <div class="rs-sub">
          <template v-if="resultStats.early">本场提前结束，</template>共完成 {{ resultStats.answered_points }} / {{ resultStats.outline_count }} 题<template
            v-if="resultStats.graded_count"> · 有效评分 {{ resultStats.graded_count }} 题</template>
        </div>
        <div class="rs-stats">
          <div class="rs-stat"><b>{{ resultStats.graded_count }}</b><i>已评分</i></div>
          <div class="rs-stat"><b>{{ resultStats.weak_count }}</b><i>薄弱题</i></div>
          <div class="rs-stat"><b>{{ writtenCount == null ? '—' : writtenCount }}</b><i>新增复习卡</i></div>
        </div>
        <div v-if="writtenCount" class="rs-writeback">
          <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4"
            stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5 6.5 12 13 4.5" /></svg>
          已把 {{ writtenCount }} 道薄弱题加入复习队列
          <el-button text type="primary" size="small" @click="$router.push('/review')">去复习</el-button>
        </div>
        <div v-else-if="writtenBackAt && resultStats.weak_count" class="rs-writeback muted">
          薄弱题此前已加入复习队列（不会重复写入）
        </div>
      </div>

      <!-- 陪练报告（C 档）：单场能力画像。数据全部来自已渲染的轮次，不额外请求后端 -->
      <div v-if="view === 'result' && report" class="rs-report">
        <div class="rp-title">本次表现</div>
        <div v-if="report.dims.length" class="rp-dims">
          <div v-for="d in report.dims" :key="d.name" class="rp-dim">
            <span class="rp-dim-name">{{ d.name }}</span>
            <span class="rp-dim-track">
              <span class="rp-dim-fill"
                :style="{ width: d.value + '%', background: scoreColor(d.value) }"></span>
            </span>
            <b class="rp-dim-val">{{ d.value }}</b>
          </div>
        </div>
        <div v-if="report.missing.length" class="rp-sub">
          <div class="rp-sub-title">最该补的要点</div>
          <ul class="rp-list">
            <li v-for="(m, i) in report.missing" :key="i">
              <span v-if="m.n > 1" class="rp-cnt">漏 {{ m.n }} 次</span>{{ m.text }}
            </li>
          </ul>
        </div>
        <div class="rp-sub">
          <div class="rp-sub-title">难度走向</div>
          <p class="rp-diff">{{ report.diffNote || '全程按题纲难度出题（未触发自适应调整）' }}</p>
        </div>
      </div>

      <!-- 考点清单 -->
      <div v-if="view === 'session' && outline.length" class="outline-bar">
        <span v-for="(p, i) in outline" :key="i" class="outline-chip"
          :class="{ active: i === currentOutlineIndex, done: i < currentOutlineIndex }">
          <b>{{ i + 1 }}</b>{{ p.point }}
        </span>
      </div>

      <!-- 考点数提示（PRD 3.3.7：实际数量可能少于请求数，需显式告知） -->
      <div v-if="view === 'session' && outline.length" class="outline-note">
        本次共生成 <b>{{ outline.length }}</b> 个考点<template v-if="outlineShort">（原计划
          {{ outlineRequested }} 个，模型按素材实际内容生成）</template>
      </div>

      <!-- 生成中 / 评分中 -->
      <div v-if="view === 'session' && (phase === 'outline' || phase === 'scoring')" class="stage-banner">
        <span class="stage-spinner"></span>{{ stageText || '正在处理…' }}
      </div>
      <div v-else-if="view === 'session' && phase === 'idle' && !readonly" class="stage-banner">
        <span>这场陪练尚未开始。</span>
        <el-button type="primary" size="small" @click="attach">开始陪练</el-button>
      </div>

      <!-- 失败态（可重试） -->
      <div v-if="view === 'session' && phase === 'failed'" class="fail-banner">
        <span class="fail-text">{{ failMsg || '生成失败，请重试' }}</span>
        <el-button type="primary" size="small" :loading="retrying" @click="retry">重试</el-button>
        <el-button size="small" @click="backToList">返回列表</el-button>
      </div>

      <!-- 逐题区 -->
      <div class="turn-list" ref="bodyRef">
        <div v-for="t in turns" :key="t.id" class="turn">
          <!-- 题干 -->
          <div class="q-block">
            <span class="q-tag" :class="{ followup: t.is_followup }">
              {{ t.is_followup ? '追问' : '考点 ' + ((t.outline_index || 0) + 1) }}
            </span>
            <span v-if="t.source_title" class="q-src" :title="t.source_title">出处：{{ t.source_title }}</span>
            <span v-if="diffBadge(t)" class="q-diff" :class="diffBadge(t).cls"
              :title="diffBadge(t).tip">{{ diffBadge(t).label }}</span>
            <div class="q-text">{{ t.question }}</div>
          </div>

          <!-- 我的作答 -->
          <div v-if="t.user_answer !== null" class="a-block">
            <div class="a-label">我的作答</div>
            <div class="a-text" :class="{ skipped: t.skipped }">
              {{ t.skipped || t.user_answer === '' ? '（本题已跳过）' : t.user_answer }}
            </div>
          </div>

          <!-- 评分卡 -->
          <div v-if="t._pending" class="score-card pending">
            <span class="stage-spinner"></span>{{ stageText || 'AI 正在评分…' }}
          </div>
          <div v-else-if="t.user_answer !== null && t.skipped" class="score-card skipped">
            <svg class="sc-ico" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6" /><path d="M5 8h6" stroke-linecap="round" /></svg>
            <span class="sc-verdict-skipped">{{ t.comment || '本题已跳过' }} · 未计入总分</span>
          </div>
          <div v-else-if="t.user_answer !== null && t.score != null" class="score-card" :class="t.verdict">
            <div class="score-head">
              <span class="score-num" :style="{ color: scoreColor(t.score) }">{{ t.score }}</span>
              <span class="score-verdict" :class="t.verdict">{{ verdictLabel(t.verdict) }}</span>
              <div class="score-dims">
                <span v-for="d in dimsOf(t)" :key="d.name" class="dim">
                  <i>{{ d.name }}</i>
                  <span class="dim-bar"><span class="dim-fill" :style="{ width: d.value + '%', background: scoreColor(d.value) }"></span></span>
                  <b>{{ d.value }}</b>
                </span>
              </div>
            </div>
            <p v-if="t.comment" class="score-comment">{{ t.comment }}</p>
            <div v-if="t.missing_points && t.missing_points.length" class="score-miss">
              <div class="score-sub-label">未覆盖的要点</div>
              <ul><li v-for="(p, i) in t.missing_points" :key="i">{{ p }}</li></ul>
            </div>
            <div v-if="t.expected_points && t.expected_points.length" class="score-expect">
              <div class="score-sub-label">参考答案要点</div>
              <ul><li v-for="(p, i) in t.expected_points" :key="i">{{ p }}</li></ul>
            </div>
          </div>
          <div v-else-if="t.user_answer !== null && !t._pending" class="score-card failed">
            <svg class="sc-ico" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6" /><path d="M8 5v3.5" stroke-linecap="round" /><circle cx="8" cy="10.8" r=".8" fill="currentColor" /></svg>
            <span class="sc-verdict-failed">本题评分失败，未计入总分</span>
          </div>

          <!-- 作答区（仅当前待答的那一题） -->
          <div v-if="view === 'session' && phase === 'question' && t === unansweredTurn" class="answer-box">
            <el-input v-model="answer" type="textarea" :rows="4" resize="none"
              placeholder="写下你的回答，尽量把要点讲透…（Ctrl/⌘ + Enter 提交）"
              @keydown.ctrl.enter.prevent="submitAnswer(false)"
              @keydown.meta.enter.prevent="submitAnswer(false)" />
            <div class="answer-tools">
              <el-button size="small" plain :loading="speaking" @click="speakQuestion(t)">
                念题
              </el-button>
              <el-button size="small" plain :disabled="transcribing" :class="{ 'rec-on': recording }"
                @click="toggleRecord">
                {{ recording ? '结束录音' : (transcribing ? '识别中…' : '口述作答') }}
              </el-button>
              <span v-if="recording" class="rec-hint"><i class="rec-dot"></i>正在录音 {{ recSeconds }} 秒 / 最长 {{ REC_MAX_SECONDS }} 秒</span>
              <span v-else-if="asrHint" class="rec-hint err">{{ asrHint }}</span>
            </div>
            <div class="answer-actions">
              <span class="answer-hint" :class="{ over: answerOver }">
                {{ answerLen }} 字<template v-if="answerOver">，超出 {{ MAX_ANSWER }} 字上限</template>
              </span>
              <el-button size="small" @click="submitAnswer(true)">跳过本题</el-button>
              <el-button type="primary" size="small" :loading="submitting"
                :disabled="!answer.trim() || answerOver" @click="submitAnswer(false)">提交作答</el-button>
            </div>
          </div>
        </div>

        <div v-if="view === 'session' && phase === 'question' && !turns.length" class="turn-empty">
          正在准备第一道题…
        </div>
      </div>

      <!-- 成绩单底部操作 -->
      <div v-if="view === 'result'" class="rs-actions">
        <el-button @click="backToList">返回列表</el-button>
        <el-button :loading="noteBusy" @click="saveToNote">
          {{ drillNote ? '更新笔记' : '转成笔记' }}
        </el-button>
        <el-button v-if="drillNote" @click="openDrillNote">查看笔记</el-button>
        <el-button type="primary" @click="againSameConfig">按此范围再练一场</el-button>
      </div>
    </template>

    <!-- 查看/编辑「成绩单笔记」：复用知识库那份最全的笔记编辑器，不自造 -->
    <NoteEditorDialog v-model="noteView.show" :note-id="noteView.id" @changed="loadDrillNote" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { drillApi, materialApi, kbApi, foldersApi, noteApi } from '../api'
import { errMsg, guideIfNeedsSetupMsg } from '../api/http'
import ScopePicker from '../components/ScopePicker.vue'
import NoteEditorDialog from '../components/NoteEditorDialog.vue'
import PageHead from '../components/PageHead.vue'
import mascot from '../assets/mascot.png'

const route = useRoute()
const router = useRouter()

// ---------- 常量 ----------
const LIMIT = 12                 // 列表一页 12 场，超出走底部「加载更多」
const DEFAULT_COUNT = 8
const MIN_COUNT = 3
const MAX_COUNT = 20
const MAX_ANSWER = 2000          // 作答字符上限（PRD 3.4.5：1–2000；后端同样校验 400）
// 约可出题数：粗估（1 题 ≈ 400 字可用素材）。仅作范围体量提示，不作为硬约束
const CHARS_PER_QUESTION = 400

const STATUS_MAP = {
  draft: { label: '未开始', cls: 'draft' },
  outlining: { label: '生成中', cls: 'running' },
  running: { label: '进行中', cls: 'running' },
  finished: { label: '已完成', cls: 'done' },
  failed: { label: '已中断', cls: 'failed' },
}
const VERDICT_MAP = { correct: '正确', partial: '部分正确', wrong: '错误' }
// 难度自适应（C 档）：档位序用于判定「加难 / 降难」
const DIFF_LEVELS = ['easy', 'medium', 'hard']
const DIFF_LABEL = { easy: '简单', medium: '中等', hard: '较难' }
// 难度归一化：题纲难度由 LLM 生成，可能是「Medium」「中等」「难」等任意写法。
// ⚠️ 后端出题时已归一化（norm_difficulty）后落库，前端必须用同一口径比对，
//    否则旧数据里存着 "Medium" 时会被判成 ≠ medium → **每题**都误标「已加难」。
const DIFF_ALIASES = {
  easy: 'easy', 简单: 'easy', 容易: 'easy', 基础: 'easy', 低: 'easy',
  medium: 'medium', 中等: 'medium', 中: 'medium', 一般: 'medium', 普通: 'medium',
  hard: 'hard', 困难: 'hard', 较难: 'hard', 难: 'hard', 高: 'hard',
}
function normDiff(v) {
  const s = String(v == null ? '' : v).trim().toLowerCase()
  return DIFF_ALIASES[s] || 'medium'      // 与后端 norm_difficulty 的兜底完全一致
}

// 漏点归一（与后端 services/drill.py::point_key 同口径）：
// 「未提到成本」/「没考虑成本」必须聚成同一个「成本」，否则「最该补的要点」会被拆成
// 几十条只出现 1 次的碎片，top 列表就没有信息量了。
const POINT_DROP_CHARS = /[，。；、,.;:：！!？?（）()【】\[\]「」「」"'　 ·\-—]/g
const POINT_LEAD = ['未充分', '未提及', '未提到', '未涉及', '未覆盖', '未考虑', '未说明', '未回答',
  '没有提到', '没有提及', '没有涉及', '没有考虑', '没有说明', '没有覆盖',
  '没提到', '没提及', '没涉及', '没考虑', '没说明', '没覆盖',
  '缺乏', '缺少', '缺失', '遗漏', '忽略', '漏掉', '漏了', '不够', '未', '没', '不']
const POINT_VERB = ['提到', '提及', '涉及', '考虑', '覆盖', '说明', '回答', '阐述', '点出',
  '给出', '包括', '包含', '强调', '明确']
const POINT_SKIP = ['了', '掉', '的', '地', '得']
const POINT_TAIL = ['维度', '方面', '部分', '内容', '这一点']
function pointKey(text) {
  const raw = String(text == null ? '' : text).trim().toLowerCase()
  let s = raw.replace(POINT_DROP_CHARS, '')
  for (let n = 0; n < 4; n += 1) {
    const before = s
    for (const k of POINT_SKIP) { if (s.startsWith(k) && s.length > k.length) { s = s.slice(k.length); break } }
    for (const k of POINT_LEAD) { if (s.startsWith(k) && s.length > k.length) { s = s.slice(k.length); break } }
    for (const k of POINT_VERB) { if (s.startsWith(k) && s.length > k.length) { s = s.slice(k.length); break } }
    for (const k of POINT_TAIL) { if (s.endsWith(k) && s.length > k.length) { s = s.slice(0, s.length - k.length); break } }
    if (s === before) break
  }
  return s || raw
}
// 成绩单笔记的稳定业务键：一次陪练只沉淀一条，二次点击走「覆盖更新」而不是新建
const drillNoteKey = (sid) => `drill_report:${sid}`
const SCOPE_LABEL = { all: '整个知识库', material: '资料', note: '笔记', folder: '文件夹' }

// ---------- 状态（⚠️ 必须全部声明在所有 computed/watch 之前，否则注册期求值 → TDZ 白屏） ----------
const view = ref('list')            // list | create | session | result
const readonly = ref(false)         // 历史详情（已结束）为只读

// 列表
const sessions = ref([])
const total = ref(0)
const listLoading = ref(false)
const loadingMore = ref(false)
const sessionSearch = ref('')

// 选项池
const materialOptions = ref([])
const noteOptions = ref([])
const folderOptions = ref([])

// 新建表单
const form = reactive({
  title: '',
  scope: 'all',
  materialIds: [],
  noteIds: [],
  folderIds: [],
  questionCount: DEFAULT_COUNT,
  followUp: 'shallow',
  showAnswer: true,
  adaptive: true,
})
const preview = ref(null)
const previewing = ref(false)
const creating = ref(false)

// 会话运行态
const current = ref(null)
const turns = ref([])
const outline = ref([])
const progress = reactive({ cur: 0, total: 0 })
const phase = ref('idle')           // idle | outline | question | scoring | finished | failed
const stageText = ref('')
const failMsg = ref('')
const answer = ref('')
const submitting = ref(false)

// 语音（C 档）
const speaking = ref(false)        // 念题合成中
const recording = ref(false)       // 正在录音
const transcribing = ref(false)    // 正在转写
const recSeconds = ref(0)
const asrHint = ref('')
let mediaRec = null
let recChunks = []
let recTimer = null
// 录音上限：不设上限时用户可能白录十分钟，上传时才因超 12MB 被后端拒掉
const REC_MAX_SECONDS = 120
// 语音转写标记：只为**校对**服务（用户要能分辨哪段是 ASR），提交前会剥掉
const VOICE_MARK = '〔语音〕'
// 念题（TTS）：必须留着播放句柄 —— 否则「再点一次＝停止」做不到，两段朗读会叠加。
let speakAudio = null
let speakUrl = ''        // 对应的 blob URL（play() 被拒时 onended 不触发，只能显式回收）
let speakToken = 0       // 合成代次：连点时让先返回的旧响应作废，避免叠加播放

// 成绩单笔记（C 档）
const noteBusy = ref(false)
const noteView = reactive({ show: false, id: null })
const drillNotes = ref([])         // 材料无关笔记里 source_type === 'drill_report' 的那批
const trend = ref(null)            // 跨场次趋势（服务端聚合，优化 3）
const retrying = ref(false)
const summary = ref(null)           // finish 事件 / finish 接口返回的结算对象
const bodyRef = ref(null)

let streamCtrl = null
let previewTimer = null

// ---------- 派生 ----------
const currentOutlineIndex = computed(() => Math.max(0, progress.cur - 1))

const unansweredTurn = computed(() => {
  for (let i = turns.value.length - 1; i >= 0; i--) {
    if (turns.value[i].user_answer === null) return turns.value[i]
  }
  return null
})

const refsKey = computed(() => (
  form.scope + '|' + form.materialIds.join(',') + '|'
  + form.noteIds.join(',') + '|' + form.folderIds.join(',')
))

const scopeEmpty = computed(() => {
  if (form.scope === 'all') return false
  if (form.scope === 'material') return !form.materialIds.length
  if (form.scope === 'note') return !form.noteIds.length
  if (form.scope === 'folder') return !form.folderIds.length
  return true
})

const previewEmpty = computed(() => {
  if (!preview.value) return false
  return (preview.value.material_count || 0) + (preview.value.note_count || 0) === 0
})

const scopeHint = computed(() => {
  if (scopeEmpty.value) {
    if (form.scope === 'folder') return '请先选择要陪练的文件夹'
    if (form.scope === 'material') return '请先选择要陪练的资料'
    if (form.scope === 'note') return '请先选择要陪练的笔记'
    return '请先选择要陪练的内容'
  }
  if (preview.value && previewEmpty.value) {
    if (form.scope === 'all') return '知识库还是空的，先去导入材料吧'
    if (form.scope === 'folder') return '所选文件夹下没有可陪练的材料'
    return '所选范围内没有可陪练的内容，请重新选择'
  }
  if (preview.value && preview.value.excluded_count > 0) {
    return '有 ' + preview.value.excluded_count + ' 份材料尚未解析完成，已排除'
  }
  return ''
})

const canStart = computed(() => (
  !creating.value && !scopeEmpty.value && !previewEmpty.value && !!preview.value
))

const estimateQuestions = computed(() => {
  const chars = (preview.value && preview.value.content_chars) || 0
  return Math.max(1, Math.round(chars / CHARS_PER_QUESTION))
})

const writtenCount = computed(() => {
  if (summary.value && typeof summary.value.written_count === 'number') return summary.value.written_count
  return null
})
const writtenBackAt = computed(() => (current.value || {}).written_back_at || null)

const resultStats = computed(() => {
  const ts = turns.value
  const graded = ts.filter((t) => t.score != null)
  const avg = graded.length
    ? Math.round((graded.reduce((a, t) => a + t.score, 0) / graded.length) * 10) / 10
    : null
  // 「已完成」按**考点**计：追问轮不重复计数、跳过的不算完成。用轮次数会出现
  // 「共完成 11 / 8 题」这种自相矛盾的数字（追问会撑大轮次数）。
  const answeredPoints = new Set(
    ts.filter((t) => t.user_answer !== null && t.user_answer !== '')
      .map((t) => t.outline_index || 0),
  ).size
  const base = summary.value
    ? {
      total_score: summary.value.total_score != null ? summary.value.total_score : avg,
      graded_count: summary.value.graded_count != null ? summary.value.graded_count : graded.length,
      answered_count: summary.value.answered_count != null ? summary.value.answered_count
        : ts.filter((t) => t.user_answer !== null).length,
      outline_count: summary.value.outline_count != null ? summary.value.outline_count : outline.value.length,
      weak_count: summary.value.weak_count != null ? summary.value.weak_count
        : ts.filter((t) => t.verdict === 'partial' || t.verdict === 'wrong').length,
    }
    : {
      total_score: (current.value || {}).total_score != null ? current.value.total_score : avg,
      graded_count: graded.length,
      answered_count: ts.filter((t) => t.user_answer !== null).length,
      outline_count: outline.value.length,
      weak_count: ts.filter((t) => t.verdict === 'partial' || t.verdict === 'wrong').length,
    }
  // 提前结束的判据 = 「已完成考点数 < 考点总数」。历史详情拿不到后端的 reason
  // （session 表没持久化它），但这两个数在任何路径下都可得 → 实时与回看口径一致。
  const done = Math.min(answeredPoints, base.outline_count)
  return { ...base, answered_points: done, early: base.outline_count > 0 && done < base.outline_count }
})

// ⚠️ TDZ 契约：这三个 computed 依赖的 ref 都已在**上方**声明
// 字数按**剥掉语音标记**后算：标记只为校对，不该占用用户的 2000 字额度
const answerLen = computed(() => stripVoiceMark(answer.value).length)
const answerOver = computed(() => answerLen.value > MAX_ANSWER)
const outlineRequested = computed(() => {
  const cfg = (current.value || {}).config || {}
  return Number(cfg.question_count) || 0
})
// 实际考点数 < 请求数（模型按素材实际内容产出）→ 显式告知（PRD 3.3.7）
const outlineShort = computed(() => outline.value.length > 0
  && outlineRequested.value > outline.value.length)

// 本场是否已转过笔记（按 anchor.key 认领 —— 不用 source_asset_id：覆盖更新会换 id）
const drillNote = computed(() => {
  const sid = (current.value || {}).id
  if (!sid) return null
  return drillNotes.value.find((n) => ((n.anchor || {}).key) === drillNoteKey(sid)) || null
})

// 陪练报告（C 档）：单场能力画像 + 最该补的要点 + 难度走向。
// ⚠️ 数据**全部来自已拿到的 turns / outline**，不新增后端接口 —— 成绩单本来就全量持有它们。
const report = computed(() => {
  if (view.value !== 'result') return null
  const graded = turns.value.filter((t) => t.score != null)
  if (!graded.length) return null      // 一题都没评上分 → 没有可分析的东西，整块不显示

  // ① 三维能力：只对有该维度的轮次求均值（模型偶尔只返总分，缺失维度不能按 0 拉低）
  const dims = []
  for (const name of ['正确性', '完整度', '表达']) {
    const vals = graded.map((t) => (t.dimensions || {})[name]).filter((v) => typeof v === 'number')
    if (vals.length) {
      dims.push({ name, value: Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) })
    }
  }

  // ② 最该补的要点：跨轮聚合 missing_points —— 按 pointKey() 归一后分组。
  // ⚠️ 旧写法按**精确字符串**计数：「未提到成本」与「没考虑成本」各算 1 次，
  //    同一短板被拆成一堆只出现 1 次的碎片，top5 随即失去意义。
  const cnt = new Map()
  for (const t of turns.value) {
    for (const p of (t.missing_points || [])) {
      const raw = String(p || '').trim()
      if (!raw) continue
      const k = pointKey(raw)
      const rec = cnt.get(k)
      if (!rec) cnt.set(k, { text: raw, n: 1 })
      else {
        rec.n += 1
        if (raw.length < rec.text.length) rec.text = raw   // 展示最短的原始措辞
      }
    }
  }
  const missing = [...cnt.values()]
    .sort((a, b) => b.n - a.n || a.text.localeCompare(b.text)).slice(0, 5)

  // ③ 难度走向：实际难度 ≠ 题纲难度的题（追问不参与，它沿用母题难度）
  const ups = [], downs = []
  for (const t of turns.value) {
    if (t.is_followup || !t.difficulty) continue
    const base = normDiff((outline.value[t.outline_index] || {}).difficulty)
    const used = normDiff(t.difficulty)
    if (used === base) continue
    const no = (t.outline_index || 0) + 1
    if (DIFF_LEVELS.indexOf(used) > DIFF_LEVELS.indexOf(base)) ups.push(no)
    else downs.push(no)
  }
  const seg = []
  if (ups.length) seg.push('第 ' + ups.join('、') + ' 题加难')
  if (downs.length) seg.push('第 ' + downs.join('、') + ' 题降难')
  return { dims, missing, diffNote: seg.join('；') }
})

// ---------- watch ----------
// 范围变化 → 重新统计范围体量（防抖 300ms；接口是纯 DB 统计，不触发检索）
watch(refsKey, () => {
  if (view.value !== 'create') return
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(refreshPreview, 300)
})

// ---------- 工具 ----------
function statusOf(s) { return STATUS_MAP[s] || { label: s || '—', cls: 'draft' } }
function verdictLabel(v) { return VERDICT_MAP[v] || '' }
function scoreColor(s) {
  if (s == null) return 'var(--asc-text-3)'
  if (s >= 80) return '#2e9e5b'
  if (s >= 60) return '#b45309'
  return '#d64545'
}
function dimsOf(t) {
  const d = t.dimensions || {}
  return ['正确性', '完整度', '表达']
    .filter((k) => typeof d[k] === 'number')
    .map((k) => ({ name: k, value: Math.max(0, Math.min(100, Math.round(d[k]))) }))
}
// 难度标注：仅当「实际难度 ≠ 题纲难度」时返回（旧数据无 difficulty → 自然不标注）
// 复算「这题为什么被调难 / 调易」：用后端下发的自适应参数在本地跑一遍同一规则。
// ⚠️ 只有**复算结果与实际调整方向一致**时才展示依据 —— 参数被改过、复算对不上时
//    宁可不给依据（泛化文案），也不给一个可能错的「依据」（假信息比不显示更糟）。
function adaptDriver(t, adapt) {
  if (!adapt || !t) return null
  const base = normDiff((outline.value[t.outline_index] || {}).difficulty)
  const used = normDiff(t.difficulty)
  const wantShift = DIFF_LEVELS.indexOf(used) - DIFF_LEVELS.indexOf(base)
  if (!wantShift) return null
  const prior = turns.value
    .filter((x) => x.score != null && !x.is_followup && x.outline_index != null
      && x.outline_index < t.outline_index)
    .sort((a, b) => (a.seq || 0) - (b.seq || 0))
  let up = 0, down = 0
  const drivers = []
  for (let i = prior.length - 1; i >= 0; i -= 1) {
    const sc = prior[i].score
    if (sc >= adapt.up_score) { if (down) break; up += 1; drivers.unshift(prior[i]) }
    else if (sc <= adapt.down_score) { if (up) break; down += 1; drivers.unshift(prior[i]) }
    else break
  }
  let shift = 0
  if (up >= adapt.streak * 2) shift = 2
  else if (up >= adapt.streak) shift = 1
  else if (down >= adapt.streak * 2) shift = -2
  else if (down >= adapt.streak) shift = -1
  shift = Math.max(-adapt.max_shift, Math.min(adapt.max_shift, shift))
  if (shift !== wantShift) return null       // 复算不吻合 → 不给依据
  return drivers.map((x) => ({ no: (x.outline_index || 0) + 1, score: x.score }))
}

function diffBadge(t) {
  if (!t || t.is_followup || !t.difficulty) return null
  const used = normDiff(t.difficulty)
  const base = normDiff((outline.value[t.outline_index] || {}).difficulty)
  if (used === base) return null
  const up = DIFF_LEVELS.indexOf(used) > DIFF_LEVELS.indexOf(base)
  const drv = adaptDriver(t, (current.value || {}).adapt)
  const why = (drv && drv.length)
    ? '依据：' + drv.map((d) => '第 ' + d.no + ' 题 ' + d.score + ' 分').join('、')
      + (up ? ' 连续高分 → 加难' : ' 连续低分 → 降难')
    : '按前序得分自动调整'
  return {
    label: (up ? '已加难 · ' : '已降难 · ') + (DIFF_LABEL[used] || used),
    cls: up ? 'up' : 'down',
    tip: '题纲原定「' + (DIFF_LABEL[base] || base) + '」→ 本题用「'
      + (DIFF_LABEL[used] || used) + '」；' + why,
  }
}

function scopeSummary(s) {
  const n = (s.scope_refs || []).length
  const base = SCOPE_LABEL[s.scope] || s.scope || '—'
  if (s.scope === 'all') return base
  return n ? base + ' · ' + n + ' 项' : base
}
function relTime(iso) {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  const diff = Date.now() - d.getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return min + ' 分钟前'
  const hour = Math.floor(min / 60)
  if (hour < 24) return hour + ' 小时前'
  const day = Math.floor(hour / 24)
  if (day === 1) return '昨天'
  if (day < 7) return day + ' 天前'
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}
function scrollBottom() {
  nextTick(() => { if (bodyRef.value) bodyRef.value.scrollTop = bodyRef.value.scrollHeight })
}

// 把表单范围转成后端约定的 scope_refs：[{type,id,title}]
function buildRefs() {
  if (form.scope === 'material') {
    return form.materialIds.map((id) => ({
      type: 'material', id, title: ((materialOptions.value.find((x) => x.id === id) || {}).title) || '',
    }))
  }
  if (form.scope === 'note') {
    return form.noteIds.map((id) => ({
      type: 'note', id, title: ((noteOptions.value.find((x) => x.id === id) || {}).title) || '',
    }))
  }
  if (form.scope === 'folder') {
    return form.folderIds.map((id) => ({
      type: 'folder', id, title: ((folderOptions.value.find((x) => x.id === id) || {}).name) || '',
    }))
  }
  return []
}

// ---------- 数据加载 ----------
async function loadSessions() {
  listLoading.value = true
  try {
    const { data } = await drillApi.list({
      q: sessionSearch.value.trim() || undefined, offset: 0, limit: LIMIT,
    })
    sessions.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error(errMsg(e, '加载陪练记录失败'))
  } finally {
    listLoading.value = false
  }
}

// 跨场次趋势（优化 3）：服务端聚合已落库漏点 ——「报告只在前端算」时这件事做不到
async function loadTrend() {
  try {
    const { data } = await drillApi.trend(20)
    trend.value = data || null
  } catch { trend.value = null }
}

const hasMore = computed(() => sessions.value.length < total.value)

// 「加载更多」按已显示条数续取下一页并**追加**（不是替换）—— 替换会让用户
// 刚翻到的第 2 页整屏跳变、滚动位置丢失。搜索词沿用当前的，避免串页。
async function loadMore() {
  if (loadingMore.value || !hasMore.value) return
  loadingMore.value = true
  try {
    const { data } = await drillApi.list({
      q: sessionSearch.value.trim() || undefined,
      offset: sessions.value.length, limit: LIMIT,
    })
    sessions.value = sessions.value.concat(data.items || [])
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error(errMsg(e, '加载更多失败'))
  } finally {
    loadingMore.value = false
  }
}

function clearSearch() { sessionSearch.value = ''; loadSessions() }

async function loadOptions() {
  try {
    const [mats, kb, folders] = await Promise.all([
      materialApi.list(), kbApi.overview(), foldersApi.list(),
    ])
    materialOptions.value = (mats.data || []).filter((m) => m.parsed_status === 'success')
    noteOptions.value = (kb.data || {}).notes || []
    folderOptions.value = folders.data || []
  } catch { /* 静默：选项缺失时范围选择器仍可用（全库范围不依赖它们） */ }
}

async function refreshPreview() {
  previewing.value = true
  try {
    const { data } = await drillApi.scopePreview({ scope: form.scope, scope_refs: buildRefs() })
    preview.value = data
  } catch {
    preview.value = null
  } finally {
    previewing.value = false
  }
}

// ---------- SSE ----------
// 后端事件：stage / outline / question / score / finish / done / error
// 心跳是 ": ping"（无 event: 行）→ 在下面被自然忽略
async function runStream(url, body, handlers) {
  if (streamCtrl) { try { streamCtrl.abort() } catch { /* 忽略 */ } }
  const ctrl = new AbortController()
  streamCtrl = ctrl
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal: ctrl.signal,
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.detail || ('请求失败 ' + resp.status))
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop()
      for (const evt of events) {
        const lines = evt.split('\n')
        const ev = lines.find((l) => l.startsWith('event:'))?.slice(6).trim()
        const dataLine = lines.find((l) => l.startsWith('data:'))?.slice(5)
        if (!ev || !dataLine) continue
        let payload = {}
        try { payload = JSON.parse(dataLine) } catch { continue }
        if (handlers[ev]) handlers[ev](payload)
      }
    }
  } finally {
    if (streamCtrl === ctrl) streamCtrl = null
  }
}

function onQuestion(p) {
  if (!turns.value.some((t) => t.id === p.turn_id)) {
    turns.value.push({
      id: p.turn_id, seq: p.seq, outline_index: p.outline_index || 0,
      is_followup: !!p.is_followup, question: p.question,
      user_answer: null, score: null, verdict: null, dimensions: null,
      comment: '', missing_points: [], expected_points: [],
      source_title: '', skipped: false, _pending: false,
      difficulty: p.difficulty || '',
    })
  }
  if (p.progress) { progress.cur = p.progress.cur; progress.total = p.progress.total }
  phase.value = 'question'
  answer.value = ''
  scrollBottom()
}

function onScore(p) {
  let t = turns.value.find((x) => x.seq === p.seq)
  if (!t) {
    // 出题失败的考点：后端只发 score(跳过) 不发 question（避免出现假的待作答题），
    // 所以这里必须补建一张卡，否则该考点在结果页凭空消失、题数与分母对不上。
    // ⚠️ 只在带 turn_id 的跳过事件上补建；其余「找不到」仍按乱序事件忽略。
    if (!p.skipped || !p.turn_id) return
    t = {
      id: p.turn_id, seq: p.seq, outline_index: p.outline_index || 0,
      is_followup: !!p.is_followup, question: p.question || '',
      user_answer: '', score: null, verdict: null, dimensions: null,
      comment: '', missing_points: [], expected_points: [],
      source_title: '', skipped: true, _pending: false,
      difficulty: p.difficulty || '',
    }
    turns.value.push(t)
  }
  t.skipped = !!p.skipped
  t.score = p.score
  t.verdict = p.verdict
  t.dimensions = p.dimensions || null
  t.comment = p.comment || ''
  t.missing_points = p.missing_points || []
  if (p.expect_points && p.expect_points.length) t.expected_points = p.expect_points
  t._pending = false
  // 降级（出题失败 / 评分失败）：不阻断后续，但给一次轻提示，别让用户以为是正常的
  if (p.failed) ElMessage.warning(p.message || p.comment || '本题评分失败，可稍后重试')
  if (p.progress) { progress.cur = p.progress.cur; progress.total = p.progress.total }
  scrollBottom()
}

function onFinish(p) {
  summary.value = p
  phase.value = 'finished'
  view.value = 'result'
  scrollBottom()
  loadDrillNote()      // 成绩单出现时才需要知道「本场转过笔记没有」
}

function onStreamError(p) {
  const msg = (p && p.message) || '生成失败，请重试'
  if (guideIfNeedsSetupMsg(msg)) {
    failMsg.value = msg.replace('[NEED_SETUP]', '')
  } else {
    failMsg.value = msg
  }
  phase.value = 'failed'
  ElMessage.error(failMsg.value)
}

function throwMessage(e) {
  if (e && e.name === 'AbortError') return null
  return (e && e.message) || '请求失败，请重试'
}

// ---------- 会话流程 ----------
function applyDetail(d) {
  current.value = d
  outline.value = (d.outline || []).map((p) => ({
    point: p.point || '', difficulty: p.difficulty || 'medium',
  }))
  const ts = (d.turns || []).map((t) => ({ ...t, skipped: t.user_answer === '', _pending: false }))
  turns.value = ts
  progress.total = outline.value.length
  const last = ts[ts.length - 1]
  progress.cur = last ? (last.outline_index || 0) + 1 : 0
}

async function startStream(sid) {
  phase.value = 'outline'
  stageText.value = '正在整理考点…'
  failMsg.value = ''
  try {
    await runStream(drillApi.startStreamUrl(sid), {}, {
      stage: (p) => {
        stageText.value = p.text || ''
        if (p.stage === 'score') phase.value = 'scoring'
        else if (p.stage === 'question') phase.value = 'question'
        else if (p.stage === 'outline') phase.value = 'outline'
      },
      outline: (p) => {
        outline.value = (p.points || []).map((x) => ({ point: x.point || '', difficulty: x.difficulty || 'medium' }))
        if (p.count) progress.total = p.count
      },
      question: onQuestion,
      // ⚠️ 出题阶段也可能收到 score / finish —— 出题失败的考点降级成 score(skipped)，
      //    若全部考点都出题失败则直接 finish。这两个 handler 不注册的话，
      //    降级事件会被**静默丢弃**（页面少一张卡、也看不到「已跳过」）。
      score: onScore,
      finish: onFinish,
      done: () => { if (phase.value !== 'finished') phase.value = turns.value.length ? 'question' : phase.value },
      error: onStreamError,
    })
  } catch (e) {
    const msg = throwMessage(e)
    if (msg) { failMsg.value = msg; phase.value = 'failed'; ElMessage.error(msg) }
  }
}

// 附着 / 继续 / 开始（draft 与 failed 也会由后端按状态正确处理）
function attach() {
  if (!current.value) return
  startStream(current.value.id)
}

async function retry() {
  if (!current.value) return
  retrying.value = true
  try {
    // 先用 DB 权威状态重建本地视图，再附着：
    //  - failed / draft：后端会先删掉半成品轮次再重跑 → 本地也必须清空，
    //    否则重新下发的题会与残留的旧轮次重复。
    //  - 其他（running）：只附着，**绝不能清空**——否则已作答的历史题会全部消失
    //    （断点续跑只补发「当前这一题」，不会重发前面答过的题）。
    const { data } = await drillApi.detail(current.value.id)
    if (data.status === 'failed' || data.status === 'draft') {
      current.value = data
      turns.value = []
      outline.value = []
      progress.cur = 0
      progress.total = 0
    } else {
      applyDetail(data)
    }
    failMsg.value = ''
    phase.value = 'idle'
    await startStream(current.value.id)
  } catch (e) {
    const msg = throwMessage(e) || errMsg(e, '重试失败')
    failMsg.value = msg
    phase.value = 'failed'
    ElMessage.error(msg)
  } finally {
    retrying.value = false
  }
}

async function submitAnswer(skip) {
  if (submitting.value) return
  const t = unansweredTurn.value
  if (!t || !current.value) return
  const txt = stripVoiceMark(answer.value).trim()
  if (!skip && !txt) { ElMessage.warning('先写点什么再提交'); return }
  // A2：前端先拦一道（后端 L400 也拦，但等一个来回体验差）
  if (!skip && answerOver.value) {
    ElMessage.warning('作答过长，请精简到 ' + MAX_ANSWER + ' 字以内')
    return
  }

  const seq = t.seq
  t.user_answer = skip ? '' : txt
  t.skipped = !!skip
  t._pending = true
  submitting.value = true
  phase.value = 'scoring'
  stageText.value = skip ? '已跳过本题…' : 'AI 正在评分…'
  if (!skip) answer.value = ''
  scrollBottom()

  try {
    await runStream(drillApi.answerStreamUrl(current.value.id), { seq, answer: txt, skip }, {
      stage: (p) => { stageText.value = p.text || stageText.value },
      score: onScore,
      question: onQuestion,
      finish: onFinish,
      done: () => { if (phase.value !== 'finished') phase.value = 'question' },
      error: onStreamError,
    })
  } catch (e) {
    const msg = throwMessage(e)
    if (msg) { failMsg.value = msg; phase.value = 'failed'; ElMessage.error(msg) }
  } finally {
    t._pending = false
    submitting.value = false
  }
}

async function finishEarly() {
  if (!current.value) return
  try {
    await ElMessageBox.confirm(
      '提前结束将按已作答的题目计算总分，并把薄弱题加入复习队列。确定结束吗？',
      '结束陪练',
      { confirmButtonText: '结束并看成绩', cancelButtonText: '继续答题', type: 'warning' },
    )
  } catch { return }
  try {
    await drillApi.finish(current.value.id, 'abort')
    const { data } = await drillApi.detail(current.value.id)
    applyDetail(data)
    summary.value = null
    phase.value = 'finished'
    view.value = 'result'
    loadSessions()
  } catch (e) {
    ElMessage.error(errMsg(e, '结束失败，请重试'))
  }
}


// ---------- 成绩单转笔记（C 档） ----------

// 只拉「材料无关笔记」的 anchor（slim=1 → 不下发正文），判断本场转过没有
async function loadDrillNote() {
  try {
    const { data } = await noteApi.list(null, { slim: 1 })
    drillNotes.value = (data || []).filter((n) => n.source_type === 'drill_report')
  } catch { drillNotes.value = [] }
}

function openDrillNote() {
  if (!drillNote.value) return
  noteView.id = Number(drillNote.value.id)
  noteView.show = true
}

function buildNoteContent() {
  const s = current.value || {}
  const st = resultStats.value
  const cfg = s.config || {}
  const L = []
  L.push('# ' + (s.title || '陪练成绩单'))
  L.push('')
  L.push('- 总评分：' + (st.total_score != null ? st.total_score + ' 分' : '—（本场无已评分题目）'))
  L.push('- 完成情况：' + st.answered_points + ' / ' + st.outline_count + ' 题'
    + (st.graded_count ? '，有效评分 ' + st.graded_count + ' 题' : ''))
  L.push('- 薄弱题：' + st.weak_count + ' 道'
    + (writtenCount.value ? '（已加入复习队列 ' + writtenCount.value + ' 道）' : ''))
  L.push('- 范围：' + scopeSummary(s) + ' ｜ 题量 ' + (cfg.question_count || '—')
    + (cfg.adaptive === false ? ' ｜ 未开难度自适应' : ' ｜ 难度自适应'))
  if (report.value && report.value.dims.length) {
    L.push('- 本次能力：' + report.value.dims.map((d) => d.name + ' ' + d.value).join(' ｜ '))
  }
  if (report.value && report.value.diffNote) L.push('- 难度调整：' + report.value.diffNote)
  L.push('')
  L.push('## 逐题记录')
  for (const t of turns.value) {
    const no = t.is_followup ? '追问' : '第 ' + ((t.outline_index || 0) + 1) + ' 题'
    let head = '### ' + no
    if (t.score != null) head += ' · ' + t.score + ' 分' + (t.verdict ? '（' + verdictLabel(t.verdict) + '）' : '')
    else if (t.skipped) head += ' · 已跳过'
    else if (t.user_answer !== null) head += ' · 未评分'
    L.push('')
    L.push(head)
    L.push('')
    L.push('**题目**：' + (t.question || '—'))
    L.push('')
    L.push('**我的作答**：' + (t.user_answer ? t.user_answer : '（未作答）'))
    if (t.comment) { L.push(''); L.push('**点评**：' + t.comment) }
    if ((t.missing_points || []).length) {
      L.push(''); L.push('**未覆盖的要点**：')
      for (const p of t.missing_points) L.push('- ' + p)
    }
    if ((t.expected_points || []).length) {
      L.push(''); L.push('**参考答案要点**：')
      for (const p of t.expected_points) L.push('- ' + p)
    }
  }
  const weak = turns.value.filter((t) => t.verdict === 'partial' || t.verdict === 'wrong')
  if (weak.length) {
    L.push('')
    L.push('## 薄弱考点（已沉淀进复习队列）')
    for (const t of weak) {
      L.push('- ' + String(t.question || '').slice(0, 60)
        + (t.score != null ? '（' + t.score + ' 分）' : ''))
    }
  }
  return L.join('\n')
}

async function saveToNote() {
  const s = current.value || {}
  if (!s.id) return
  const content = buildNoteContent()
  if (content.trim().length < 2) { ElMessage.warning('成绩单内容为空，无法转笔记'); return }
  // ⚠️ source_type / anchor.kind 必须是新值 'drill_report' —— **绝不能**用 'summary' /
  //    'learn_note'：学习页按 anchor.kind 认领「本材料的摘要 / 讲义笔记」，共用会让它
  //    误认领这条成绩单，因缺 version 而弹出错误的「摘要已更新」覆盖确认（已踩过两次）。
  // ⚠️ 走 /notes/from-ai 而不是 /notes：陪练天然跨多材料（scope 可能是 all / folder），
  //    挂不到单一 material_id 上；from-ai 的 anchor.key 幂等正好支撑「二次点击＝覆盖更新」。
  const anchor = {
    kind: 'drill_report',
    drill_session_id: s.id,
    key: drillNoteKey(s.id),
    total_score: resultStats.value.total_score,
  }
  noteBusy.value = true
  try {
    if (drillNote.value) {
      await ElMessageBox.confirm(
        '本场成绩单已转过笔记。更新后，你在笔记里做过的编辑会被最新成绩单内容覆盖。',
        '更新笔记', { type: 'warning', confirmButtonText: '覆盖更新', cancelButtonText: '取消' })
      const { data } = await noteApi.update(drillNote.value.id, { content, anchor })
      if (data && data.reindex_warning) ElMessage.warning(data.reindex_warning)
      else ElMessage.success('笔记已更新')
    } else {
      const { data } = await noteApi.fromAi({
        title: (s.title || '陪练') + ' · 成绩单', content, source_type: 'drill_report', anchor,
      })
      if (data && data.note && data.note.reindex_warning) ElMessage.warning(data.note.reindex_warning)
      else ElMessage.success('已转成笔记')
    }
    await loadDrillNote()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(errMsg(e, '转笔记失败'))
  } finally {
    noteBusy.value = false
  }
}

// ---------- 语音（C 档）：念题 TTS + 口述作答 ASR ----------

// 语音标记只为校对服务：提交前剥掉，别把「〔语音〕」送进评分文本
function stripVoiceMark(s) { return String(s == null ? '' : s).replace(/〔语音〕/g, '') }

// 走 blob 的请求失败时，后端返回的 JSON 被包成了 Blob，errMsg 取不到 detail → 单独解一遍
async function blobErrMsg(e, fallback) {
  const d = e && e.response && e.response.data
  if (d instanceof Blob) {
    try {
      const j = JSON.parse(await d.text())
      if (j && j.detail) return String(j.detail)
    } catch { /* 不是 JSON → 走通用兜底 */ }
  }
  return errMsg(e, fallback)
}

// 停掉正在念的题并释放资源；返回「原本是否真的在念」。
function stopSpeaking() {
  const a = speakAudio
  speakAudio = null
  speakToken += 1                 // 让还在飞的合成响应作废
  if (a) {
    // ⚠️ 先摘回调再 pause：否则 pause 触发的 onended/onerror 会再绕回来一次
    try { a.onended = a.onerror = null; a.pause() } catch { /* 忽略 */ }
  }
  if (speakUrl) { URL.revokeObjectURL(speakUrl); speakUrl = '' }
  speaking.value = false
  return !!a
}

// 念题：后端合成 mp3 → 浏览器播放。
// ⚠️ 用 blob URL + Audio 而不是 <audio src="...">：题干可能上千字，塞不进 URL（POST 才行）。
async function speakQuestion(t) {
  // 正在念 → 这次点击是「停止」（否则用户想打断只能干等它念完）
  if (stopSpeaking()) return
  const text = (t && t.question) || ''
  if (!text.trim()) return
  speaking.value = true
  const my = speakToken
  try {
    const { data } = await drillApi.tts(text)
    // ⚠️ 合成期间用户又点过（停止 / 换题）→ 这次结果已过期，丢弃，否则两段声音会叠加
    if (my !== speakToken) return
    const url = URL.createObjectURL(data)
    const audio = new Audio(url)
    speakAudio = audio
    speakUrl = url
    // ⚠️ 必须在 play() **之前**挂回调，且**不能**用 `await audio.play()` 的 resolve 复位守卫：
    //    play() 在「开始播放」时就 resolve，用它复位会让守卫瞬间失效 → 连点即叠加播放。
    //    复位时机取真正播完（onended / onerror）。
    audio.onended = audio.onerror = () => { if (speakAudio === audio) stopSpeaking() }
    await audio.play()
  } catch (e) {
    stopSpeaking()
    ElMessage.warning(await blobErrMsg(e, '念题失败，请重试'))
  }
}

// 口述作答：录音 → 转写 → **只填入作答框**，由用户校对后自己点提交。
// ⚠️ 刻意**不自动提交**：ASR 必有错字，自动提交等于把错字直接送去评分。
async function toggleRecord() {
  if (recording.value) { stopRecord(); return }
  asrHint.value = ''
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    asrHint.value = '当前浏览器不支持录音，请改用文字作答'
    return
  }
  let stream
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch {
    asrHint.value = '麦克风不可用或未授权'
    return
  }
  try {
    mediaRec = new MediaRecorder(stream)
  } catch {
    teardownStream(stream)
    asrHint.value = '当前环境不支持录音'
    return
  }
  recChunks = []
  mediaRec.ondataavailable = (ev) => { if (ev.data && ev.data.size) recChunks.push(ev.data) }
  mediaRec.onstop = async () => {
    teardownStream(stream)
    stopRecTimer()
    recording.value = false
    const mime = (mediaRec && mediaRec.mimeType) || 'audio/webm'
    const blob = new Blob(recChunks, { type: mime })
    recChunks = []
    // 手一抖点两下会产生几十毫秒的空录音 → 别拿它去喂模型（必然识别成空/噪声）
    if (blob.size < 1200) { asrHint.value = ''; return }
    transcribing.value = true
    try {
      const { data } = await drillApi.asr(blob, 'answer.webm')
      const text = ((data || {}).text || '').trim()
      if (!text) { asrHint.value = '没听清，请再说一遍'; return }
      // **追加**而非覆盖：用户可能已经手写了一半，再补一段口述
      // ⚠️ 前缀可见标记「〔语音〕」—— ASR 必有错字，用户要能一眼看出哪段需要重点校对；
      //    提交时由 stripVoiceMark() 剥掉，不让标记混进送去评分的文本。
      answer.value = (answer.value ? answer.value.replace(/\s+$/, '') + '\n' : '') + VOICE_MARK + text
      asrHint.value = ''
      ElMessage.success('已转写，请校对后提交')
    } catch (e) {
      const msg = await blobErrMsg(e, '语音识别失败')
      if (msg.indexOf('[NEED_ASR]') >= 0) {
        asrHint.value = ''
        ElMessageBox.confirm(msg.replace('[NEED_ASR]', ''), '需要先下载语音模型',
          { confirmButtonText: '去管理中心下载', cancelButtonText: '稍后', type: 'info' })
          .then(() => router.push('/settings')).catch(() => {})
      } else {
        asrHint.value = msg
      }
    } finally {
      transcribing.value = false
    }
  }
  recSeconds.value = 0
  recTimer = setInterval(() => {
    recSeconds.value += 1
    if (recSeconds.value >= REC_MAX_SECONDS) {
      // 自动停：否则用户会一直录到上传时才被 12MB 上限拒掉（白录十分钟）
      ElMessage.info('已录满 ' + Math.round(REC_MAX_SECONDS / 60) + ' 分钟，自动结束录音')
      stopRecord()
    }
  }, 1000)
  mediaRec.start()
  recording.value = true
}

function stopRecord() {
  if (mediaRec && mediaRec.state !== 'inactive') {
    try { mediaRec.stop() } catch { /* 忽略 */ }
  } else {
    recording.value = false
    stopRecTimer()
  }
}

function stopRecTimer() {
  if (recTimer) { clearInterval(recTimer); recTimer = null }
}

function teardownStream(stream) {
  try { (stream.getTracks() || []).forEach((x) => x.stop()) } catch { /* 忽略 */ }
}

// 组件销毁 / 切走时必须收干净：否则麦克风占用不放、计时器继续跑
function teardownRecording() {
  stopRecTimer()
  if (mediaRec) {
    try {
      if (mediaRec.stream) teardownStream(mediaRec.stream)
      // ⚠️ 必须**先摘掉 onstop 再 stop()**：stop() 触发 onstop 是**异步**的，不摘的话
      //    切走页面后仍会把半段录音发去转写 → 白传一次，还在**新页面**上弹「已转写」，
      //    用户完全对不上发生了什么。
      mediaRec.onstop = null
      mediaRec.ondataavailable = null
      if (mediaRec.state !== 'inactive') mediaRec.stop()
    } catch { /* 忽略 */ }
    mediaRec = null
  }
  recording.value = false
  recChunks = []
}

// ---------- 导航 ----------
// 会话深链（A5）：把当前场次写进 URL，刷新后 onMounted 读回 → 原地恢复。
// ⚠️ 用 replace 而非 push：列表↔会话来回点不该堆出一串历史记录。
function syncRoute(sid) {
  const q = { ...route.query }
  if (sid) q.sid = String(sid)
  else delete q.sid
  router.replace({ path: '/drill', query: q })
}

function gotoCreate() {
  form.title = ''
  form.questionCount = DEFAULT_COUNT
  form.followUp = 'shallow'
  form.showAnswer = true
  form.adaptive = true
  preview.value = null
  view.value = 'create'
  syncRoute(null)
  refreshPreview()
}

async function createAndStart() {
  if (!canStart.value) return
  creating.value = true
  try {
    const { data } = await drillApi.create({
      title: form.title.trim() || null,
      scope: form.scope,
      scope_refs: buildRefs(),
      config: {
        question_count: Number(form.questionCount) || DEFAULT_COUNT,
        follow_up: form.followUp,
        show_answer: !!form.showAnswer,
        adaptive: !!form.adaptive,
      },
    })
    readonly.value = false
    turns.value = []
    outline.value = []
    progress.cur = 0
    progress.total = 0
    summary.value = null
    answer.value = ''
    current.value = data
    view.value = 'session'
    syncRoute(data.id)
    loadSessions()
    startStream(data.id)
  } catch (e) {
    const msg = errMsg(e, '创建失败，请重试')
    if (!guideIfNeedsSetupMsg(msg)) ElMessage.error(msg)
  } finally {
    creating.value = false
  }
}

async function openSession(sid) {
  if (streamCtrl) { try { streamCtrl.abort() } catch { /* 忽略 */ } }
  readonly.value = false
  summary.value = null
  failMsg.value = ''
  try {
    const { data } = await drillApi.detail(sid)
    applyDetail(data)
    syncRoute(sid)
    if (data.status === 'finished') {
      readonly.value = true
      phase.value = 'finished'
      view.value = 'result'
      loadDrillNote()
      return
    }
    view.value = 'session'
    if (data.status === 'draft') {
      // 草稿不自动开跑（可能只是建完就离开了），交给用户点「开始陪练」
      phase.value = 'idle'
    } else if (data.status === 'failed') {
      phase.value = 'failed'
      failMsg.value = data.error || '上次生成中断，可重试'
    } else {
      // outlining / running（含重启后的断点续跑）→ 附着到进行中的任务
      startStream(sid)
    }
  } catch (e) {
    ElMessage.error(errMsg(e, '打开陪练失败'))
    syncRoute(null)          // 深链指向的场次已不存在 → 别把死链留在地址栏
  }
}

function backToList() {
  if (streamCtrl) { try { streamCtrl.abort() } catch { /* 忽略 */ } }
  syncRoute(null)
  view.value = 'list'
  current.value = null
  turns.value = []
  outline.value = []
  summary.value = null
  phase.value = 'idle'
  readonly.value = false
  loadSessions()
  loadTrend()
}

function againSameConfig() {
  const s = current.value || {}
  const cfg = s.config || {}
  form.title = ''
  form.scope = s.scope || 'all'
  form.questionCount = cfg.question_count || DEFAULT_COUNT
  form.followUp = cfg.follow_up || 'shallow'
  form.showAnswer = cfg.show_answer !== false
  // 旧场次没有这个键 → 视为开启（与后端 `cfg.get("adaptive", True)` 同一口径）
  form.adaptive = cfg.adaptive !== false
  form.materialIds = []
  form.noteIds = []
  form.folderIds = []
  for (const r of (s.scope_refs || [])) {
    if (r.type === 'material') form.materialIds.push(r.id)
    else if (r.type === 'note') form.noteIds.push(r.id)
    else if (r.type === 'folder') form.folderIds.push(r.id)
  }
  view.value = 'create'
  syncRoute(null)
  refreshPreview()
}

async function removeSession(s) {
  try {
    await ElMessageBox.confirm(
      '删除后本场陪练的作答与评分记录将不可恢复（已加入复习队列的卡片会保留）。确定删除？',
      '删除陪练记录',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await drillApi.remove(s.id)
    ElMessage.success('已删除')
    loadSessions()
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  }
}

// ---------- 生命周期 ----------
onMounted(async () => {
  await Promise.all([loadOptions(), loadSessions(), loadDrillNote(), loadTrend()])
  // 深链优先（A5）：地址栏带 sid → 直接恢复到该场次（刷新页面原地回到会话/成绩单）
  const sid = Number(route.query.sid)
  if (sid) { await openSession(sid); return }
  // 从材料详情页进入：预置「资料」范围
  const mid = route.query.material_id
  if (mid) {
    form.scope = 'material'
    form.materialIds = [Number(mid)]
  }
})

onUnmounted(() => {
  if (streamCtrl) { try { streamCtrl.abort() } catch { /* 忽略 */ } }
  if (previewTimer) clearTimeout(previewTimer)
  teardownRecording()      // 录音/计时器必须随组件销毁一起收掉，否则麦克风指示灯常亮
  stopSpeaking()           // 念题音频同理：不叫停会一直播下去，且 blob URL 不回收
})
</script>

<style scoped>
.drill-page { padding-bottom: 32px; }

/* ===== 列表 ===== */
.list-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.list-search { max-width: 300px; }
.list-count { font-size: 12.5px; color: var(--asc-text-2); }
.list-more { padding: 18px 0 6px; }
.list-loading { padding: 40px 0; font-size: 13px; color: var(--asc-text-2); }

.session-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px;
}
.session-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 14px; padding: 14px 16px; cursor: pointer;
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease;
}
.session-card:hover {
  box-shadow: var(--asc-shadow-hover); transform: translateY(-1px);
  border-color: rgba(124, 92, 252, .35);
}
.sc-top { display: flex; align-items: flex-start; gap: 8px; }
.sc-title {
  flex: 1; min-width: 0; font-size: 14.5px; font-weight: 600; color: var(--asc-text);
  overflow: hidden; text-overflow: ellipsis; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.45;
}
.sc-status {
  flex-shrink: 0; font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500;
  display: inline-flex; align-items: center; gap: 5px;
}
.sc-status::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.sc-status.draft { background: var(--asc-surface-2); color: var(--asc-text-2); }
.sc-status.running { background: var(--asc-primary-soft); color: var(--asc-primary); }
.sc-status.done { background: rgba(46, 158, 91, .12); color: #2e7d52; }
.sc-status.failed { background: rgba(214, 69, 69, .1); color: #c0392b; }
.sc-meta {
  margin-top: 8px; font-size: 12.5px; color: var(--asc-text-2);
  display: flex; align-items: center; flex-wrap: wrap; gap: 5px;
}
.sc-sep { color: #c0c0c6; }
.sc-scope { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 190px; }
.sc-score { font-weight: 700; font-size: 14px; }
.sc-foot {
  margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--asc-divider);
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12px; color: var(--asc-text-2);
}
.sc-del { color: var(--asc-text-2); cursor: pointer; }
.sc-del:hover { color: #c0392b; }

/* ===== 新建 ===== */
.cr-head { display: flex; align-items: center; gap: 14px; margin-bottom: 18px; }
.cr-head h3 { margin: 0; font-size: 18px; font-weight: 600; color: var(--asc-text); }
.cr-back, .flow-back {
  font-size: 13px; color: var(--asc-text-2); cursor: pointer; user-select: none;
}
.cr-back:hover, .flow-back:hover { color: var(--asc-primary); }
.cr-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 14px; padding: 10px 22px;
}
.cr-row {
  display: flex; align-items: center; gap: 14px;
  padding: 15px 0;
}
.cr-row-block { align-items: flex-start; }
.cr-label {
  flex-shrink: 0; width: 84px; font-size: 13px; font-weight: 600; color: var(--asc-text);
}
.cr-row-block .cr-label { padding-top: 4px; }
.cr-input { max-width: 340px; }
.cr-scope { flex: 1; min-width: 0; }
.cr-tip { font-size: 12px; color: var(--asc-text-2); }
.cr-seg { flex-shrink: 0; }
.cr-preview {
  margin-top: 10px; font-size: 12.5px; color: var(--asc-text-2); line-height: 1.6;
  background: var(--asc-primary-soft); border-radius: 8px; padding: 8px 12px;
}
.cr-preview b { color: var(--asc-primary); font-weight: 600; }
.cr-preview.warn { color: #a44a07; background: rgba(180, 83, 9, .1); }
.cr-actions {
  display: flex; justify-content: flex-start; gap: 10px; margin-top: 18px;
}
.cr-start { min-width: 120px; }

/* ===== 会话 / 成绩单 ===== */
.flow-head {
  display: flex; align-items: center; gap: 12px; margin-bottom: 14px;
}
.flow-title {
  flex: 1; min-width: 0; font-size: 16px; font-weight: 600; color: var(--asc-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.flow-progress { font-size: 12.5px; color: var(--asc-text-2); flex-shrink: 0; }
.flow-progress.done {
  background: rgba(46, 158, 91, .12); color: #2a724b;
  font-weight: 600; padding: 4px 12px; border-radius: 20px;
}

.rs-hero {
  background: linear-gradient(160deg, rgba(124, 92, 252, .08), rgba(124, 92, 252, .015) 55%, var(--asc-card) 80%);
  border: 1px solid var(--asc-border);
  border-radius: 16px; padding: 26px 24px 22px; margin-bottom: 16px;
}
.rs-score { font-size: 44px; font-weight: 700; line-height: 1; letter-spacing: -.5px; }
.rs-unit { font-size: 15px; font-weight: 500; margin-left: 4px; }
.rs-sub { margin-top: 8px; font-size: 13px; color: var(--asc-text-2); }
.rs-stats {
  display: flex; justify-content: flex-start; gap: 0; margin-top: 16px;
  padding-top: 16px; border-top: 1px solid var(--asc-divider);
}
.rs-stat { display: flex; flex-direction: column; gap: 3px; padding: 0 28px; }
.rs-stat:first-child { padding-left: 0; }
.rs-stat + .rs-stat { border-left: 1px solid var(--asc-divider); }
.rs-stat b { font-size: 19px; font-weight: 600; color: var(--asc-text); }
.rs-stat i { font-size: 12px; font-style: normal; color: var(--asc-text-2); }
.rs-writeback {
  margin-top: 14px; display: inline-flex; align-items: center; gap: 6px;
  font-size: 12.5px; color: #2e7d52;
}
.rs-writeback.muted { color: var(--asc-text-2); }

.outline-bar {
  display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px;
}
.outline-note { font-size: 12.5px; color: var(--asc-text-2); margin: -6px 0 12px; }
.outline-note b { color: var(--asc-text); font-weight: 600; }
.outline-chip {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; color: var(--asc-text-2);
  background: var(--asc-surface-2); border-radius: 8px; padding: 4px 9px;
  max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.outline-chip b {
  color: var(--asc-text-2); font-weight: 600; font-size: 11px;
}
.outline-chip.active { background: var(--asc-primary-soft); color: var(--asc-primary); }
.outline-chip.active b { color: var(--asc-primary); }
.outline-chip.done { opacity: .5; }

.stage-banner {
  display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
  padding: 11px 16px; border-radius: 12px;
  background: var(--asc-primary-soft); font-size: 13px; color: var(--asc-primary);
}
.fail-banner {
  display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
  padding: 11px 16px; border-radius: 12px;
  background: rgba(214, 69, 69, .08); font-size: 13px; color: #c0392b;
}
.fail-text { flex: 1; }
.stage-spinner {
  width: 13px; height: 13px; border-radius: 50%; flex-shrink: 0;
  border: 2px solid currentColor; border-top-color: transparent;
  animation: drill-spin .7s linear infinite;
}
@keyframes drill-spin { to { transform: rotate(360deg); } }

.turn-list { display: flex; flex-direction: column; gap: 16px; }
.turn-empty { padding: 30px 0; font-size: 13px; color: var(--asc-text-2); }

.turn {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 14px; padding: 16px 18px;
}
.q-block { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.q-tag {
  font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 20px;
  background: var(--asc-surface-2); color: var(--asc-text-2);
}
.q-tag.followup { background: rgba(180, 83, 9, .12); color: #b45309; }
.q-src { font-size: 11.5px; color: var(--asc-text-2); max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.q-text {
  flex-basis: 100%; margin-top: 4px; font-size: 15px; line-height: 1.65;
  color: var(--asc-text); font-weight: 500;
}
.a-block { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--asc-divider); }
.a-label { font-size: 12px; font-weight: 500; letter-spacing: .02em; color: var(--asc-text-2); margin-bottom: 4px; }
.a-text { font-size: 13.5px; line-height: 1.7; color: var(--asc-text); white-space: pre-wrap; word-break: break-word; border-left: 3px solid rgba(124, 92, 252, .3); padding-left: 12px; }
.a-text.skipped { color: var(--asc-text-2); font-style: italic; border-left-color: var(--asc-divider); }

.score-card {
  margin-top: 12px; border-radius: 12px; padding: 14px 16px;
  background: var(--asc-card); border: 1px solid var(--asc-divider);
  border-left: 4px solid var(--asc-border);
}
.score-card.correct { border-left-color: #2e7d52; }
.score-card.partial { border-left-color: #b45309; }
.score-card.wrong { border-left-color: #c0392b; }
.score-card.pending { color: var(--asc-primary); font-size: 13px; display: flex; align-items: center; gap: 8px; }
.score-card.skipped, .score-card.failed { font-size: 13px; display: flex; align-items: center; gap: 8px; }
.sc-ico { flex-shrink: 0; color: var(--asc-text-3); }
.score-card.failed .sc-ico { color: #b45309; }
.sc-verdict-skipped { color: var(--asc-text-2); }
.sc-verdict-failed { color: #b45309; }
.score-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.score-num { font-size: 26px; font-weight: 700; line-height: 1; }
.score-verdict {
  font-size: 11.5px; font-weight: 600; padding: 2px 9px; border-radius: 20px;
  background: rgba(46, 158, 91, .12); color: #2e7d52;
}
.score-verdict.partial { background: rgba(180, 83, 9, .12); color: #b45309; }
.score-verdict.wrong { background: rgba(214, 69, 69, .12); color: #c0392b; }
.score-dims { display: flex; gap: 14px; flex-wrap: wrap; margin-left: auto; }
.dim { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--asc-text-2); }
.dim i { font-style: normal; }
.dim-bar { width: 54px; height: 5px; border-radius: 3px; background: rgba(0, 0, 0, .08); overflow: hidden; }
.dim-fill { display: block; height: 100%; border-radius: 3px; }
.dim b { font-weight: 600; color: var(--asc-text); min-width: 20px; text-align: right; }
.score-comment { margin: 10px 0 0; font-size: 13.5px; line-height: 1.7; color: var(--asc-text); }
.score-sub-label { font-size: 12px; color: var(--asc-text-2); margin: 10px 0 4px; }
.score-miss ul, .score-expect ul { margin: 0; padding-left: 18px; }
.score-miss li, .score-expect li { font-size: 13px; line-height: 1.7; color: var(--asc-text); margin-bottom: 2px; }
.score-expect { border-top: 1px solid var(--asc-border); margin-top: 8px; padding-top: 4px; }

.answer-box { margin-top: 14px; }
.answer-actions {
  display: flex; align-items: center; gap: 10px; margin-top: 10px;
}
.answer-hint { flex: 1; font-size: 12px; color: var(--asc-text-2); }
.answer-hint.over { color: #b45309; font-weight: 600; }
.rs-actions { display: flex; justify-content: flex-start; gap: 10px; margin-top: 22px; }

/* ===== 陪练报告（C 档） ===== */
.rs-report {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 16px; padding: 18px 22px; margin-bottom: 16px;
}
.rp-title { font-size: 14px; font-weight: 600; color: var(--asc-text); margin-bottom: 12px; }
.rp-dims { display: flex; flex-direction: column; gap: 9px; }
.rp-dim { display: flex; align-items: center; gap: 10px; }
.rp-dim-name { width: 46px; flex-shrink: 0; font-size: 12.5px; color: var(--asc-text-2); }
.rp-dim-track {
  flex: 1; height: 7px; border-radius: 4px; background: rgba(0, 0, 0, .07); overflow: hidden;
}
.rp-dim-fill { display: block; height: 100%; border-radius: 4px; transition: width .3s ease; }
.rp-dim-val { width: 30px; text-align: right; font-size: 12.5px; color: var(--asc-text); }
.rp-sub { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--asc-divider); }
.rp-sub-title { font-size: 12.5px; color: var(--asc-text-2); margin-bottom: 7px; }
.rp-list { margin: 0; padding-left: 18px; }
.rp-list li { font-size: 13px; line-height: 1.75; color: var(--asc-text); margin-bottom: 2px; }
.rp-cnt {
  display: inline-block; margin-right: 6px; padding: 1px 6px; border-radius: 20px;
  font-size: 11px; font-weight: 600; background: rgba(214, 69, 69, .1); color: #c0392b;
}
.rp-diff { margin: 0; font-size: 13px; line-height: 1.7; color: var(--asc-text); }

/* ===== 难度标注 ===== */
.q-diff {
  font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 20px;
  /* ⚠️ 对比度必须按**胶囊自身底色**算，不能按白底：
     --asc-text-2(#6e6e6e) 落在 --asc-surface-2(#f0f0f2) 上只有 4.48:1（不足 AA 4.5）
     → 这里显式取 #6a6a6a（实测 4.75:1）。 */
  background: var(--asc-surface-2); color: #6a6a6a;
}
/* 底色是「白底 + 12% 强调色」的合成色 → 原来的 #b45309 / #2e7d52 实测仅 4.25 / 4.41:1 → 各压深一档 */
.q-diff.up { background: rgba(180, 83, 9, .12); color: #a44a07; }
.q-diff.down { background: rgba(46, 158, 91, .12); color: #2a724b; }

/* ===== 语音工具行 ===== */
.answer-tools { display: flex; align-items: center; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.rec-hint { font-size: 12px; color: var(--asc-text-2); display: inline-flex; align-items: center; gap: 6px; }
.rec-hint.err { color: #b45309; }
.rec-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #d64545;
  animation: rec-pulse 1s ease-in-out infinite;
}
@keyframes rec-pulse { 0%, 100% { opacity: 1 } 50% { opacity: .25 } }
.answer-tools .rec-on {
  border-color: #d64545 !important; color: #d64545 !important;
}

/* ===== 跨场次趋势（优化 3） ===== */
.td-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 14px; padding: 14px 18px; margin: 0 0 16px;
}
.td-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.td-title { font-size: 13.5px; font-weight: 600; color: var(--asc-text); }
.td-sub { font-size: 12px; color: var(--asc-text-2); }
.td-body { display: flex; gap: 22px; align-items: flex-start; flex-wrap: wrap; }
.td-col { flex: 1 1 260px; min-width: 0; }
.td-col-bar { flex: 0 1 auto; }
.td-label { font-size: 12px; color: var(--asc-text-2); margin-bottom: 7px; }
.td-list { margin: 0; padding: 0; list-style: none; }
.td-list li { display: flex; align-items: center; gap: 8px; font-size: 12.5px; line-height: 1.95; }
/* 对比度按卡片白底算：#a44a07 ≈ 5.0:1 / #2a724b ≈ 5.1:1 / #6a6a6a ≈ 5.4:1 */
.td-delta { flex-shrink: 0; font-size: 11px; font-weight: 600; }
.td-delta.up { color: #a44a07; }
.td-delta.down { color: #2a724b; }
.td-delta.flat { color: #6a6a6a; }
.td-text { flex: 1; min-width: 0; color: var(--asc-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.td-cnt { flex-shrink: 0; font-size: 11px; color: var(--asc-text-2); }
.td-empty { margin: 0; font-size: 12.5px; color: var(--asc-text-2); }
.td-bars { display: flex; align-items: flex-end; gap: 4px; height: 56px; }
.td-bar-wrap { width: 12px; height: 100%; display: flex; align-items: flex-end; }
.td-bar { display: block; width: 100%; border-radius: 3px 3px 0 0; min-height: 3px; }

/* ===== 响应式（本页原先 0 断点；新块虽已 flex-wrap，窄屏仍需真正收敛） ===== */
@media (max-width: 760px) {
  .rs-report { padding: 14px 15px; border-radius: 13px; }
  .td-card { padding: 13px 15px; }
  .td-body { gap: 14px; }
  .rp-dim-name { width: 42px; }
  .session-grid { grid-template-columns: 1fr; }
  .rs-stat { padding: 0 16px; }
}
@media (max-width: 560px) {
  .answer-actions { flex-wrap: wrap; }
  .answer-hint { flex-basis: 100%; }
  .answer-tools { gap: 8px; }
  .rp-list li { font-size: 12.5px; }
}
</style>
