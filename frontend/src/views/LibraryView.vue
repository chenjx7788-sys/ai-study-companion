<template>
  <div class="page library"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="onDrop">
    <!-- 整页拖拽上传遮罩 -->
    <div v-if="dragging" class="drop-mask">
      <div class="drop-tip">
        <el-icon class="drop-tip-icon"><Upload /></el-icon>
        松开鼠标上传材料（PDF / PPT / Word / Markdown / 图片 / 音视频，≤100MB）
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
        <el-upload :show-file-list="false" :http-request="onUpload" accept=".pdf,.ppt,.pptx,.doc,.docx,.md,.jpg,.jpeg,.png,.webp,.bmp,.mp3,.wav,.m4a,.mp4" multiple>
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

    <!-- 标签筛选 + 视图切换 -->
    <div class="filter-bar">
      <div class="tag-bar">
        <template v-if="allTags.length">
          <span class="tag-chip" :class="{ on: !activeTag }" @click="activeTag = ''">全部</span>
          <span v-for="tag in allTags" :key="tag" class="tag-chip"
            :class="{ on: activeTag === tag }" @click="activeTag = tag">{{ tag }}</span>
        </template>
        <span v-else class="tag-bar-empty">还没有标签</span>
        <span class="tag-add" @click="addPresetTag">
          <el-icon class="tag-add-icon"><Plus /></el-icon>新增标签
        </span>
      </div>
      <el-radio-group v-model="viewMode" size="small" class="view-toggle">
        <el-radio-button value="flat">平铺</el-radio-button>
        <el-radio-button value="folder">文件夹</el-radio-button>
      </el-radio-group>
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
              <span v-if="m.storage_mode === 'reference'" class="ref-badge">引用</span>
              <span v-if="m.page_count">{{ m.page_count }} 页</span>
              <span>{{ formatTime(m.created_at) }}</span>
            </div>
          </div>
          <div class="card-status" @click.stop>
            <el-tag v-if="m.parsed_status === 'parsing'" size="small" effect="light" round>
              <el-icon class="is-loading"><Loading /></el-icon>
              {{ isSlowFmt(m.format) ? `${slowLabel(m.format)} ${m.parse_progress || 0}%` : '解析中' }}
            </el-tag>
            <el-tag v-else-if="m.parsed_status === 'failed'" type="danger" size="small" effect="light" round :title="m.parse_error">解析失败</el-tag>
            <el-tag v-else-if="m.parsed_status === 'scanned'" type="warning" size="small" effect="light" round :title="m.parse_error">扫描件</el-tag>
            <el-tag v-else-if="m.has_ai" type="success" size="small" effect="light" round>已总结</el-tag>
          </div>
        </div>
        <div class="card-body">
          <div class="card-tags-row">
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
import { Loading, ArrowDown, Folder, FolderAdd, Plus, Search, Upload, Document, Picture, Headset, VideoPlay } from '@element-plus/icons-vue'
import { materialApi, foldersApi, settingsApi } from '../api'
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

function openFolder(id) { activeFolderId.value = id }
function goRoot() { activeFolderId.value = null }
watch(viewMode, () => { activeFolderId.value = null })

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
const fmtLabel = (f) => ({ pdf: 'PDF', ppt: 'PPT', pptx: 'PPT', doc: 'WORD', docx: 'WORD', md: 'MD', markdown: 'MD', mp3: '音频', wav: '音频', m4a: '音频', mp4: '视频', jpg: '图片', jpeg: '图片', png: '图片', webp: '图片', bmp: '图片' }[f] || (f || '').toUpperCase())

// 格式 → 图标分组（决定图标与徽章配色）
const fmtGroup = (f) => {
  if (isImageFmt(f)) return 'img'
  if (isMediaFmt(f)) return f === 'mp4' ? 'video' : 'audio'
  if (['ppt', 'pptx'].includes(f)) return 'ppt'
  if (['doc', 'docx'].includes(f)) return 'doc'
  if (['md', 'markdown'].includes(f)) return 'md'
  return 'pdf'
}
const fmtIcon = (f) => ({ img: Picture, audio: Headset, video: VideoPlay }[fmtGroup(f)] || Document)

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
  if (!['pdf', 'ppt', 'pptx', 'doc', 'docx', 'md', 'markdown', 'jpg', 'jpeg', 'png', 'webp', 'bmp', 'mp3', 'wav', 'm4a', 'mp4'].includes(ext)) {
    ElMessage.warning(`「${file.name}」格式不支持（PDF/PPT/Word/Markdown/图片/音视频）`)
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

/* ===== 筛选条（标签 + 视图切换） ===== */
.filter-bar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px 16px; flex-wrap: wrap; margin-bottom: 18px;
}
.tag-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 28px; }
.tag-bar-empty { font-size: 12px; color: var(--asc-text-3); margin-right: 4px; }
.tag-chip {
  font-size: 12px; padding: 4px 12px; border-radius: 999px; cursor: pointer;
  background: var(--asc-card); border: 1px solid var(--asc-border); color: var(--asc-text-2);
  transition: all .15s;
}
.tag-chip:hover { border-color: var(--asc-primary); color: var(--asc-primary); }
.tag-chip.on { background: var(--asc-primary); border-color: var(--asc-primary); color: #fff; }
.tag-add {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 12px; color: var(--asc-primary); cursor: pointer;
  padding: 4px 10px; border: 1px dashed var(--asc-primary);
  border-radius: 999px; transition: all .15s;
}
.tag-add:hover { background: var(--asc-primary-soft); }
.tag-add-icon { font-size: 12px; }
.view-toggle { flex-shrink: 0; }

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
.card-status { flex-shrink: 0; }
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
.fmt-name { font-weight: 600; font-size: 11px; letter-spacing: .3px; }
.ftn-pdf { color: #d85a30; } .ftn-ppt { color: #ba7517; } .ftn-doc { color: #378add; }
.ftn-md { color: #64748b; } .ftn-audio { color: #0f9488; } .ftn-video { color: #7c5cfc; }
.ftn-img { color: #0891b2; }
.ref-badge {
  font-size: 11px; color: var(--asc-text-3); background: var(--asc-surface-2);
  border-radius: 4px; padding: 0 6px; line-height: 16px;
  display: inline-block; vertical-align: middle;
}

.card-body { padding: 10px 16px 12px; display: flex; flex-direction: column; flex: 1; }
.card-tags-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; min-height: 22px; margin-bottom: 8px;
}
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
