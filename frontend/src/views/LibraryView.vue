<template>
  <div class="page library"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop">
    <!-- 整页拖拽上传遮罩 -->
    <div v-if="dragging" class="drop-mask">
      <div class="drop-tip">
        <el-icon class="drop-tip-icon"><Upload /></el-icon>
        松开鼠标上传材料（PDF / PPT / Word / Markdown / EPUB / 图片 / 音视频，≤100MB）
      </div>
    </div>

    <!-- 页头：标题 + 统计 / 搜索 + 主操作 -->
    <div class="page-header">
      <div class="header-left">
        <h2>材料库</h2>
        <span class="header-sub">共 {{ materials.length }} 份材料<template v-if="folders.length"> · {{ folders.length }} 个文件夹</template></span>
      </div>
      <div class="header-actions">
        <div class="search-group">
          <el-radio-group v-model="searchMode" size="small">
            <el-radio-button value="title">标题</el-radio-button>
            <el-radio-button value="fulltext">全文</el-radio-button>
          </el-radio-group>
          <el-input v-model="search" :prefix-icon="Search"
            :placeholder="searchMode === 'fulltext' ? '搜索全部材料正文内容' : '搜索材料标题'"
            clearable style="width: 240px" />
        </div>
        <el-upload :show-file-list="false" :http-request="onUpload" accept=".pdf,.ppt,.pptx,.doc,.docx,.md,.markdown,.epub,.jpg,.jpeg,.png,.webp,.bmp,.mp3,.wav,.m4a,.mp4" multiple>
          <el-button type="primary" :icon="Upload">上传材料</el-button>
        </el-upload>
        <el-popover ref="importPop" placement="bottom-end" :width="232" trigger="click" popper-class="import-popper">
          <template #reference>
            <el-button plain>
              导入本地<el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
          </template>
          <div class="import-menu">
            <div class="import-item" @click="onPickLocal('file')">
              <el-icon><Document /></el-icon>导入本地文件
            </div>
            <div class="import-item" @click="onPickLocal('folder')">
              <el-icon><Folder /></el-icon>导入文件夹
            </div>
            <!-- 网页剪藏（P0-6）：先抓取预览 → **逐条确认**才入库。
                 刻意不做"一键全量抓取"：合规边界见方案 §6.2。 -->
            <div class="import-item" @click="openClip(false)">
              <el-icon><Link /></el-icon>粘贴网页链接
            </div>
            <div class="import-item" @click="openClip(true)">
              <el-icon><DocumentCopy /></el-icon>批量粘贴链接
            </div>
            <div class="import-item" @click="openRecentReading">
              <el-icon><Clock /></el-icon>最近阅读
            </div>
            <div class="import-divider"></div>
            <div class="import-mode">
              <div class="import-mode-head">
                <span>导入方式</span>
                <el-switch v-model="refMode" size="small" active-text="引用" inactive-text="复制" inline-prompt />
              </div>
              <div class="import-mode-tip">{{ refMode ? '直接引用原文件，不复制、省空间' : '复制副本到应用数据目录，可迁移' }}</div>
            </div>
          </div>
        </el-popover>
        <el-button text @click="router.push('/editor')">
          <el-icon class="el-icon--left"><Plus /></el-icon>新增文档
        </el-button>
      </div>
    </div>

    <!-- 上传队列（进度反馈） -->
    <div v-if="uploads.length" class="upload-list">
      <div v-for="u in uploads" :key="u.key" class="upload-item">
        <el-icon class="upload-icon" :class="{ 'is-loading': u.status === 'uploading' }">
          <Loading v-if="u.status === 'uploading'" /><Document v-else />
        </el-icon>
        <span class="upload-name" :title="u.name">{{ u.name }}</span>
        <el-progress :percentage="u.progress" :stroke-width="6" style="flex: 1"
          :status="u.status === 'error' ? 'exception' : (u.status === 'done' ? 'success' : '')" />
      </div>
    </div>

    <!-- 视图切换：页头下方第一行，与标签行左对齐（用户反馈「不知道在哪切换浏览方式」，故提到标签之前） -->
    <div v-if="materials.length || folders.length" class="view-bar">
      <el-radio-group v-model="viewMode" size="small" class="view-toggle">
        <el-radio-button value="flat" title="平铺看全部材料（含子文件夹里的文件）">
          <span class="vt-label"><el-icon :size="13"><Grid /></el-icon>全部材料</span>
        </el-radio-button>
        <el-radio-button value="folder" title="按文件夹层级浏览，可新建 / 移动文件夹">
          <span class="vt-label"><el-icon :size="13"><FolderOpened /></el-icon>按文件夹</span>
        </el-radio-button>
      </el-radio-group>
    </div>

    <!-- 标签筛选：仅「全部材料」模式（按文件夹浏览时层级即筛选，标签不参与过滤） -->
    <div v-if="viewMode === 'flat'" class="filter-bar">
      <div class="tag-bar">
        <template v-if="allTags.length">
          <span class="tag-chip" :class="{ on: !activeTag }" @click="activeTag = ''">全部</span>
          <span v-for="tag in allTags" :key="tag" class="tag-chip"
            :class="{ on: activeTag === tag }" @click="activeTag = tag">
            {{ tag }}<el-icon v-if="presetTags.includes(tag)" class="tag-del" title="删除标签" @click.stop="removePresetTag(tag)"><Close /></el-icon>
          </span>
        </template>
        <span v-else class="tag-bar-empty">还没有标签</span>
      </div>
      <span class="tag-add" @click="addPresetTag">
        <el-icon class="tag-add-icon"><Plus /></el-icon>新增标签
      </span>
    </div>

    <!-- 文件夹面包屑（folder 视图） -->
    <div v-if="viewMode === 'folder'" class="crumb-bar">
      <span class="folder-crumb">
        <el-button text size="small" @click="goRoot">根目录</el-button>
        <template v-for="c in crumbPath" :key="c.id">
          <span class="crumb-sep">/</span>
          <el-button text size="small" @click="openFolder(c.id)">{{ c.name }}</el-button>
        </template>
      </span>
      <el-button size="small" :icon="FolderAdd" @click="createFolder">新建文件夹</el-button>
    </div>

    <!-- 空状态 -->
    <template v-if="searchMode === 'title'">
      <el-empty v-if="!loading && filtered.length === 0 && !search" :image="mascot" :image-size="120"
        description="还没有学习材料，点击右上角「上传材料」导入第一份吧" />
      <el-empty v-else-if="!loading && filtered.length === 0" :image="mascot" :image-size="120"
        :description="`没有找到「${search}」相关材料`" />
    </template>

    <!-- 全文搜索结果 -->
    <div v-if="searchMode === 'fulltext' && search" class="ft-results" v-loading="ftLoading">
      <div v-for="(r, i) in ftResults" :key="i" class="ft-item"
        @click="router.push({ path: `/study/${r.material_id}`, query: { page: r.page_no, hl: r.snippet.replace(/…/g, '').trim().slice(0, 30) } })">
        <div class="ft-head">
          <span class="ft-title">{{ r.material_title }}</span>
          <span class="ft-page">P{{ r.page_no }}</span>
        </div>
        <div class="ft-snippet" v-html="hlQuery(r.snippet)"></div>
      </div>
      <el-empty v-if="!ftLoading && ftResults.length === 0" :image="mascot" :image-size="120"
        :description="`正文里没有找到「${search}」`" />
    </div>

    <!-- 文件夹视图 / 平铺视图 -->
    <template v-else>
      <!-- 文件夹区（folder 视图，有子文件夹时） -->
      <div v-if="viewMode === 'folder' && currentFolders.length" class="grid-section">
        <div class="grid-section-title">文件夹</div>
        <div class="card-grid">
          <div v-for="(f, i) in currentFolders" :key="f.id" class="folder-card card-in"
            :style="{ animationDelay: (i % 12) * 35 + 'ms' }" @click="openFolder(f.id)">
            <div class="fmt-chip fmt-folder"><el-icon :size="20"><Folder /></el-icon></div>
            <div class="folder-card-name">{{ f.name }}</div>
            <div class="folder-card-count">{{ f.file_count }} 个文件{{ f.child_count ? ` · ${f.child_count} 个子文件夹` : '' }}</div>
            <div class="folder-card-ops" @click.stop>
              <el-button size="small" text @click="renameFolder(f)">重命名</el-button>
              <el-button size="small" text @click="openMoveDialog('folder', f)">移动</el-button>
              <el-button size="small" text type="danger" @click="deleteFolder(f)">删除</el-button>
            </div>
          </div>
        </div>
      </div>
      <!-- 文件区（平铺视图，或 folder 视图进入文件夹后；列表页不显示文件） -->
      <div v-if="viewMode === 'flat' || activeFolderId != null" class="card-grid">
        <div v-for="(m, i) in displayFiles" :key="m.id" class="material-card card-in"
          :style="{ animationDelay: (i % 12) * 35 + 'ms' }" @click="openStudy(m)">
        <div class="card-top">
          <div class="fmt-chip" :class="'fmt-' + fmtGroup(m.format)">
            <el-icon :size="20"><component :is="fmtIcon(m.format)" /></el-icon>
          </div>
          <div class="card-head">
            <div class="card-title" :title="m.title">{{ m.title }}</div>
            <div class="card-meta">
              <span class="fmt-name" :class="'ftn-' + fmtGroup(m.format)">{{ fmtLabel(m.format) }}</span>
              <!-- 来源渠道徽章：只标"外部来源"。upload 是默认值不标（否则每张卡都多一个无信息量的标签），
                   local 已由下方「引用」标记表达，不重复。 -->
              <span v-if="originLabel(m.origin)" class="origin-chip" :class="'o-' + m.origin"
                :title="originTitle(m)">{{ originLabel(m.origin) }}</span>
              <span v-if="m.storage_mode === 'reference'" class="ref-badge">引用</span>
              <span v-if="m.page_count">{{ m.page_count }}{{ countUnit(m.format) }}</span>
              <span>{{ formatTime(m.created_at) }}</span>
            </div>
          </div>
        </div>
        <div class="card-body">
          <div class="card-tags-row">
            <!-- 材料状态：与标签同行、置于行首，避免右侧标签变宽挤压标题/日期行 -->
            <div v-if="statusTag(m)" class="card-status" @click.stop>
              <el-tag :type="statusTag(m).type" size="small" effect="light" round :title="statusTag(m).title">
                <el-icon v-if="statusTag(m).loading" class="is-loading"><Loading /></el-icon>
                {{ statusTag(m).text }}
              </el-tag>
            </div>
            <!-- 标签块：「+ 标签」默认紧随状态标签，添加标签后自动跟到标签之后 -->
            <div v-if="m.tags?.length" class="card-tags">
              <span v-for="t in m.tags" :key="t" class="mini-tag">{{ t }}</span>
            </div>
            <el-popover trigger="click" width="260" @click.stop>
              <template #reference>
                <span class="tag-edit" @click.stop>+ 标签</span>
              </template>
              <el-select :model-value="m.tags" multiple filterable allow-create default-first-option
                placeholder="输入回车创建标签" style="width: 100%"
                @change="(v) => saveTags(m, v)" @click.stop>
                <el-option v-for="t in allTags" :key="t" :label="t" :value="t" />
              </el-select>
            </el-popover>
          </div>
          <div v-if="m.parsed_status === 'parsing' && m.parse_progress > 1" class="parse-progress">
            <div class="progress-track">
              <div class="progress-fill" :style="{ width: m.parse_progress + '%' }"></div>
            </div>
            <span class="progress-text">{{ m.parse_progress }}%</span>
          </div>
          <div v-if="m.page_count && m.last_read_page" class="progress-row">
            <div class="progress-track">
              <div class="progress-fill" :style="{ width: Math.min(m.last_read_page / m.page_count * 100, 100) + '%' }"></div>
            </div>
            <span class="progress-text">读到 P{{ m.last_read_page }}</span>
          </div>
          <!-- 平铺模式：材料归属的文件夹，点击进入该文件夹（位于阅读进度下方）-->
          <div v-if="viewMode === 'flat' && m.folder_id" class="card-folder-row">
            <span class="folder-chip" :title="`点击进入文件夹：${folderPath(m.folder_id)}`"
              @click.stop="enterFolder(m.folder_id)">
              <el-icon :size="12"><Folder /></el-icon>
              <span class="folder-chip-name">{{ folderName(m.folder_id) }}</span>
            </span>
          </div>
          <div class="card-footer">
            <span class="note-count">笔记 {{ m.note_count }}</span>
            <div class="card-ops" :class="{ 'ops-always': m.parsed_status === 'failed' }" @click.stop>
              <el-button v-if="['md','markdown'].includes(m.format)" size="small" text @click="router.push(`/editor/${m.id}`)">编辑</el-button>
              <el-button size="small" text @click="openMoveDialog('material', m)">移动到</el-button>
              <el-button v-if="m.parsed_status === 'failed'" size="small" @click="onReparse(m)">重试</el-button>
              <el-popconfirm
                :title="`将删除原文、AI 总结和资料索引；${m.note_count} 条笔记会保留在知识库中`"
                confirm-button-text="删除" confirm-button-type="danger" cancel-button-text="取消"
                width="280" @confirm="onDelete(m)">
                <template #reference>
                  <el-button size="small" type="danger" text>删除</el-button>
                </template>
              </el-popconfirm>
            </div>
          </div>
        </div>
      </div>
    </div>
      <!-- folder 视图空状态：列表页无文件夹 -->
      <el-empty v-if="viewMode === 'folder' && activeFolderId == null && currentFolders.length === 0"
        :image="mascot" :image-size="120" description="还没有文件夹，点击「新建文件夹」创建一个" />
    </template>

    <!-- 网页剪藏：粘贴链接 → 候选列表 → 逐条确认（单篇/批量共用同一组件） -->
    <el-dialog v-model="clipDialog.show" :close-on-click-modal="false"
      :title="clipDialog.batch ? '批量粘贴链接' : '粘贴网页链接'" width="720px">
      <template v-if="clipDialog.stage === 'input'">
        <el-input v-if="!clipDialog.batch" v-model="clipDialog.url" clearable
          placeholder="粘贴网页链接（支持博客 / 新闻站 / 公众号文章）"
          @keydown.enter.exact="submitClip" />
        <el-input v-else v-model="clipDialog.urls" type="textarea" :rows="6" resize="vertical"
          placeholder="每行一个链接（可粘贴完整分享文案，自动提取其中链接），一次最多 20 个" />
        <div class="clip-tip">
          支持大部分博客、新闻站与公众号文章。<strong>动态渲染的页面</strong>抓不到正文时，
          会让你手动粘贴正文继续，其余流程不变。
        </div>
      </template>
      <CandidateList v-else :items="clipDialog.items" @done="onClipDone"
        @saved="onClipSaved" @open-material="openClipMaterial" />
      <template v-if="clipDialog.stage === 'input'" #footer>
        <el-button @click="clipDialog.show = false">取消</el-button>
        <el-button type="primary" :loading="clipDialog.loading" @click="submitClip">开始抓取</el-button>
      </template>
    </el-dialog>

    <!-- 移动目标选择对话框 -->
    <el-dialog v-model="moveDialog.show" :title="moveDialog.type === 'folder' ? `移动文件夹「${moveDialog.name}」` : `移动文件「${moveDialog.name}」`" width="420px">
      <div class="move-list">
        <div class="move-item" @click="confirmMove(null)">
          <el-icon class="move-icon"><Folder /></el-icon>
          <span>{{ moveDialog.type === 'folder' ? '顶层（根目录）' : '未分组' }}</span>
        </div>
        <div v-for="f in moveOptions" :key="f.id" class="move-item" @click="confirmMove(f.id)">
          <el-icon class="move-icon"><Folder /></el-icon>
          <span :style="{ marginLeft: f.depth * 14 + 'px' }">{{ f.name }}</span>
        </div>
        <el-empty v-if="moveOptions.length === 0" description="暂无其他文件夹" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, ArrowDown, Folder, FolderAdd, FolderOpened, Grid, Plus, Close, Search, Upload, Document, Picture, Headset, VideoPlay, Reading, Link, DocumentCopy, Clock } from '@element-plus/icons-vue'
