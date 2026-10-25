<template>
  <div class="page skills-page">
    <!-- 头部 -->
    <div class="skills-hero">
      <div class="hero-text">
        <h2>技能</h2>
        <p>技能是一份「怎么做这件事」的方法说明。在对话里挂载后，它只改变这一轮的表达方式 —— 用不用，由你决定。</p>
      </div>
      <div class="hero-actions">
        <el-button size="small" @click="openImport">导入</el-button>
        <el-button size="small" type="primary" @click="openCreate">新建技能</el-button>
      </div>
    </div>

    <div class="hero-meta">
      <span class="meta-chip">启用 {{ enabledCount }} / 常驻上限 {{ maxResident }}</span>
      <span class="meta-chip" :class="{ dim: !residentCount }">已纳入常驻 {{ residentCount }}</span>
      <span v-if="truncated > 0" class="meta-chip warn">还有 {{ truncated }} 个未纳入常驻</span>
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

    <!-- 加载中 / 空态 / 列表 -->
    <div v-if="loading" class="skills-loading">加载中…</div>

    <div v-else-if="!items.length" class="skills-empty">
      <div class="empty-title">还没有技能</div>
      <div class="empty-desc">
        点右上角「新建技能」写一个，或把 <code>.skill.md</code> 文件放进下面的目录后刷新。
      </div>
      <code class="empty-path">{{ userDir || '（用户技能目录）' }}</code>
    </div>

    <div v-else class="skill-grid">
      <div v-for="s in items" :key="s.id" class="skill-card"
           :class="{ off: s.enabled === false }" @click="openDetail(s)">
        <div class="card-head">
          <span class="card-icon">{{ iconText(s.icon) }}</span>
          <span class="card-name">{{ s.name }}</span>
          <span class="card-badge" :class="'badge-' + s.source">{{ badgeText(s) }}</span>
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
            {{ s.enabled === false ? '已停用' : (s.resident ? '常驻清单' : '未纳入清单') }}
          </span>
        </div>
      </div>
    </div>

    <div class="skills-tip">
      <b>这不是产物目录。</b>摘要、讲义、播客、视频仍在各自页面按原来的方式使用；
      这里只管理「对话里可挂载的方法」。<b>导入的技能只能手动挂载</b>——它不会自动跳出来建议你用它。
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
          <el-button v-if="detail.source === 'builtin'" size="small" @click="copyAsCustom(detail)">
            复制为自定义
          </el-button>
          <el-button v-else size="small" type="primary" @click="openEdit(detail)">编辑</el-button>
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
          <el-form-item label="方法说明（围栏外正文，≤2400 字，这是技能的全部价值所在）">
            <el-input v-model="form.instructions" type="textarea" :rows="9" maxlength="2400" show-word-limit
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { skillApi } from '../api'
import { errMsg } from '../api/http'

const MAX_RESIDENT_FALLBACK = 12

const loading = ref(true)
const items = ref([])
const truncated = ref(0)
const enabledCount = ref(0)
const residentCount = ref(0)
const maxResident = ref(MAX_RESIDENT_FALLBACK)
const rejected = ref([])
const userDir = ref('')
const switching = ref('')
// 语义预判开关（P3）。默认关：与后端 DEFAULT_SEMANTIC 保持一致，实际值以接口为准
const semantic = ref(false)
const semanticSaving = ref(false)

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

// 符号图标在不同字体下会渲染成方块（原型阶段实测）→ 一律映射成中文单字
const ICON_TEXT = { term: '译', argue: '证', law: '法', skill: '技' }
function iconText(key) {
  return ICON_TEXT[key] || '技'
}

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
.skills-page { padding: 20px 24px 32px; }

.skills-hero {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 16px; margin-bottom: 12px;
}
.hero-text h2 { margin: 0 0 6px; font-size: 18px; font-weight: 600; color: var(--asc-text); }
.hero-text p { margin: 0; font-size: 13px; line-height: 1.7; color: var(--asc-text-2); max-width: 720px; }
.hero-actions { display: flex; gap: 8px; flex-shrink: 0; }

.hero-meta { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }

