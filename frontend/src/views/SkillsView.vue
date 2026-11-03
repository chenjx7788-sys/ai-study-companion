<template>
  <div class="page skills-page">
    <!-- 吸顶区：页头 + 筛选（与材料库同款：滚动时搜索与主操作保持在手边） -->
    <div class="sticky-head">
      <!-- 页头：标题 + 统计 / 搜索 + 主操作（对齐材料库的单行紧凑布局） -->
      <div class="page-header">
        <div class="header-left">
          <div class="skills-head-icon" aria-hidden="true">
            <svg viewBox="0 0 16 16" width="22" height="22" fill="none" stroke="currentColor"
                 stroke-width="1.3" stroke-linecap="round">
              <rect x="2.4" y="3.2" width="11.2" height="9.6" rx="2"/>
              <path d="M5 6.4h6M5 8.8h6M5 11.2h3.6"/>
            </svg>
          </div>
          <h2>技能库</h2>
          <span class="header-sub">共 {{ items.length }} 个技能<template v-if="items.length"> · 启用 {{ enabledCount }} · 语义候选 {{ residentCount }}/{{ maxResident }}</template></span>
        </div>
        <div class="header-actions">
          <el-input v-model="search" :prefix-icon="Search" placeholder="搜索名称 / 描述 / 触发词"
                    clearable class="skills-search" />
          <el-button plain @click="openImport">导入</el-button>
          <el-button plain @click="folder.open = true">批量导入…</el-button>
          <el-button type="primary" :icon="Plus" @click="openCreate">新建技能</el-button>
        </div>
      </div>
      <div class="hero-desc">技能是一份「怎么做这件事」的方法说明。在对话里挂载后，它只改变这一轮的表达方式 —— 用不用，由你决定。</div>

      <!-- 来源 + 状态筛选（tag-chip 语言与材料库一致） -->
      <div v-if="items.length" class="filter-bar">
        <span class="tag-chip" :class="{ on: !srcFilter }" @click="srcFilter = ''">全部来源</span>
        <span class="tag-chip" :class="{ on: srcFilter === 'builtin' }" @click="srcFilter = 'builtin'">内置</span>
        <span class="tag-chip" :class="{ on: srcFilter === 'local' }" @click="srcFilter = 'local'">自制</span>
        <span class="tag-chip" :class="{ on: srcFilter === 'imported' }" @click="srcFilter = 'imported'">导入</span>
        <span class="type-tag-divider" aria-hidden="true"></span>
        <span class="tag-chip" :class="{ on: stFilter === 'resident' }" @click="toggleSt('resident')">语义候选</span>
        <span class="tag-chip" :class="{ on: stFilter === 'off' }" @click="toggleSt('off')">已停用</span>
        <span v-if="truncated > 0" class="meta-chip warn">还有 {{ truncated }} 个不参与语义建议</span>
      </div>
    </div>

    <!-- 语义预判（P3）：默认关。开着会让「触发词没命中的问题」多一次模型调用，
         所以必须把代价写在开关旁边，而不是只给一个孤零零的开关。 -->
    <div class="semantic-bar">
      <el-switch :model-value="semantic" :loading="semanticSaving"
                 size="small" @change="toggleSemantic" />
      <div class="semantic-text">
        <span class="semantic-title">语义预判</span>
        <span class="semantic-desc">
          触发词没命中时，再让模型判断一次这个问题适不适合某个技能。
          <template v-if="semantic">已开启：这类问题会多等 1–2 秒，并消耗少量 token。</template>
          <template v-else>当前关闭：只按触发词匹配，不会产生额外调用。</template>
        </span>
      </div>
    </div>

    <!-- 被拒绝的文件：静默忽略是最难查的故障，必须露出来 -->
    <div v-if="rejected.length" class="reject-bar">
      <div class="reject-title">{{ rejected.length }} 个条目未被加载</div>
      <div v-for="(r, i) in rejected" :key="i" class="reject-row">
        <code class="reject-file">{{ r.file }}</code>
        <span class="reject-reason">{{ r.reason }}</span>
      </div>
    </div>

    <!-- 使用痕迹读不出来（典型：后端未重启、表还没建）：绝不能静默显示成「全部还没用过」 -->
    <div v-if="usageError" class="reject-bar">
      <div class="reject-title">使用记录暂不可读</div>
      <div class="reject-row">
        <span class="reject-reason">{{ usageError }}</span>
      </div>
    </div>

    <!-- 首次加载骨架屏（材料库同款）：loading 期间纯白 =「卡了」的感知来源 -->
    <div v-if="loading" class="skill-grid">
      <div v-for="i in 6" :key="'sk' + i" class="skill-card sk-card">
        <div class="sk-top">
          <div class="sk-block sk-icon"></div>
          <div class="sk-head">
            <div class="sk-block sk-line" style="width: 58%"></div>
            <div class="sk-block sk-line" style="width: 34%"></div>
          </div>
        </div>
        <div class="sk-block sk-line" style="width: 94%"></div>
        <div class="sk-block sk-line" style="width: 66%"></div>
      </div>
    </div>

    <div v-else-if="!items.length" class="skills-empty">
      <el-empty :image="mascot" :image-size="120">
        <template #description>
          <div class="empty-title">还没有技能</div>
          <div class="empty-desc">
            点右上角「新建技能」写一个，或把 <code>.skill.md</code> 文件放进下面的目录后刷新。
          </div>
          <code class="empty-path">{{ userDir || '（用户技能目录）' }}</code>
        </template>
      </el-empty>
    </div>

    <!-- 搜索/筛选无结果（与「一个技能都没有」的空态分开：后者要给出路径指引） -->
    <div v-else-if="!filteredItems.length" class="skills-noresult">
      <el-empty :image="mascot" :image-size="100"
                description="没有找到匹配的技能，换个关键词或清除筛选试试" />
    </div>

    <div v-else class="skill-grid">
      <div v-for="(s, i) in filteredItems" :key="s.id" class="skill-card card-in"
           :class="{ off: s.enabled === false }" :style="{ animationDelay: (i % 12) * 35 + 'ms' }"
           @click="openDetail(s)">
        <div class="card-top">
          <span class="card-icon">{{ skillIconText(s.icon) }}</span>
          <div class="card-head">
            <div class="card-name" :title="s.name">{{ s.name }}</div>
            <div class="card-meta">
              <span class="card-badge" :class="'badge-' + s.source">{{ badgeText(s) }}</span>
              <!-- 只在用过时出现：没有记录就整块不渲染，不占版面、不做空态提示 -->
              <span v-if="useCount(s)" class="card-use" :title="useTitle(s)">用过 {{ useCount(s) }} 次</span>
            </div>
          </div>
        </div>
        <div class="card-desc">{{ s.description }}</div>
        <div class="card-foot">
          <span v-for="t in s.triggers.slice(0, 4)" :key="t" class="trig">{{ trigText(t) }}</span>
          <span v-if="s.triggers.length > 4" class="trig more">+{{ s.triggers.length - 4 }}</span>
          <span v-if="!s.triggers.length" class="trig more">无触发词</span>
        </div>
        <!-- 开关区：@click.stop 阻止冒泡到卡片（否则点开关会顺带打开详情抽屉） -->
        <div class="card-ops" @click.stop>
          <el-switch :model-value="s.enabled !== false" size="small"
                     :loading="switching === s.id"
                     @change="(v) => toggle(s, v)" />
          <span class="ops-label" :class="{ dim: !s.resident }">
            {{ s.enabled === false ? '已停用' : (s.resident ? '语义候选' : '不参与语义建议') }}
          </span>
          <!-- 停用的技能不进对话技能池（对话页只列 enabled 的），所以这里必须一起不可点，
               否则点了会静默退化成普通对话，用户会以为技能生效了 -->
          <button type="button" class="card-try" :class="{ off: s.enabled === false }"
                  :disabled="s.enabled === false"
                  :title="s.enabled === false ? '已停用：先打开左侧开关' : '到 AI 对话里用这个技能回答'"
                  @click="trySkill(s)">去试试 →</button>
        </div>
      </div>
    </div>

    <!-- 详情抽屉：让用户看清这个技能到底教了模型什么 -->
    <el-drawer v-model="drawer" :title="detail?.name || ''" size="560px" direction="rtl">
      <div v-if="detailLoading" class="skills-loading">加载中…</div>
      <div v-else-if="detail" class="detail-body">
        <div class="detail-row">
          <span class="detail-label">标识</span>
          <code>{{ detail.id }}</code>
        </div>
        <div class="detail-row">
          <span class="detail-label">来源</span>
          <span>{{ sourceText(detail.source) }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">触发词</span>
          <span class="detail-trigs">
            <span v-for="t in detail.triggers" :key="t" class="trig">{{ trigText(t) }}</span>
            <span v-if="!detail.triggers.length" class="trig more">无（只能手动挂载）</span>
          </span>
        </div>
        <div class="detail-row">
          <span class="detail-label">引用</span>
          <span>{{ detail.citation === 'required' ? '必须给出来源' : '可选' }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">工具</span>
          <span class="detail-trigs">
            <span v-for="c in detail.capabilities || []" :key="c" class="trig cap">{{ capLabel(c) }}</span>
            <span v-if="detail.tool_first" class="trig cap">首轮必调</span>
            <span v-if="!(detail.capabilities || []).length" class="trig more">无（只用检索到的片段作答）</span>
          </span>
        </div>
        <!-- 能力被后端剔除的原因（未知能力 / 内置专属）必须看得见：
             「写了却没生效」是这个页面最难自查的一类问题 -->
        <div v-for="(w, i) in detail.warnings || []" :key="'w' + i" class="detail-warn">
          能力提示：{{ w }}
        </div>
        <div class="detail-section">
          <div class="section-label">它会教给模型的方法 · 指令原文（{{ (detail.instructions || '').length }} 字）</div>
          <pre class="instr-box">{{ detail.instructions }}</pre>
        </div>

        <div class="detail-ops">
          <el-button size="small" type="primary" :disabled="detail.enabled === false"
                     @click="trySkill(detail)">去试试</el-button>
          <el-button v-if="detail.source === 'builtin'" size="small" @click="copyAsCustom(detail)">
            复制为自定义
          </el-button>
          <el-button v-else size="small" @click="openEdit(detail)">编辑</el-button>
          <el-button size="small" @click="doExport(detail)">导出</el-button>
          <el-button v-if="detail.source !== 'builtin'" size="small" type="danger" plain
                     @click="doDelete(detail)">删除</el-button>
        </div>
        <div v-if="detail.source === 'builtin'" class="builtin-note">
          内置技能随包发布、只读。想改它：先「复制为自定义」，改副本即可 —— 副本会用新的 id，不会和内置冲突。
        </div>
      </div>
    </el-drawer>

    <!-- 编辑器抽屉 -->
    <el-drawer v-model="editor.open" :title="editor.mode === 'create' ? '新建技能' : ('编辑 · ' + editor.id)"
               size="620px" direction="rtl" :close-on-click-modal="false">
      <div class="editor-body">
        <el-form label-position="top" size="small">
          <el-form-item label="标识（id，创建后不可改；小写字母开头，仅小写/数字/下划线）">
            <el-input v-model="form.id" :disabled="editor.mode === 'edit'" placeholder="例如 meeting_minutes" />
          </el-form-item>
          <el-form-item label="名称（1–20 字，对话里显示的名字）">
            <el-input v-model="form.name" maxlength="20" show-word-limit placeholder="例如 会议纪要" />
          </el-form-item>
          <el-form-item label="描述（决定它会不会被推荐，写清适用场景）">
            <el-input v-model="form.description" type="textarea" :rows="2" maxlength="120" show-word-limit
                      placeholder="例如：把一段会议记录整理成决议、待办、争议三块" />
          </el-form-item>
          <el-form-item label="触发词（一行一个，支持正则；留空 = 只能手动挂载）">
            <el-input v-model="form.triggersText" type="textarea" :rows="3"
                      placeholder="例如：&#10;会议纪要&#10;纪要整理" />
          </el-form-item>
          <div class="form-row">
            <el-form-item label="优先级（越大越优先被建议）" class="col-half">
              <el-input-number v-model="form.priority" :min="0" :max="99" size="small" />
            </el-form-item>
            <el-form-item label="引用要求" class="col-half">
              <el-select v-model="form.citation" size="small">
                <el-option label="可选" value="optional" />
                <el-option label="必须给出来源" value="required" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="工具能力（勾选后模型可以在回答过程中真的去查/去写，不勾只能靠检索片段）">
            <div class="cap-list">
              <el-checkbox-group v-model="form.capabilities">
                <el-checkbox v-for="c in capsMeta" :key="c.name" :label="c.name"
                             :disabled="capDisabled(c.name)">
                  {{ c.label }}
                  <span v-if="c.builtin_only" class="cap-only">仅内置</span>
                </el-checkbox>
              </el-checkbox-group>
            </div>
          </el-form-item>
          <el-form-item>
            <template #label>
              首轮必调工具
              <span class="cap-only" style="margin-left:6px">开启后连「你好」也会先查一次</span>
            </template>
            <!-- P3-①：只有「找全/对比」类技能才该开；没勾选任何读类能力时禁选（开了也没牌可打） -->
            <el-checkbox v-model="form.tool_first"
                         :disabled="!(form.capabilities || []).some(c => ['list_materials', 'search_kb', 'read_material'].includes(c))">
              挂载期间，每个问题的第一轮强制发起一次工具调用（适合「找全 / 逐条对比」类技能）
            </el-checkbox>
          </el-form-item>
          <el-form-item label="方法说明（围栏外正文，按 token 预算卡，约合中文 9600 字 / 英文 24000 字符）">
            <el-input v-model="form.instructions" type="textarea" :rows="9" maxlength="26000" show-word-limit
                      placeholder="写「怎么做这件事」：先做什么、再做什么、什么情况下不算完成。" />
          </el-form-item>
        </el-form>

        <div class="editor-preview">
          <div class="section-label">将写入磁盘的文件（{{ draftText.length }} 字节）</div>
          <pre class="instr-box md-preview">{{ draftText }}</pre>
        </div>

        <div v-if="check.checked" class="check-bar" :class="check.ok ? 'check-ok' : 'check-bad'">
          <div class="check-title">{{ check.ok ? '校验通过' : ('校验未通过：' + check.error) }}</div>
          <div v-for="(w, i) in check.warnings" :key="i" class="check-warn">提示：{{ w }}</div>
        </div>

        <div class="editor-ops">
          <el-button size="small" @click="editor.open = false">取消</el-button>
          <el-button size="small" :loading="checking" @click="doCheck">校验</el-button>
          <el-button size="small" type="primary" :loading="saving" @click="doSave">保存</el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 导入对话框 -->
    <el-dialog v-model="importer.open" title="导入技能" width="640px">
      <div class="import-body">
        <div class="import-hint">
          粘贴一份 <code>.skill.md</code> 全文，或选择文件。导入的技能标为「导入」，<b>不会自动参与建议</b>，
          只能在对话里手动挂载。
        </div>
        <div class="import-tools">
          <el-button size="small" @click="pickFile">选择文件…</el-button>
          <span v-if="importer.filename" class="import-file">{{ importer.filename }}</span>
          <el-checkbox v-model="importer.overwrite" label="覆盖同名技能" size="small" />
        </div>
        <el-input v-model="importer.text" type="textarea" :rows="14"
                  placeholder="把 .skill.md 的内容粘贴到这里" />
        <input ref="fileInput" type="file" accept=".md,.txt,text/markdown" hidden @change="onFile" />
      </div>
      <template #footer>
        <el-button size="small" @click="importer.open = false">取消</el-button>
        <el-button size="small" type="primary" :loading="importer.loading" @click="doImport">导入</el-button>
      </template>
    </el-dialog>

    <!-- 文件夹批量导入（P6）：扫描预览 → 确认后才落盘 -->
    <el-dialog v-model="folder.open" title="从文件夹批量导入" width="720px">
      <div class="import-body">
        <div class="import-hint">
          选择技能包文件夹，或填本地路径（都会递归找 <code>SKILL.md</code> 与 <code>.skill.md</code>）。
          Claude 系格式（YAML frontmatter）会自动转成本系统格式；导入的技能<b>不参与自动建议</b>，只能手动挂载。
        </div>
        <div class="import-tools">
          <el-button size="small" :loading="folder.scanning && folder.mode === 'picker'" @click="pickFolder">选择文件夹…</el-button>
          <el-input v-model="folder.path" size="small" placeholder="或粘贴本地路径，例如 D:\工作软件\技能\pm-skills-main"
                    style="flex:1" @keyup.enter="scanFolder" />
          <el-button size="small" :loading="folder.scanning && folder.mode === 'path'" @click="scanFolder">扫描路径</el-button>
        </div>
        <div v-if="folder.sourceName" class="folder-source">
          已选文件夹：{{ folder.sourceName }} · 候选 {{ folder.files.length }} 份
        </div>
        <input ref="folderInput" data-testid="folder-input" type="file" webkitdirectory multiple hidden @change="onFolderPick" />
        <div v-if="folder.scanned" class="folder-result">
          <div class="folder-summary">
            共 {{ folder.results.length }} 份：
            可导入 <b class="ok-text">{{ readyCount }}</b> 份，
            冲突 {{ statusCount('conflict') }} 份，重复 {{ statusCount('dup') }} 份，失败 {{ statusCount('failed') }} 份
          </div>
          <div class="folder-list">
            <div v-for="r in folder.results" :key="r.file" class="folder-row" :class="'st-' + r.status">
              <span class="folder-status">{{ statusText(r.status) }}</span>
              <span class="folder-name">{{ r.name || r.file }}</span>
              <span class="folder-id">{{ r.id }}</span>
              <span class="folder-reason">{{ r.reason || (r.warnings || []).join('；') }}</span>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="folder.open = false">取消</el-button>
        <el-button size="small" type="primary" :disabled="readyCount === 0"
                   :loading="folder.importing" @click="doImportFolder">
          导入 {{ readyCount }} 份</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Plus } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { skillApi } from '../api'
import { errMsg } from '../api/http'
import mascot from '../assets/mascot.png'
import { skillIconText } from '../utils/skillIcon'

const MAX_RESIDENT_FALLBACK = 13

const router = useRouter()
const loading = ref(true)
const items = ref([])
const truncated = ref(0)
const enabledCount = ref(0)
const residentCount = ref(0)
const maxResident = ref(MAX_RESIDENT_FALLBACK)
const rejected = ref([])
// 使用痕迹读取失败的原因（空 = 正常）。见后端 `GET /skills` 的 usage_error。
const usageError = ref('')
const userDir = ref('')
const switching = ref('')
// 语义预判开关（P3）。默认关：与后端 DEFAULT_SEMANTIC 保持一致，实际值以接口为准
const semantic = ref(false)
const semanticSaving = ref(false)

// ---- 搜索与筛选（对齐材料库工具行；纯前端过滤，不打接口） ----
const search = ref('')
const srcFilter = ref('')
const stFilter = ref('')
function toggleSt(k) { stFilter.value = stFilter.value === k ? '' : k }
// ⚠️ computed 注册即同步求值一次：用到的 ref（items/search/srcFilter/stFilter）必须都已声明
//    （反过来写会 TDZ → 整页白屏，且构建与文本断言都是绿的）。trigText 是 function 声明，提升安全。
const filteredItems = computed(() => {
  const q = search.value.trim().toLowerCase()
  return items.value.filter((s) => {
    if (srcFilter.value && s.source !== srcFilter.value) return false
    if (stFilter.value === 'resident' && !s.resident) return false
    if (stFilter.value === 'off' && s.enabled !== false) return false
    if (!q) return true
    const hay = [s.name, s.description, s.id,
                 ...(s.triggers || []).map((t) => trigText(t))].join(' ').toLowerCase()
    return hay.includes(q)
  })
})

const drawer = ref(false)
const detail = ref(null)
const detailLoading = ref(false)

// ---- 编辑器 ----
const editor = ref({ open: false, mode: 'create', id: '' })
const form = ref(blankForm())
const checking = ref(false)
const saving = ref(false)
const check = ref({ checked: false, ok: false, error: '', warnings: [] })

// ---- 导入 ----
const importer = ref({ open: false, text: '', filename: '', overwrite: false, loading: false })

// ---- 文件夹批量导入（P6） ----
const folder = ref({ open: false, path: '', mode: 'path', sourceName: '', files: [], failures: [], scanning: false, importing: false, scanned: false, results: [] })
const folderInput = ref(null)
const PICKER_MAX_FILES = 500
const PICKER_MAX_FILE_BYTES = 512 * 1024
const PICKER_MAX_TOTAL_BYTES = 16 * 1024 * 1024
const readyCount = computed(() =>
  folder.value.results.filter((r) => r.status === 'ready' || r.status === 'conflict').length)
function statusCount(s) { return folder.value.results.filter((r) => r.status === s).length }
function statusText(s) {
  return { ready: '可导入', ok: '已导入', conflict: '冲突', failed: '失败', dup: '重复' }[s] || s
}
async function scanFolder() {
  if (!folder.value.path.trim()) return
  folder.value.mode = 'path'
  folder.value.sourceName = ''
  folder.value.files = []
  folder.value.failures = []
  folder.value.results = []
  folder.value.scanned = false
  folder.value.scanning = true
  try {
    const { data } = await skillApi.importFolder(folder.value.path.trim(), true)
    folder.value.results = data.results || []
    folder.value.scanned = true
  } catch (e) {
    ElMessage.error(errMsg(e, '扫描失败'))
  } finally {
    folder.value.scanning = false
  }
}

function pickFolder() {
  if (!folderInput.value) return
  folderInput.value.value = ''       // 允许连续选择同一个文件夹
  folderInput.value.click()
}

async function onFolderPick(ev) {
  const picked = Array.from(ev.target.files || [])
  if (!picked.length) return
  folder.value.results = []
  folder.value.scanned = false
  folder.value.failures = []
  const candidates = []
  const localFailures = []
  let total = 0
  for (const f of picked) {
    const rel = (f.webkitRelativePath || f.name || '').replace(/\\/g, '/')
    const name = rel.split('/').pop() || f.name
    if (!(name.toLowerCase() === 'skill.md' || name.toLowerCase().endsWith('.skill.md'))) continue
    if (candidates.length + localFailures.length + 1 > PICKER_MAX_FILES) {
      ElMessage.error(`候选技能文件超过 ${PICKER_MAX_FILES} 份，请先拆小文件夹再导入`)
      return
    }
    if (f.size > PICKER_MAX_FILE_BYTES) {
      localFailures.push({ file: rel, id: '', name: '', status: 'failed',
        reason: `文件 ${f.size} 字节，超过单文件上限 512KB，已跳过`, warnings: [] })
      continue
    }
    total += f.size
    if (total > PICKER_MAX_TOTAL_BYTES) {
      ElMessage.error('选中文件总大小超过 16MB，请改用本地路径扫描')
      return
    }
    candidates.push({ file: rel, blob: f })
  }
  if (!candidates.length && !localFailures.length) {
    ElMessage.warning('这个文件夹里没找到技能文件（SKILL.md 或 .skill.md）')
    return
  }
  folder.value.mode = 'picker'
  folder.value.path = ''
  folder.value.sourceName = (candidates[0]?.file || localFailures[0]?.file || '已选文件夹').split('/')[0]
  folder.value.failures = localFailures
  folder.value.scanning = true
  try {
    const files = []
    for (const c of candidates) files.push({ file: c.file, text: await c.blob.text() })
    folder.value.files = files
    if (!files.length) {
      folder.value.results = localFailures
      folder.value.scanned = true
      return
    }
    const { data } = await skillApi.importFiles(files, true)
    folder.value.results = localFailures.concat(data.results || [])
    folder.value.scanned = true
  } catch (e) {
    ElMessage.error(errMsg(e, '扫描失败'))
  } finally {
    folder.value.scanning = false
  }
}

async function doImportFolder() {
  folder.value.importing = true
  try {
    const { data } = folder.value.mode === 'picker'
      ? await skillApi.importFiles(folder.value.files, false)
      : await skillApi.importFolder(folder.value.path.trim(), false)
    folder.value.results = folder.value.mode === 'picker'
      ? folder.value.failures.concat(data.results || [])
      : (data.results || [])
    const ok = data.ok_count || 0
    const failed = statusCount('failed')
    if (failed) ElMessage.warning(`已导入 ${ok} 份，${failed} 份失败（见列表原因）`)
    else ElMessage.success(`已导入 ${ok} 份技能，到列表里手动挂载即可使用`)
    await load()
  } catch (e) {
    ElMessage.error(errMsg(e, '导入失败'))
  } finally {
    folder.value.importing = false
  }
}
const fileInput = ref(null)

function blankForm() {
  return {
    id: '', name: '', description: '', triggersText: '', priority: 10,
    citation: 'optional', instructions: '', capabilities: [], tool_first: false,
  }
}

// ⚠️ 声明的顺序即依赖顺序：computed 立即求值一次，用到的 ref 必须已经在上面声明
//    （反过来写会 TDZ → 整页白屏，且 vite build 与文本断言都是绿的）
const draftText = computed(() => {
  const f = form.value
  const man = {
    schema_version: 1,
    id: (f.id || '').trim(),
    name: (f.name || '').trim(),
    icon: 'skill',
    kind: 'chat',
    description: (f.description || '').trim(),
    triggers: parseTriggers(f.triggersText),
    priority: Number(f.priority) || 0,
    citation: f.citation || 'optional',
  }
  // 能力只在真的勾了才写进文件：给每个技能平白加一行空数组会让导出文本和预期不一致
  if (f.capabilities && f.capabilities.length) man.capabilities = [...f.capabilities]
  // tool_first 只在开启时写进文件：默认关闭，不该给每个技能平白加一行 false
  if (f.tool_first) man.tool_first = true
  return '```skill\n' + JSON.stringify(man, null, 2) + '\n```\n\n' + (f.instructions || '').trim() + '\n'
})

// 图标显示规则收敛到 utils/skillIcon.js：技能页与对话页不能各维护一份映射。

// triggers 是**给机器看的正则**（如 `第.{1,3}条`）。直接展示会让用户以为要输入正则，
// 所以只做展示层清洗；后端匹配仍用原文。（清洗后为空则回落到原文，不显示空白 chip）
function trigText(t) {
  const s = String(t)
    .replace(/\.\{\d+(?:,\d+)?\}/g, '…')
    .replace(/[\\^$*+?()[\]{}|]/g, '')
  return s || String(t)
}

function badgeText(s) {
  if (s.source === 'builtin') return '内置'
  if (s.source === 'imported') return '导入'
  return '自定义'
}

function sourceText(src) {
  if (src === 'builtin') return '内置（随包发布，只读）'
  if (src === 'imported') return '导入（他人提供的方法，只能手动挂载）'
  return '自定义'
}

// ---- 使用痕迹（P0-1 资产掌控）----
// 后端只在「技能注入且回答**成功完成**」时记一笔：/skills/match 的建议不记、
// 中断与报错也不记（判据见 docs/资产掌控力-研发方案.md §3.6）。
// 没有记录就整块不渲染（2026-11-03 用户拍板：不强制展示空态，不占版面）。
function useCount(s) {
  return Number(s.use_count || 0)
}
// 后端给的是 UTC（带 Z 后缀），Date.parse 会正确转成本地时间。
// ⚠️ 别去掉后端的 Z：naive UTC 字符串会被当本地时间解析，整体偏 8 小时。
function relTime(iso) {
  const t = Date.parse(iso || '')
  if (!t) return '—'
  const mins = Math.floor((Date.now() - t) / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs} 小时前`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days} 天前`
  return `${Math.floor(days / 30)} 个月前`
}
// 卡面只显示「用过 N 次」；最后使用时间收进 tooltip，不占版面。
function useTitle(s) {
  const t = relTime(s.last_used_at)
  return t === '—' ? '在 AI 对话里成功完成的回答次数' : `最近一次：${t}`
}

// ---- 工具能力（P4）----
// 清单来自后端 `GET /skills` 的 `capabilities`：硬编码在前端会两处漂移
// （后端加了新工具，页面里选不到；后端删了，页面还在给选项）。
const capsMeta = ref([])
function capBuiltinOnly(name) {
  const m = capsMeta.value.find((c) => c.name === name)
  return !!(m && m.builtin_only)
}
function capLabel(name) {
  const m = capsMeta.value.find((c) => c.name === name)
  return m ? m.label : name
}
// 内置专属能力要按当前技能是不是内置来禁用：让用户勾一个必然被后端剔除的项，
// 属于「保存成功但没生效」的经典坑。
function capDisabled(name) {
  return capBuiltinOnly(name) && !editorIsBuiltin.value
}
const editorIsBuiltin = computed(() => {
  const it = items.value.find((s) => s.id === editor.value.id)
  return !!(it && it.source === 'builtin')
})

function parseTriggers(text) {
  return String(text || '')
    .split(/\r?\n/)
    .map((t) => t.trim())
    .filter(Boolean)
}

async function load() {
  loading.value = true
  try {
    const { data } = await skillApi.list()
    items.value = data.items || []
    truncated.value = data.truncated || 0
    enabledCount.value = data.enabled_count != null
      ? data.enabled_count
      : items.value.filter((s) => s.enabled !== false).length
    residentCount.value = data.resident_count != null
      ? data.resident_count
      : items.value.filter((s) => s.resident).length
    maxResident.value = data.max_resident || MAX_RESIDENT_FALLBACK
    rejected.value = data.rejected || []
    usageError.value = data.usage_error || ''
    userDir.value = data.user_dir || ''
    // 开关状态以接口为准（用户可能在别的窗口改过，或后端回落了默认值）
    semantic.value = !!data.semantic_enabled
    capsMeta.value = data.capabilities || []
  } catch (e) {
    items.value = []
    rejected.value = [{ file: '（请求失败）', reason: errMsg(e, '无法加载技能列表') }]
  } finally {
    loading.value = false
  }
}

async function toggleSemantic(val) {
  // @change 给的是「目标值」，不是当前值 —— 别用 !semantic.value 取反（连点会错位）
  semanticSaving.value = true
  const prev = semantic.value
  semantic.value = !!val
  try {
    const { data } = await skillApi.setSemantic(!!val)
    // 以服务端落盘结果为准：状态文件损坏时后端会回落到默认值
    semantic.value = !!data.enabled
  } catch (e) {
    semantic.value = prev
    ElMessage.error(errMsg(e, '切换语义预判失败'))
  } finally {
    semanticSaving.value = false
  }
}

async function toggle(s, val) {
  switching.value = s.id
  const prev = s.enabled !== false
  // 乐观更新：开关必须立刻动，否则用户会连点（后端可能已经处理完，界面还在旧状态）
  s.enabled = val
  try {
    await skillApi.setEnabled(s.id, val)
    // 停用会让常驻名额空出来给下一个，计数必须重拉（本地推算会和后端错位）
    await load()
  } catch (e) {
    s.enabled = prev
    ElMessage.error(errMsg(e, '切换失败'))
  } finally {
    switching.value = ''
  }
}

// 「去试试」：带技能跳到对话页，由对话页默认挂载它。
// ⚠️ 只传 id，不传 name —— 名称以对话页拉到的技能列表为准，避免两处中文名漂移；
//    也不在这里写 localStorage：去不去用、用哪一轮，由对话页自己决定。
function trySkill(s) {
  if (!s || s.enabled === false) return
  drawer.value = false
  router.push({ name: 'chat', query: { skill: s.id } })
}

async function openDetail(s) {
  drawer.value = true
  detail.value = null
  detailLoading.value = true
  try {
    const { data } = await skillApi.detail(s.id)
    detail.value = data
  } catch (e) {
    detail.value = { ...s, instructions: '（加载失败：' + errMsg(e) + '）' }
  } finally {
    detailLoading.value = false
  }
}

function openCreate() {
  editor.value = { open: true, mode: 'create', id: '' }
  form.value = blankForm()
  check.value = { checked: false, ok: false, error: '', warnings: [] }
}

function openEdit(d) {
  // ⚠️ 必须先关详情抽屉：两层 el-drawer 叠着时，上层关闭后下层的遮罩仍在，
  //    表现为"点了列表卡片没反应"（实际被遮罩吃了事件）。
  drawer.value = false
  editor.value = { open: true, mode: 'edit', id: d.id }
  form.value = {
    id: d.id,
    name: d.name,
    description: d.description,
    triggersText: (d.triggers || []).join('\n'),
    priority: d.priority,
    citation: d.citation,
    instructions: d.instructions || '',
    capabilities: [...(d.capabilities || [])],
    tool_first: !!d.tool_first,
  }
  check.value = { checked: false, ok: false, error: '', warnings: [] }
}

// 内置技能复制一份：自动换 id，否则会撞「内置优先」被静默忽略
function nextCopyId(base) {
  const taken = new Set(items.value.map((s) => s.id))
  let cand = base + '_my'
  let n = 2
  while (taken.has(cand)) {
    cand = base + '_my' + n
    n += 1
  }
  return cand
}

function copyAsCustom(d) {
  const nid = nextCopyId(d.id)
  editor.value = { open: true, mode: 'create', id: nid }
  form.value = {
    id: nid,
    name: (d.name + '（副本）').slice(0, 20),
    description: d.description,
    triggersText: (d.triggers || []).join('\n'),
    priority: d.priority,
    citation: d.citation,
    instructions: d.instructions || '',
    // 副本默认**不带** save_note：它只对内置技能生效，带过去也只会得到一条警告
    capabilities: (d.capabilities || []).filter((c) => !capBuiltinOnly(c)),
    tool_first: !!d.tool_first,
  }
  check.value = { checked: false, ok: false, error: '', warnings: [] }
  drawer.value = false
  ElMessage.info('已复制为「' + nid + '」，改完保存即可 —— 内置版本不受影响')
}

async function doCheck() {
  checking.value = true
  try {
    const { data } = await skillApi.validate(draftText.value)
    check.value = {
      checked: true, ok: !!data.ok, error: data.error || '',
      warnings: data.warnings || [],
    }
  } catch (e) {
    check.value = { checked: true, ok: false, error: errMsg(e, '校验请求失败'), warnings: [] }
  } finally {
    checking.value = false
  }
}

async function doSave() {
  const f = form.value
  const payload = {
    name: (f.name || '').trim(),
    description: (f.description || '').trim(),
    triggers: parseTriggers(f.triggersText),
    priority: Number(f.priority) || 0,
    citation: f.citation,
    instructions: f.instructions || '',
    capabilities: [...(f.capabilities || [])],
    tool_first: !!f.tool_first,
  }
  saving.value = true
  try {
    if (editor.value.mode === 'create') {
      await skillApi.create({ ...payload, id: (f.id || '').trim() })
      ElMessage.success('已创建')
    } else {
      await skillApi.update(editor.value.id, payload)
      ElMessage.success('已保存')
    }
    editor.value.open = false
    await load()
  } catch (e) {
    ElMessage.error(errMsg(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

async function doExport(d) {
  try {
    const { data } = await skillApi.exportText(d.id)
    const text = typeof data === 'string' ? data : JSON.stringify(data)
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = d.id + '.skill.md'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1500)
  } catch (e) {
    ElMessage.error(errMsg(e, '导出失败'))
  }
}

async function doDelete(d) {
  try {
    await ElMessageBox.confirm(
      '删除后文件会从磁盘移除，无法在应用内撤销。确定删除「' + d.name + '」吗？',
      '删除技能', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await skillApi.remove(d.id)
    ElMessage.success('已删除')
    drawer.value = false
    await load()
  } catch (e) {
    ElMessage.error(errMsg(e, '删除失败'))
  }
}

function openImport() {
  importer.value = { open: true, text: '', filename: '', overwrite: false, loading: false }
}

function pickFile() {
  if (fileInput.value) fileInput.value.click()
}

async function onFile(ev) {
  const f = ev.target.files && ev.target.files[0]
  if (!f) return
  try {
    importer.value.text = await f.text()
    importer.value.filename = f.name
  } catch (e) {
    ElMessage.error('读取文件失败：' + (e?.message || e))
  } finally {
    // 必须清空：否则连续选同一个文件不会触发 change
    ev.target.value = ''
  }
}

async function doImport() {
  const text = (importer.value.text || '').trim()
  if (!text) {
    ElMessage.warning('请先粘贴内容或选择文件')
    return
  }
  importer.value.loading = true
  try {
    const { data } = await skillApi.importText(text, importer.value.overwrite)
    importer.value.open = false
    ElMessage.success('已导入「' + (data.skill?.name || data.id) + '」，可在对话里手动挂载')
    await load()
  } catch (e) {
    ElMessage.error(errMsg(e, '导入失败'))
  } finally {
    importer.value.loading = false
  }
}

onMounted(load)
</script>

<style scoped>
/* ===== 页头与筛选（对齐材料库） ===== */
.sticky-head {
  position: sticky; top: 0; z-index: 20;
  margin: -28px -32px 0; padding: 28px 32px 1px;
  background: var(--asc-bg);
}
.page-header {
  display: flex; justify-content: space-between; align-items: flex-end;
  gap: 16px 24px; flex-wrap: wrap;
  margin-bottom: 12px; padding-bottom: 16px;
  border-bottom: 1px solid var(--asc-divider);
}
.header-left { display: flex; align-items: center; gap: 12px; }
/* 页头图标锚点（与知识库同款渐变方块；图标沿用侧栏「技能库」图形，导航与页头同一语言） */
.skills-head-icon {
  width: 44px; height: 44px; border-radius: 12px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  color: var(--asc-primary);
  background: linear-gradient(150deg, rgba(124, 92, 252, .16), rgba(124, 92, 252, .06));
  border: 1px solid rgba(124, 92, 252, .22);
}
.page-header h2 { margin: 0; font-size: 20px; font-weight: 600; letter-spacing: .2px; }
.header-sub { font-size: 12px; color: var(--asc-text-3); }
.header-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.skills-search { width: 220px; }
.skills-search :deep(.el-input__wrapper) { border-radius: 8px !important; }
.hero-desc {
  margin: -6px 0 12px; font-size: 12.5px; line-height: 1.6;
  color: var(--asc-text-2); max-width: 720px;
}
.filter-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 12px;
}
.tag-chip {
  height: 28px; display: inline-flex; align-items: center; padding: 0 12px;
  font-size: 12px; border-radius: 999px; cursor: pointer;
  background: var(--asc-card); border: 1px solid var(--asc-border); color: var(--asc-text-2);
  transition: all .15s;
}
.tag-chip:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
.tag-chip.on { background: var(--asc-primary); border-color: var(--asc-primary); color: #fff; }
.type-tag-divider { width: 1px; height: 16px; background: var(--asc-border); margin: 0 2px; }

.meta-chip {
  font-size: 12px; padding: 3px 9px; border-radius: 999px;
  background: var(--asc-surface-2); color: var(--asc-text-2); white-space: nowrap;
}
.meta-chip.warn { background: rgba(230, 145, 56, .12); color: #a55c11; }

/* 语义预判开关：与列表卡片区分开，底色用极浅的 surface-2，不抢视觉重心 */
.semantic-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; margin-bottom: 14px;
  background: var(--asc-surface-2); border: 1px solid var(--asc-border);
  border-radius: 10px;
}
.semantic-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.semantic-title { font-size: 13px; font-weight: 600; color: var(--asc-text); }
/* 次要文字 · 12.5px：底色是 surface-2（不是纯白），对比度按元素自身底色算 */
.semantic-desc { font-size: 12.5px; line-height: 1.6; color: var(--asc-text-2); }

.reject-bar {
  border: 1px solid rgba(230, 145, 56, .35); background: rgba(230, 145, 56, .07);
  border-radius: var(--asc-radius); padding: 10px 12px; margin-bottom: 14px;
}
.reject-title { font-size: 12px; font-weight: 600; color: #8a4d0f; margin-bottom: 6px; }
.reject-row { display: flex; gap: 8px; font-size: 12px; line-height: 1.8; color: var(--asc-text-2); }
.reject-file { flex-shrink: 0; color: #8a4d0f; }
.reject-reason { color: var(--asc-text-2); }

.skills-loading { padding: 40px 0; text-align: center; font-size: 13px; color: var(--asc-text-2); }

.skills-empty { padding: 24px 24px 32px; }
.empty-title { font-size: 15px; font-weight: 600; color: var(--asc-text); margin-bottom: 8px; }
.empty-desc { font-size: 13px; color: var(--asc-text-2); line-height: 1.7; }
.empty-path {
  display: inline-block; margin-top: 12px; padding: 5px 10px; font-size: 12px;
  background: var(--asc-surface-2); border-radius: 6px; color: var(--asc-text-2);
  word-break: break-all; max-width: 100%;
}
.skills-noresult { padding: 24px 0 32px; }

/* ===== 卡片网格（材料库同款：入场动画 + 上浮 hover） ===== */
@keyframes card-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
.card-in { animation: card-in .3s ease both; }
@media (prefers-reduced-motion: reduce) {
  .card-in { animation: none; }
  .skill-card { transition: none; }
}
.skill-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 16px; }
.skill-card {
  position: relative;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 14px; overflow: hidden; cursor: pointer;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
  display: flex; flex-direction: column;
  padding: 16px 16px 12px;
}
.skill-card:hover {
  transform: translateY(-3px);
  border-color: #c9b8fe;
  box-shadow: 0 10px 28px rgba(124, 92, 252, .1);
}
/* 停用态：**虚线边框**承担主要辨识，不靠底色差。
   实测：页面底 #f5f5f6 与 --asc-surface-2 #f0f0f2 只差 5 个色阶，纯靠灰底根本看不出来。
   也不降 opacity —— 半透明会把 12.5px 次要文字压到 4.5:1 以下。 */
.skill-card.off { background: var(--asc-surface-2); border: 1px dashed #c4c4cc; }
.skill-card.off .card-name { color: var(--asc-text-2); }

.card-top { display: flex; align-items: flex-start; gap: 12px; }
.card-head { flex: 1; min-width: 0; padding-top: 2px; }
.card-icon {
  width: 42px; height: 42px; flex-shrink: 0; border-radius: 11px;
  display: flex; align-items: center; justify-content: center;
  background: var(--asc-primary-soft); color: var(--asc-primary);
  font-size: 16px; font-weight: 600;
}
.card-name {
  font-size: 14px; font-weight: 600; line-height: 1.45; color: var(--asc-text);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.card-meta {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 12px; color: var(--asc-text-3); margin-top: 4px;
}
.card-badge {
  font-size: 11px; padding: 1px 7px; border-radius: 999px; white-space: nowrap;
}
.badge-builtin { background: var(--asc-primary-soft); color: var(--asc-primary); }
.badge-local { background: var(--asc-surface-2); color: var(--asc-text-2); }
.badge-imported { background: rgba(230, 145, 56, .14); color: #8a4d0f; }
/* 使用痕迹：只在用过时渲染。走 text-2（#6e6e6e，白底 4.5:1），不用 text-3
   （#a0a0a0 仅 2.6:1）—— 这是这张卡信息量最大的一行。 */
.card-use { font-size: 11px; color: var(--asc-text-2); white-space: nowrap; }
.card-desc {
  font-size: 12.5px; line-height: 1.7; color: var(--asc-text-2); flex: 1;
  margin-top: 10px;
  /* 描述长短不一 → clamp 三行保证卡片等高（材料库网格的整齐感来源） */
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3;
  overflow: hidden;
}
.card-foot { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 10px; }
.trig {
  font-size: 11px; padding: 1px 6px; border-radius: 4px;
  background: var(--asc-surface-2); color: var(--asc-text-2); white-space: nowrap;
}
.trig.more { background: transparent; color: #6e6e6e; }
/* 能力标签比触发词更"硬"（它是行为差异，不是匹配规则），用描边区分而不是换色 */
.trig.cap { background: transparent; border: 1px solid var(--asc-border); color: var(--asc-text); }

/* ===== 骨架屏（材料库同款：复用卡片外壳，内容换呼吸灰块） ===== */
.sk-card { cursor: default; pointer-events: none; }
.sk-card:hover { transform: none; box-shadow: none; border-color: var(--asc-border); }
.sk-top { display: flex; gap: 12px; margin-bottom: 14px; }
.sk-head { flex: 1; display: flex; flex-direction: column; gap: 10px; padding-top: 3px; }
.sk-block { background: var(--asc-border); border-radius: 6px; animation: sk-pulse 1.2s ease-in-out infinite; }
.sk-icon { width: 42px; height: 42px; border-radius: 11px; flex-shrink: 0; }
.sk-line { height: 12px; }
.sk-card > .sk-line + .sk-line { margin-top: 10px; }
@keyframes sk-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }
/* 能力被剔除的原因：必须能看见，否则用户会以为"写了就生效了" */
.detail-warn {
  margin-top: 8px; font-size: 12px; line-height: 1.7; color: #8a5a12;
  background: #fdf6e8; border-radius: 6px; padding: 6px 10px;
}
.cap-list { line-height: 1.9; }
.cap-only {
  margin-left: 4px; font-size: 10.5px; color: #6e6e6e;
  border: 1px solid var(--asc-border); border-radius: 3px; padding: 0 3px;
}

.card-ops {
  display: flex; align-items: center; gap: 8px;
  margin-top: 10px; padding-top: 9px; border-top: 1px dashed var(--asc-divider);
}
.ops-label { font-size: 11px; color: var(--asc-text-2); }
.ops-label.dim { color: #6e6e6e; }
.card-try {
  margin-left: auto; padding: 2px 6px; border: 0; border-radius: 6px;
  background: transparent; font: inherit; font-size: 11.5px; color: var(--asc-primary);
  cursor: pointer; white-space: nowrap; transition: background .16s ease;
}
.card-try:hover { background: rgba(124, 92, 252, .1); }
.card-try.off { color: #6e6e6e; cursor: not-allowed; }
.card-try.off:hover { background: transparent; }

.detail-body { font-size: 13px; color: var(--asc-text); }
.detail-row { display: flex; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--asc-divider); }
.detail-label { width: 56px; flex-shrink: 0; color: var(--asc-text-2); }
.detail-trigs { display: flex; flex-wrap: wrap; gap: 5px; }
.detail-section { margin-top: 16px; }
.section-label { font-size: 12px; color: var(--asc-text-2); margin-bottom: 8px; }
.instr-box {
  margin: 0; padding: 12px; font-size: 12.5px; line-height: 1.8;
  background: var(--asc-surface-2); border-radius: 8px;
  white-space: pre-wrap; word-break: break-word;
  font-family: inherit; color: var(--asc-text); max-height: 460px; overflow-y: auto;
}
.detail-ops { display: flex; gap: 8px; margin-top: 18px; flex-wrap: wrap; }
.builtin-note {
  margin-top: 10px; font-size: 12px; line-height: 1.7; color: var(--asc-text-2);
  padding: 8px 10px; background: var(--asc-surface-2); border-radius: 6px;
}

.editor-body { font-size: 13px; }
.form-row { display: flex; gap: 12px; }
.col-half { flex: 1; min-width: 0; }
.editor-preview { margin-top: 4px; }
.md-preview { max-height: 220px; font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; }
.check-bar { margin-top: 12px; padding: 8px 10px; border-radius: 6px; font-size: 12px; line-height: 1.7; }
.check-ok { background: rgba(103, 194, 58, .1); color: #3a7a1f; }
.check-bad { background: rgba(245, 108, 108, .1); color: #a12b2b; }
.check-title { font-weight: 600; }
.check-warn { color: #8a4d0f; }
.editor-ops { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }

.import-body { font-size: 13px; }
.import-hint { font-size: 12.5px; line-height: 1.7; color: var(--asc-text-2); margin-bottom: 10px; }
.import-hint b { color: var(--asc-text); }
.import-tools { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.folder-source { margin: -2px 0 8px; font-size: 12px; color: var(--asc-text-2); }
.import-file { font-size: 12px; color: var(--asc-text-2); }
/* 批量导入结果列表（P6） */
.folder-result { margin-top: 10px; }
.folder-summary { font-size: 12.5px; color: var(--asc-text-2); margin-bottom: 6px; }
.folder-summary .ok-text { color: var(--asc-primary); }
.folder-list { max-height: 300px; overflow: auto; border: 1px solid rgba(0,0,0,.08); border-radius: 6px; }
.folder-row { display: flex; gap: 8px; align-items: baseline; padding: 5px 10px; font-size: 12.5px; border-bottom: 1px solid rgba(0,0,0,.05); }
.folder-row:last-child { border-bottom: none; }
.folder-status { flex: none; width: 44px; font-weight: 600; }
.folder-row.st-ready .folder-status, .folder-row.st-ok .folder-status { color: #3a9a5d; }
.folder-row.st-conflict .folder-status { color: #b57d1e; }
.folder-row.st-failed .folder-status { color: #c44a4a; }
.folder-row.st-dup .folder-status { color: var(--asc-text-2); }
.folder-name { flex: none; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.folder-id { flex: none; color: var(--asc-text-2); font-size: 11.5px; }
.folder-reason { flex: 1; color: var(--asc-text-2); font-size: 11.5px; }
</style>