import { materialApi, foldersApi, settingsApi, clipApi } from '../api'
import CandidateList from '../components/CandidateList.vue'
import { useReadingStore } from '../stores/reading'
import mascot from '../assets/mascot.png'

const router = useRouter()
const materials = ref([])
const search = ref('')
const loading = ref(false)
const dragging = ref(false)
const uploads = reactive([])   // 上传队列：{ key, name, progress, status }
let uploadSeq = 0
let pollTimer = null
const refMode = ref(true)   // true=引用原文件 / false=复制副本
const viewMode = ref('flat')      // flat 平铺 / folder 文件夹
const folders = ref([])           // Folder 平铺列表
const activeFolderId = ref(null)  // 当前进入的文件夹 id，null=顶层

const searchMode = ref('title')
const ftResults = ref([])
const ftLoading = ref(false)
let ftTimer = null

// 全文搜索：输入防抖 400ms（folder 视图进入文件夹时，限定当前文件夹含子文件夹）
watch([search, searchMode], ([q, mode]) => {
  if (ftTimer) clearTimeout(ftTimer)
  if (mode !== 'fulltext' || !q.trim()) { ftResults.value = []; return }
  ftTimer = setTimeout(async () => {
    ftLoading.value = true
    try {
      const folderId = (viewMode.value === 'folder' && activeFolderId.value != null) ? activeFolderId.value : null
      const { data } = await materialApi.fulltextSearch(q.trim(), folderId)
      ftResults.value = data
    } finally {
      ftLoading.value = false
    }
  }, 400)
})