/* 语义预判开关：与列表卡片区分开，底色用极浅的 surface-2，不抢视觉重心 */
.semantic-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; margin-bottom: 16px;
  background: var(--asc-surface-2); border: 1px solid var(--asc-border);
  border-radius: 8px;
}
.semantic-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.semantic-title { font-size: 13px; font-weight: 600; color: var(--asc-text); }
/* 次要文字 · 12.5px：底色是 surface-2（不是纯白），对比度按元素自身底色算 */
.semantic-desc { font-size: 12.5px; line-height: 1.6; color: var(--asc-text-2); }
.meta-chip {
  font-size: 12px; padding: 3px 9px; border-radius: 999px;
  background: var(--asc-surface-2); color: var(--asc-text-2); white-space: nowrap;
}
.meta-chip.warn { background: rgba(230, 145, 56, .12); color: #a55c11; }
.meta-chip.dim { color: #6e6e6e; }

.reject-bar {
  border: 1px solid rgba(230, 145, 56, .35); background: rgba(230, 145, 56, .07);
  border-radius: var(--asc-radius); padding: 10px 12px; margin-bottom: 14px;
}
.reject-title { font-size: 12px; font-weight: 600; color: #8a4d0f; margin-bottom: 6px; }
.reject-row { display: flex; gap: 8px; font-size: 12px; line-height: 1.8; color: var(--asc-text-2); }
.reject-file { flex-shrink: 0; color: #8a4d0f; }
.reject-reason { color: var(--asc-text-2); }

.skills-loading { padding: 40px 0; text-align: center; font-size: 13px; color: var(--asc-text-2); }

.skills-empty {
  padding: 48px 24px; text-align: center; border: 1px dashed var(--asc-border);
  border-radius: var(--asc-radius); background: var(--asc-card);
}
.empty-title { font-size: 15px; font-weight: 600; color: var(--asc-text); margin-bottom: 8px; }
.empty-desc { font-size: 13px; color: var(--asc-text-2); line-height: 1.7; }
.empty-path {
  display: inline-block; margin-top: 12px; padding: 5px 10px; font-size: 12px;
  background: var(--asc-surface-2); border-radius: 6px; color: var(--asc-text-2);
  word-break: break-all; max-width: 100%;
}

.skill-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(268px, 1fr)); gap: 12px; }
.skill-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: var(--asc-radius); padding: 14px; cursor: pointer;
  transition: box-shadow .15s, border-color .15s; display: flex; flex-direction: column;
}
.skill-card:hover { box-shadow: var(--asc-shadow-hover); border-color: var(--asc-primary-soft); }
/* 停用态：**虚线边框**承担主要辨识，不靠底色差。
   实测：页面底 #f5f5f6 与 --asc-surface-2 #f0f0f2 只差 5 个色阶，纯靠灰底根本看不出来。
   也不降 opacity —— 半透明会把 12.5px 次要文字压到 4.5:1 以下。 */
.skill-card.off { background: var(--asc-surface-2); border: 1px dashed #c4c4cc; }
.skill-card.off .card-name { color: var(--asc-text-2); }
.card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.card-icon {
  width: 24px; height: 24px; flex-shrink: 0; border-radius: 7px;
  display: flex; align-items: center; justify-content: center;
  background: var(--asc-primary-soft); color: var(--asc-primary);
  font-size: 12px; font-weight: 600;
}
.card-name { font-size: 14px; font-weight: 600; color: var(--asc-text); }
.card-badge {
  margin-left: auto; font-size: 11px; padding: 1px 7px; border-radius: 999px; white-space: nowrap;
}
.badge-builtin { background: var(--asc-primary-soft); color: var(--asc-primary); }
.badge-local { background: var(--asc-surface-2); color: var(--asc-text-2); }
.badge-imported { background: rgba(230, 145, 56, .14); color: #8a4d0f; }
.card-desc { font-size: 12.5px; line-height: 1.7; color: var(--asc-text-2); flex: 1; }
.card-foot { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 10px; }
.trig {
  font-size: 11px; padding: 1px 6px; border-radius: 4px;
  background: var(--asc-surface-2); color: var(--asc-text-2); white-space: nowrap;
}
.trig.more { background: transparent; color: #6e6e6e; }
/* 能力标签比触发词更"硬"（它是行为差异，不是匹配规则），用描边区分而不是换色 */
.trig.cap { background: transparent; border: 1px solid var(--asc-border); color: var(--asc-text); }
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

.skills-tip {
  margin-top: 18px; padding: 10px 12px; font-size: 12px; line-height: 1.8;
  color: var(--asc-text-2); background: var(--asc-surface-2);
  border-radius: var(--asc-radius); border-left: 2px solid var(--asc-border);
}
.skills-tip b { color: var(--asc-text); font-weight: 600; }

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
.import-file { font-size: 12px; color: var(--asc-text-2); }
</style>