const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
function hlQuery(snippet) {
  const q = search.value.trim()
  const esc = escapeHtml(snippet)
  if (!q) return esc
  return esc.split(escapeHtml(q)).join(`<mark>${escapeHtml(q)}</mark>`)
}

const activeTag = ref('')
const presetTags = ref([])   // 预设标签库（settings 持久化，可先建标签再打给材料）

// 标签全集 = 预设标签 + 材料已有标签（去重）
const allTags = computed(() => {
  const set = new Set(presetTags.value)
  for (const m of materials.value) (m.tags || []).forEach(t => set.add(t))
  return [...set]
})

async function loadPresetTags() {
  try {
    const { data } = await settingsApi.get()
    presetTags.value = data.preset_tags || []
  } catch { /* 静默 */ }
}

// 新增预设标签
async function addPresetTag() {
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt('输入标签名，创建后可给任意材料打上该标签', '新增标签')
    name = (value || '').trim()
  } catch { return }
  if (!name) return
  if (name.length > 20) { ElMessage.warning('标签名最长 20 字'); return }
  if (allTags.value.includes(name)) { ElMessage.warning(`标签「${name}」已存在`); return }
  try {
    const next = [...presetTags.value, name]
    await settingsApi.update({ preset_tags: next })
    presetTags.value = next
    ElMessage.success(`已新增标签「${name}」`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '新增失败')
  }
}

// 删除预设标签（先校验是否有关联材料，未关联则二次确认后删除）
async function removePresetTag(tag) {
  const linked = materials.value.filter(m => (m.tags || []).includes(tag)).length
  if (linked > 0) {
    ElMessage.warning(`标签「${tag}」已关联 ${linked} 份材料，请先解除关联后再删除`)
    return
  }
  try {
    await ElMessageBox.confirm(`确认删除标签「${tag}」？删除后不可恢复。`, '删除标签', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning'
    })
  } catch { return }
  try {
    const next = presetTags.value.filter(t => t !== tag)
    await settingsApi.update({ preset_tags: next })
    presetTags.value = next
    if (activeTag.value === tag) activeTag.value = ''
    ElMessage.success(`已删除标签「${tag}」`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '删除失败')
  }
}

const filtered = computed(() => {
  let list = materials.value
  if (activeTag.value) list = list.filter(m => (m.tags || []).includes(activeTag.value))
  if (search.value) list = list.filter(m => m.title.includes(search.value))
  return list
})

// ---------- 文件夹 ----------
async function loadFolders() {
  const { data } = await foldersApi.list()
  folders.value = data
}

// 当前层级的子文件夹
const currentFolders = computed(() =>
  folders.value.filter(f => (f.parent_id ?? null) === activeFolderId.value))

// 当前层级的文件（folder 视图）
const currentFiles = computed(() =>
  materials.value.filter(m => (m.folder_id ?? null) === activeFolderId.value))

// 当前文件夹 + 子文件夹 id 集合（folder 视图进入文件夹时，用于「当前文件夹内搜索」）
const currentFolderScopeIds = computed(() => {
  if (viewMode.value !== 'folder' || activeFolderId.value == null) return null
  const ids = new Set([activeFolderId.value])
  let changed = true
  while (changed) {
    changed = false
    for (const f of folders.value) {
      if (ids.has(f.parent_id) && !ids.has(f.id)) {
        ids.add(f.id)
        changed = true
      }
    }
  }
  return ids
})

// 文件区显示的数据源：flat=全部（tag/搜索过滤）；folder 进入后=当前层级文件，搜索时跨子文件夹
const displayFiles = computed(() => {
  if (viewMode.value === 'flat') {
    return filtered.value
  }
  if (activeFolderId.value == null) return []   // 列表页不显示文件
  if (search.value) {
    const scope = currentFolderScopeIds.value
    return materials.value.filter(m => scope && scope.has(m.folder_id) && m.title.includes(search.value))
  }
  return currentFiles.value
})

// 面包屑路径（根 → ... → 当前）
const crumbPath = computed(() => {
  const path = []
  let cur = activeFolderId.value
  const guard = new Set()
  while (cur != null && !guard.has(cur)) {
    guard.add(cur)
    const f = folders.value.find(x => x.id === cur)
    if (!f) break
    path.unshift(f)
    cur = f.parent_id
  }
  return path
})

// 文件夹 id → 对象（平铺模式显示「归属文件夹」用）
const folderById = computed(() => {
  const map = new Map()
  for (const f of folders.value) map.set(f.id, f)
  return map
})

function folderName(id) {
  return folderById.value.get(id)?.name || '文件夹'
}

// 完整路径（根 → … → 当前），供 title 提示
function folderPath(id) {
  const names = []
  let cur = id
  const guard = new Set()
  while (cur != null && !guard.has(cur)) {
    guard.add(cur)
    const f = folderById.value.get(cur)
    if (!f) break
    names.unshift(f.name)
    cur = f.parent_id
  }
  return names.length ? names.join(' / ') : '根目录'
}

function openFolder(id) { activeFolderId.value = id }
function goRoot() { activeFolderId.value = null }

// 平铺模式下点击材料的归属文件夹 → 进入该文件夹（folder 视图即文件夹详情：子文件夹 + 文件 + 面包屑）
function enterFolder(id) {
  if (id == null) return
  activeFolderId.value = id
  viewMode.value = 'folder'
}

// 仅切回平铺时清空所在文件夹；进入 folder 视图需保留（否则 enterFolder 会被重置为根目录）
watch(viewMode, (v) => { if (v === 'flat') activeFolderId.value = null })

// 新建文件夹
async function createFolder() {
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt('请输入文件夹名称', '新建文件夹')
    name = (value || '').trim()
  } catch { return }
  if (!name) return
  try {
    await foldersApi.create({ name, parent_id: activeFolderId.value })
    ElMessage.success('已创建文件夹')
    await loadFolders()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '创建失败')
  }
}

// 重命名文件夹
async function renameFolder(f) {
  let name = ''
  try {
    const { value } = await ElMessageBox.prompt('重命名文件夹', '重命名', { inputValue: f.name })
    name = (value || '').trim()
  } catch { return }
  if (!name || name === f.name) return
  try {
    await foldersApi.update(f.id, { name })
    ElMessage.success('已重命名')
    await loadFolders()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '重命名失败')
  }
}

// 删除文件夹（连带删文件）
async function deleteFolder(f) {
  try {
    await ElMessageBox.confirm(
      `将删除文件夹「${f.name}」及其下所有文件${f.child_count ? '和子文件夹' : ''}。引用文件只解绑、不删原文件。`,
      '删除文件夹', { confirmButtonText: '删除', confirmButtonType: 'danger', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  try {
    const { data } = await foldersApi.remove(f.id)
    ElMessage.success(`已删除文件夹，清理 ${data.deleted_files} 个文件`)
    // 若删除的是当前所在文件夹或其祖先，回到根目录，避免指向已删文件夹
    if (crumbPath.value.some(c => c.id === f.id)) goRoot()
    await refresh()
    await loadFolders()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '删除失败')
  }
}

// ---------- 移动（文件夹 / 文件） ----------
const moveDialog = reactive({ show: false, type: 'material', id: null, name: '' })

// 展平文件夹为带深度的可选列表（移动文件夹时排除自身及子孙）
const moveOptions = computed(() => {
  const exclude = new Set()
  if (moveDialog.type === 'folder' && moveDialog.id != null) {
    const collect = (id) => {
      exclude.add(id)
      folders.value.filter(f => f.parent_id === id).forEach(f => collect(f.id))
    }
    collect(moveDialog.id)
  }
  const depthOf = (id) => {
    let d = 0, cur = id
    const seen = new Set()
    while (cur != null && !seen.has(cur)) {
      seen.add(cur)
      const f = folders.value.find(x => x.id === cur)
      if (!f) break
      cur = f.parent_id
      d++
    }
    return d
  }
  return folders.value
    .filter(f => !exclude.has(f.id))
    .map(f => ({ ...f, depth: depthOf(f.id) }))
    .sort((a, b) => a.depth - b.depth || a.name.localeCompare(b.name))
})

function openMoveDialog(type, obj) {
  moveDialog.type = type
  moveDialog.id = obj.id
  moveDialog.name = obj.name || obj.title
  moveDialog.show = true
}

async function confirmMove(targetId) {
  try {
    if (moveDialog.type === 'material') {
      await materialApi.update(moveDialog.id, { folder_id: targetId })
    } else {
      await foldersApi.update(moveDialog.id, { parent_id: targetId })
    }
    moveDialog.show = false
    ElMessage.success('已移动')
    await refresh()
    await loadFolders()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '移动失败')
  }
}

async function saveTags(m, tags) {
  await materialApi.update(m.id, { tags })
  m.tags = tags
  ElMessage.success('标签已保存')
}

const isMediaFmt = (f) => ['mp3', 'wav', 'm4a', 'mp4'].includes(f)
const isImageFmt = (f) => ['jpg', 'jpeg', 'png', 'webp', 'bmp'].includes(f)
const isSlowFmt = (f) => isMediaFmt(f) || isImageFmt(f) || f === 'pdf'
const slowLabel = (f) => isMediaFmt(f) ? '转写中' : '识别中'

// 材料状态标签：解析中（转写/识别带百分比）/ 解析失败 / 扫描件 / 已总结，无状态返回 null
function statusTag(m) {
  if (m.parsed_status === 'parsing') {
    const pct = m.parse_progress || 0
    return { type: 'primary', loading: true, text: isSlowFmt(m.format) ? `${slowLabel(m.format)} ${pct}%` : '解析中', title: '' }
  }
  if (m.parsed_status === 'failed') return { type: 'danger', text: '解析失败', title: m.parse_error || '' }
  if (m.parsed_status === 'scanned') return { type: 'warning', text: '扫描件', title: m.parse_error || '' }
  if (m.has_ai) return { type: 'success', text: '已总结', title: '' }
  return null
}
const fmtLabel = (f) => ({ pdf: 'PDF', ppt: 'PPT', pptx: 'PPT', doc: 'WORD', docx: 'WORD', epub: 'EPUB', md: 'MD', markdown: 'MD', mp3: '音频', wav: '音频', m4a: '音频', mp4: '视频', jpg: '图片', jpeg: '图片', png: '图片', webp: '图片', bmp: '图片' }[f] || (f || '').toUpperCase())

// 格式 → 图标分组（决定图标与徽章配色）
const fmtGroup = (f) => {
  if (isImageFmt(f)) return 'img'
  if (isMediaFmt(f)) return f === 'mp4' ? 'video' : 'audio'
  if (['ppt', 'pptx'].includes(f)) return 'ppt'
  if (['doc', 'docx'].includes(f)) return 'doc'
  if (['md', 'markdown'].includes(f)) return 'md'
  if (f === 'epub') return 'epub'
  return 'pdf'
}
// EPUB 的计数单位是「章」（章节序号即 page_no），其余格式仍是「页」
const countUnit = (f) => (f === 'epub' ? ' 章' : ' 页')
const fmtIcon = (f) => ({ img: Picture, audio: Headset, video: VideoPlay, epub: Reading }[fmtGroup(f)] || Document)

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

async function refresh() {
  const { data } = await materialApi.list()
  materials.value = data
  // 有解析中的材料时保持轮询
  const parsing = data.some(m => m.parsed_status === 'parsing')
  if (parsing && !pollTimer) {
    pollTimer = setInterval(refresh, 2000)
  } else if (!parsing && pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function doUpload(file) {
  const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : ''
  if (!['pdf', 'ppt', 'pptx', 'doc', 'docx', 'md', 'markdown', 'epub', 'jpg', 'jpeg', 'png', 'webp', 'bmp', 'mp3', 'wav', 'm4a', 'mp4'].includes(ext)) {
    ElMessage.warning(`「${file.name}」格式不支持（PDF/PPT/Word/Markdown/EPUB/图片/音视频）`)
    return
  }
  if (file.size > 100 * 1024 * 1024) {
    ElMessage.warning(`「${file.name}」超过 100MB，请压缩后上传`)
    return
  }
  const item = reactive({ key: ++uploadSeq, name: file.name, progress: 0, status: 'uploading' })
  uploads.push(item)
  try {
    const fd = new FormData()
    fd.append('file', file)
    await materialApi.upload(fd, (pct) => { item.progress = pct })
    item.progress = 100
    item.status = 'done'
    ElMessage.success(`「${file.name}」上传成功，解析中`)
    await refresh()
  } catch (e) {
    item.status = 'error'
    ElMessage.error(e.response?.data?.detail || e.message || `「${file.name}」上传失败`)
  } finally {
    // 完成/失败条目短暂停留后自动移除
    setTimeout(() => {
      const idx = uploads.indexOf(item)
      if (idx > -1) uploads.splice(idx, 1)
    }, item.status === 'done' ? 4000 : 8000)
  }
}

const onUpload = ({ file }) => doUpload(file)
const onDrop = (e) => {
  dragging.value = false
  const files = [...(e.dataTransfer?.files || [])]
  files.forEach(doUpload)   // 多文件并行上传，各自有进度条
}

// ───────── 网页剪藏（P0-6）：粘贴链接 → 候选列表 → 逐条确认入库 ─────────
const reading = useReadingStore()
const clipDialog = reactive({
  show: false, batch: false, stage: 'input', url: '', urls: '', items: [], loading: false
})

function openClip(batch) {
  importPop.value?.hide()
  reading.clearClipDraft()   // N11：新开一轮剪藏 → 丢掉上一轮寄存的候选
  Object.assign(clipDialog, { show: true, batch, stage: 'input', url: '', urls: '', items: [], loading: false })
}

/** 「最近阅读」入口：它是从"浏览"到"沉淀"的中间态，故不占左侧主导航，放这里进。 */
function openRecentReading() {
  importPop.value?.hide()
  reading.clearHandoff()
  router.push({ path: '/read', query: { recent: '1' } })
}

async function submitClip() {
  if (clipDialog.batch) {
    const urls = clipDialog.urls.split(/\r?\n/).map(s => s.trim()).filter(Boolean)
    if (!urls.length) { ElMessage.warning('请粘贴至少一个链接'); return }
    if (urls.length > 20) { ElMessage.warning('一次最多 20 个链接，请分批粘贴'); return }
    clipDialog.loading = true
    try {
      const { data } = await clipApi.batchPreview({ urls })
      clipDialog.items = data.items || []
      clipDialog.stage = 'items'
      stashClip()
    } catch (e) {
      ElMessage.error(clipErr(e))
    } finally {
      clipDialog.loading = false
    }
    return
  }
  const url = clipDialog.url.trim()
  if (!url) { ElMessage.warning('请粘贴网页链接'); return }
  clipDialog.loading = true
  try {
    const { data } = await clipApi.preview({ url })
    clipDialog.items = [data]
    clipDialog.stage = 'items'
    stashClip()
  } catch (e) {
    ElMessage.error(clipErr(e))
  } finally {
    clipDialog.loading = false
  }
}

/** N11：把「候选列表」整批寄存到 store。
 *  ⚠️ 「仅阅读」会 router.push('/read') → LibraryView 卸载（App.vue 无 keep-alive）
 *     → 组件内 reactive 全丢，**同批其余候选凭空消失**，用户得从头再粘一遍链接。
 *     存的是同一个数组引用：条目上的 `_paste` 草稿 / `saved` / `material_id` 一并保住，
 *     不需要序列化、不写盘（应用重启即空，与「最近阅读」同语义）。
 */
function stashClip() {
  reading.setClipDraft({
    batch: clipDialog.batch,
    stage: clipDialog.stage,
    url: clipDialog.url,
    urls: clipDialog.urls,
    items: clipDialog.items
  })
}

function onClipSaved() { refresh() }   // 新条目正在后台解析 → 刷新列表即可看到进度

function onClipDone() {
  clipDialog.show = false
  refresh()
  // 候选列表里点「仅阅读」已把正文放进交接 store，这里负责导航
  if (reading.handoff) {
    stashClip()             // N11：整批候选先寄存，从阅读页回来时原样恢复
    router.push('/read')
    return
  }
  reading.clearClipDraft()  // 点「完成」= 本轮结束，清掉寄存
}

function openClipMaterial(it) {
  if (it.material_id) router.push(`/study/${it.material_id}`)
}

function clipErr(e) {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object') return d.hint || d.reason || '抓取失败'
  return e?.message || '抓取失败'
}

// 来源渠道徽章：只标外部来源（upload / local 不标，见卡片模板注释）
const ORIGIN_LABELS = { url: '网页剪藏', wx: '公众号文章', xhs: '小红书笔记', weread: '微信读书' }
const originLabel = (o) => ORIGIN_LABELS[o] || ''
const originTitle = (m) => {
  const meta = m.origin_meta || {}
  const bits = [meta.sitename, meta.author, meta.date].filter(Boolean)
  return bits.length ? `${originLabel(m.origin)} · ${bits.join(' · ')}` : originLabel(m.origin)
}

// 导入本地文件/文件夹：客户端走 pywebview 原生对话框，开发态（浏览器）粘贴路径兜底
const importPop = ref(null)
function onPickLocal(mode) {
  importPop.value?.hide()
  pickLocal(mode)
}
async function pickLocal(mode) {   // mode: 'file' | 'folder'
  let paths = []
  if (window.pywebview && window.pywebview.api) {
    try {
      paths = mode === 'folder'
        ? await window.pywebview.api.pick_folder()
        : await window.pywebview.api.pick_files()
    } catch (e) {
      ElMessage.error('调用本地文件选择失败：' + (e?.message || e))
      return
    }
  } else {
    let input = ''
    try {
      const { value } = await ElMessageBox.prompt(
        '请粘贴本地文件或文件夹的完整路径（多个用逗号或换行分隔）',
        mode === 'folder' ? '导入本地文件夹' : '导入本地文件',
        { inputType: 'textarea', inputPlaceholder: 'C:\\Users\\xxx\\资料\\xxx.pdf' })
      input = value || ''
    } catch { return }   // 用户取消
    // 剥掉两端成对引号（Windows 资源管理器复制的路径自带 ""）
    const cleanPath = (s) => {
      let t = (s || '').trim()
      while (t.length >= 2 && (t[0] === '"' || t[0] === "'") && t[t.length - 1] === t[0]) t = t.slice(1, -1).trim()
      return t
    }
    paths = input.split(/[,，\n]/).map(cleanPath).filter(Boolean)
  }
  if (!paths || !paths.length) return
  try {
    const { data } = await materialApi.importLocal({ paths, mode: refMode.value ? 'reference' : 'copy' })
    if (data.failed?.length) console.warn('[import-local] 失败明细', data.failed)
    ElMessage.success(`已导入 ${data.created.length} 份${data.failed?.length ? `，失败 ${data.failed.length} 份` : ''}`)
    await refresh()
    await loadFolders()   // 导入文件夹会新建 Folder，同步刷新
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message || '导入失败')
  }
}

function openStudy(m) {
  if (m.parsed_status === 'parsing') {
    ElMessage.info('材料解析中，请稍候')
    return
  }
  router.push(`/study/${m.id}`)
}

async function onReparse(m) {
  await materialApi.retryParse(m.id)
  ElMessage.success('已重新解析')
  await refresh()
}

async function onDelete(m) {
  await materialApi.remove(m.id)
  ElMessage.success('已删除')
  await refresh()
}

onMounted(async () => {
  // N11：从阅读页返回时把寄存的候选列表还给对话框 —— 否则用户得重新粘一遍链接。
  // ⚠️ 只有「仅阅读」会留下 draft（点「完成」时已清），所以这里弹出来正是「继续处理」。
  if (reading.clipDraft) {
    Object.assign(clipDialog, reading.clipDraft, { show: true, loading: false })
  }
  loading.value = true
  await Promise.all([refresh(), loadFolders(), loadPresetTags()])
  loading.value = false
})
onUnmounted(() => pollTimer && clearInterval(pollTimer))
</script>

<style scoped>
.library { position: relative; min-height: 100%; }

/* ===== 拖拽上传遮罩 ===== */
.drop-mask {
  position: fixed; inset: 0; z-index: 100;
  background: rgba(124, 92, 252, 0.08);
  border: 2px dashed var(--asc-primary);
  display: flex; align-items: center; justify-content: center;
  backdrop-filter: blur(2px);
}
.drop-tip {
  display: flex; align-items: center; gap: 10px;
  font-size: 15px; color: var(--asc-primary); background: var(--asc-card);
  padding: 18px 32px; border-radius: 14px; font-weight: 500;
  box-shadow: 0 8px 32px rgba(124, 92, 252, .18);
}
.drop-tip-icon { font-size: 22px; }

/* ===== 页头 ===== */
.page-header {
  display: flex; justify-content: space-between; align-items: flex-end;
  gap: 16px 24px; flex-wrap: wrap;
  margin-bottom: 20px; padding-bottom: 20px;
  border-bottom: 1px solid var(--asc-divider);
}
.header-left { display: flex; align-items: baseline; gap: 12px; }
.page-header h2 { margin: 0; font-size: 20px; font-weight: 600; letter-spacing: .2px; }
.header-sub { font-size: 12px; color: var(--asc-text-3); }
.header-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.search-group {
  display: flex; align-items: center; gap: 8px;
  padding-right: 12px;
  border-right: 1px solid var(--asc-divider);
}
.search-group :deep(.el-input__wrapper) { border-radius: 8px !important; }

/* ===== 导入本地弹出菜单 ===== */
.import-menu { display: flex; flex-direction: column; }
.import-item {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-radius: 8px; cursor: pointer;
  font-size: 13px; color: var(--asc-text);
  transition: background .15s;
}
.import-item:hover { background: var(--asc-primary-soft); color: var(--asc-primary); }
.import-divider { height: 1px; background: var(--asc-divider); margin: 6px 0; }
.import-mode { padding: 4px 10px 6px; }
.import-mode-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12px; color: var(--asc-text-2); margin-bottom: 6px;
}
.import-mode-tip { font-size: 11px; color: var(--asc-text-3); line-height: 1.5; }
/* 剪藏对话框说明 */
.clip-tip {
  font-size: 12px; color: var(--asc-text-2); line-height: 1.7;
  background: var(--asc-surface-2); border-radius: 6px; padding: 8px 10px; margin-top: 10px;
}
/* 卡片来源徽章（网页/公众号/微信读书）：与格式名同行的轻量标签 */
.origin-chip {
  font-size: 11px; font-weight: 600; border-radius: 4px;
  padding: 0 6px; line-height: 16px; white-space: nowrap;
}
.o-url { color: #2563eb; background: #e8f0fd; }
.o-wx { color: #15803d; background: #e8f6ed; }
.o-weread { color: #15803d; background: #e8f6ed; }

/* ===== 上传队列 ===== */
.upload-list {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 10px 16px; margin-bottom: 18px;
}
.upload-item { display: flex; align-items: center; gap: 12px; padding: 5px 0; }
.upload-icon { color: var(--asc-primary); font-size: 15px; flex-shrink: 0; }
.upload-name {
  font-size: 13px; max-width: 260px; flex-shrink: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* ===== 筛选条（标签） ===== */
.filter-bar {
  display: flex; align-items: center; gap: 8px; margin-bottom: 18px;
}
/* 标签恒定单行（超出横向滚动、隐藏滚动条）——与「浏览方式」行等高，不因标签多/窗口窄折行。
   宽度按内容自适应：标签放得下时「+ 新增标签」紧跟在最后一个标签之后；
   标签超出时标签区收缩为可滚动，「+ 新增标签」仍留在行尾可见。 */
.tag-bar {
  min-width: 0; display: flex; align-items: center; gap: 8px;
  flex-wrap: nowrap; overflow-x: auto; min-height: 28px;
  scrollbar-width: none; -ms-overflow-style: none;
}
.tag-bar::-webkit-scrollbar { height: 0; display: none; }
.tag-bar-empty { font-size: 12px; color: var(--asc-text-3); margin-right: 4px; }
/* 行高统一 28px（与 .view-toggle 一致）：高度写死，避免不同环境字体度量把 chip 撑高导致两行不等高 */
.tag-chip {
  height: 28px; display: inline-flex; align-items: center; padding: 0 12px;
  font-size: 12px; border-radius: 999px; cursor: pointer;
  background: var(--asc-card); border: 1px solid var(--asc-border); color: var(--asc-text-2);
  transition: all .15s;
}
.tag-chip:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
.tag-chip.on { background: var(--asc-primary); border-color: var(--asc-primary); color: #fff; }
.tag-del {
  margin-left: 6px; font-size: 12px; opacity: .5;
  transition: opacity .15s, color .15s;
}
.tag-del:hover { opacity: 1; color: var(--el-color-danger); }
.tag-add {
  height: 28px; display: inline-flex; align-items: center; gap: 3px; padding: 0 10px; flex-shrink: 0;
  font-size: 12px; color: var(--asc-primary); cursor: pointer;
  border: 1px dashed var(--asc-primary); border-radius: 999px; transition: all .15s;
}
.tag-add:hover { background: var(--asc-primary-soft); }
.tag-add-icon { font-size: 12px; }
/* 视图切换条：页头下方第一行、左对齐（与标签行同一起始边；两个视图下位置一致，切换时不跳动） */
.view-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.view-toggle { flex-shrink: 0; }
/* 与标签行等高（28px，同 .tag-chip）：两边都写死高度，两行在任意环境下都一致 */
.view-toggle :deep(.el-radio-button__inner) {
  height: 28px; padding: 0 12px; line-height: 1;
  display: inline-flex; align-items: center;
}
.vt-label { display: inline-flex; align-items: center; gap: 5px; }

/* ===== 面包屑（folder 视图） ===== */
.crumb-bar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; margin-bottom: 18px;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 10px; padding: 4px 12px;
}
.folder-crumb { display: flex; align-items: center; gap: 4px; font-size: 13px; color: var(--asc-text-2); }
.crumb-sep { color: var(--asc-text-3); font-size: 12px; margin: 0 2px; }

/* ===== 卡片网格 ===== */
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 16px; }
.grid-section { margin-bottom: 22px; }
.grid-section-title {
  font-size: 13px; font-weight: 500; color: var(--asc-text-2);
  margin-bottom: 12px; padding-left: 2px;
}

/* 卡片进场：逐张错峰淡入 */
@keyframes card-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
.card-in { animation: card-in .3s ease both; }
@media (prefers-reduced-motion: reduce) {
  .card-in { animation: none; }
  .material-card, .folder-card, .ft-item { transition: none; }
}

/* ===== 材料卡片 ===== */
.material-card {
  position: relative;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 14px; overflow: hidden; cursor: pointer;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
  display: flex; flex-direction: column;
}
.material-card:hover {
  transform: translateY(-3px);
  border-color: #c9b8fe;
  box-shadow: 0 10px 28px rgba(124, 92, 252, .1);
}
.card-top { display: flex; align-items: flex-start; gap: 12px; padding: 16px 16px 0; }
.card-head { flex: 1; min-width: 0; }
/* 状态标签（在标签行行首）：不参与压缩，也不与标题/日期争抢行宽 */
.card-status { display: inline-flex; flex-shrink: 0; }
.card-status :deep(.el-tag) { border: none; }

/* 格式图标徽章：低饱和浅色底 + 格式色图标 */
.fmt-chip {
  width: 42px; height: 42px; border-radius: 11px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
.fmt-pdf { background: #fdeee7; color: #d85a30; }
.fmt-ppt { background: #fdf1dd; color: #ba7517; }
.fmt-doc { background: #e9f1fc; color: #378add; }
.fmt-md { background: #eef1f5; color: #64748b; }
.fmt-audio { background: #e5f6f3; color: #0f9488; }
.fmt-video { background: #f1ebfd; color: #7c5cfc; }
.fmt-img { background: #e6f7fb; color: #0891b2; }
.fmt-folder { background: var(--asc-primary-soft); color: var(--asc-primary); }

.card-title {
  font-size: 14px; font-weight: 600; line-height: 1.45;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.card-meta {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  font-size: 12px; color: var(--asc-text-3); margin-top: 4px;
}
/* 元信息整体换行、不折行（避免日期被拆成两行显示） */
.card-meta > span { white-space: nowrap; }
.fmt-name { font-weight: 600; font-size: 11px; letter-spacing: .3px; }
.ftn-pdf { color: #d85a30; } .ftn-ppt { color: #ba7517; } .ftn-doc { color: #378add; }
.ftn-md { color: #64748b; } .ftn-audio { color: #0f9488; } .ftn-video { color: #7c5cfc; }
.ftn-img { color: #0891b2; } .ftn-epub { color: #15803d; }
.ref-badge {
  font-size: 11px; color: var(--asc-text-3); background: var(--asc-surface-2);
  border-radius: 4px; padding: 0 6px; line-height: 16px;
  display: inline-block; vertical-align: middle;
}
/* 平铺模式：材料归属的文件夹（位于阅读进度下方，可点击进入） */
.card-folder-row { display: flex; align-items: center; margin-bottom: 8px; }
.folder-chip {
  display: inline-flex; align-items: center; gap: 4px; max-width: 100%;
  font-size: 12px; color: var(--asc-text-2);
  background: var(--asc-surface-2); border: 1px solid var(--asc-divider);
  border-radius: 5px; padding: 1px 8px; line-height: 18px;
  cursor: pointer; transition: color .15s, border-color .15s, background .15s;
}
.folder-chip:hover {
  color: var(--asc-primary); border-color: var(--asc-primary); background: var(--asc-primary-soft);
}
.folder-chip .el-icon { flex-shrink: 0; }
.folder-chip-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.card-body { padding: 10px 16px 12px; display: flex; flex-direction: column; flex: 1; }
.card-tags-row {
  display: flex; align-items: center; gap: 8px;
  min-height: 22px; margin-bottom: 8px;
}
/* 标签块按内容宽度排列（不撑满整行）：无标签时「+ 标签」紧贴状态标签，有标签时跟在标签之后 */
.card-tags { display: flex; gap: 6px; flex-wrap: wrap; min-width: 0; }
.mini-tag {
  font-size: 11px; color: var(--asc-primary); background: var(--asc-primary-soft);
  border-radius: 4px; padding: 1px 7px;
}
.tag-edit {
  font-size: 11px; color: var(--asc-text-3); cursor: pointer; flex-shrink: 0;
  transition: color .15s;
}
.tag-edit:hover { color: var(--asc-primary); }

.progress-row, .parse-progress { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.progress-track { flex: 1; height: 4px; background: var(--asc-surface-2); border-radius: 2px; overflow: hidden; }
.progress-fill { height: 100%; background: linear-gradient(90deg, var(--asc-primary), #a38dfd); border-radius: 2px; transition: width .3s; }
.progress-text { font-size: 11px; color: var(--asc-text-3); flex-shrink: 0; font-variant-numeric: tabular-nums; }

.card-footer {
  display: flex; justify-content: space-between; align-items: center;
  border-top: 1px solid var(--asc-divider); padding-top: 8px;
  margin-top: auto; min-height: 30px;
}
.note-count { font-size: 12px; color: var(--asc-text-3); }

/* 卡片操作：默认收起，hover / 键盘聚焦时浮现，减少视觉噪音 */
.card-ops { display: flex; gap: 2px; opacity: 0; transition: opacity .15s ease; }
.material-card:hover .card-ops,
.material-card:focus-within .card-ops,
.card-ops.ops-always { opacity: 1; }
.card-ops :deep(.el-button + .el-button) { margin-left: 0; }

/* ===== 文件夹卡片 ===== */
.folder-card {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 14px; padding: 16px; cursor: pointer;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
  display: flex; flex-direction: column; align-items: flex-start;
}
.folder-card:hover {
  transform: translateY(-3px);
  border-color: var(--asc-primary);
  box-shadow: 0 10px 28px rgba(124, 92, 252, .1);
}
.folder-card-name { font-size: 14px; font-weight: 600; margin-top: 10px; }
.folder-card-count { font-size: 12px; color: var(--asc-text-3); margin-top: 3px; }
.folder-card-ops {
  display: flex; gap: 2px; margin-top: 10px; width: 100%;
  border-top: 1px solid var(--asc-divider); padding-top: 8px;
  opacity: 0; transition: opacity .15s ease;
}
.folder-card:hover .folder-card-ops,
.folder-card:focus-within .folder-card-ops { opacity: 1; }
.folder-card-ops :deep(.el-button + .el-button) { margin-left: 0; }

/* ===== 移动对话框 ===== */
.move-list { max-height: 360px; overflow-y: auto; }
.move-item {
  display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  border-radius: 8px; cursor: pointer; font-size: 13px; color: var(--asc-text);
  transition: background .15s;
}
.move-item:hover { background: var(--asc-primary-soft); }
.move-icon { color: var(--asc-primary); }

/* ===== 全文搜索结果 ===== */
.ft-results { max-width: 860px; }
.ft-item {
  background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; padding: 14px 18px; margin-bottom: 10px;
  cursor: pointer; transition: all .18s;
}
.ft-item:hover {
  border-color: var(--asc-primary); transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(124, 92, 252, .08);
}
.ft-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.ft-title { font-size: 13px; font-weight: 600; }
.ft-page { font-size: 11px; color: var(--asc-primary); font-weight: 500; background: var(--asc-primary-soft); border-radius: 4px; padding: 1px 7px; }
.ft-snippet { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.7; }
.ft-snippet :deep(mark) { background: rgba(124, 92, 252, .22); border-radius: 2px; padding: 0 1px; }
</style>

<style>
/* 导入本地弹层：收紧内边距（非 scoped，作用于 popper） */
.import-popper.el-popover { padding: 8px !important; border-radius: 12px !important; }
</style>
