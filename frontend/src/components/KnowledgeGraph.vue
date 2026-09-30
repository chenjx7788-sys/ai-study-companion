<template>
  <div class="kg-wrap">
    <!-- 空态：未抽到实体 -->
    <div v-if="!loading && allEntities.length === 0" class="kg-empty">
      <img :src="mascot" class="kg-empty-img" alt="" />
      <p>还没有生成知识图谱</p>
      <p class="kg-empty-sub">到「知识库 → 按材料」对需要的材料点「生成图谱」，即可按需抽取实体与关系（会调用大模型，消耗少量 token）。</p>
    </div>

    <div v-else class="kg-layout">
      <!-- 左侧材料列表 -->
      <div class="kg-mat-list">
        <div class="kg-mat-list-title">材料 · 知识视图</div>
        <button type="button" class="kg-mat-item kg-mat-global" :class="{ active: curMaterial === 'all' }"
          @click="selectMaterial('all')">
          <span class="t">全局视图</span>
          <span class="c">全部材料</span>
        </button>
        <button v-for="m in matList" :key="m.id" type="button" class="kg-mat-item"
          :class="{ active: curMaterial === m.id }" @click="selectMaterial(m.id)">
          <span class="t" :title="m.title">{{ m.title }}</span>
          <span class="c">
            {{ m.count }} 个实体
            <i class="kg-mat-del" title="删除该材料的知识图谱（可之后重新生成）"
              @click.stop="confirmDelete(m)">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
            </i>
          </span>
        </button>
      </div>

      <!-- 右侧图谱 -->
      <div class="kg-graph-col">
        <!-- 吸顶头：工具栏 + 统计行（滚动看大图时搜索/开关/图例/统计始终可见） -->
        <div class="kg-head">
        <div class="kg-toolbar">
          <div class="kg-search">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
            <input v-model="search" placeholder="搜索当前视图的实体…" autocomplete="off" @input="onSearch" @focus="onSearch" />
            <div v-if="searchHits.length" class="kg-search-hit">
              <button v-for="e in searchHits" :key="e.name" type="button" @click="gotoEntity(e.name)">
                <i :style="{ background: typeColor(e.type) }"></i>
                <span>{{ e.name }}</span>
                <em>{{ typeLabel(e.type) }}</em>
              </button>
            </div>
          </div>
          <label class="kg-toggle">
            <input type="checkbox" v-model="showAll" @change="onToggleShowAll" /> 显示全部实体
          </label>
          <div class="kg-legend">
            <span v-for="t in TYPE_LIST" :key="t.key" class="kg-lg">
              <i :style="{ background: t.color }"></i>{{ t.label }}
            </span>
          </div>
        </div>

        <div class="kg-meta-bar">
          {{ scopeLabel }}显示 <b>{{ nodes.length }}</b> 个核心实体 · <b>{{ edges.length }}</b> 条关系
          <span v-if="collapsedCount > 0">（共 {{ currentEnts.length }} 实体，已折叠 {{ collapsedCount }} 个低频）</span>
        </div>
        </div>

        <div v-if="!loading && nodes.length" class="kg-canvas">
          <svg :viewBox="`0 0 ${W} ${H}`" class="kg-svg" role="img"
            :aria-label="'知识图谱：' + currentEnts.length + ' 个实体'">
            <!-- 连线 -->
            <g v-for="e in edges" :key="e.key">
              <line :x1="e.a.x" :y1="e.a.y" :x2="e.b.x" :y2="e.b.y"
                class="kg-edge" :class="{ hl: isHl(e), dim: hasFocus && !isHl(e) }" />
              <text v-if="isHl(e)" :x="(e.a.x + e.b.x) / 2" :y="(e.a.y + e.b.y) / 2 - 4"
                class="kg-edge-label" text-anchor="middle">{{ e.rel }}</text>
            </g>
            <!-- 节点 -->
            <g v-for="n in nodes" :key="n.name" class="kg-node"
              :class="{ focused: focused === n.name, dim: hasFocus && focused !== n.name && !isNeighbor(n.name) }"
              :transform="`translate(${n.x},${n.y})`" @click="focusNode(n.name)">
              <circle :r="nodeR(n)" :fill="typeColor(n.type)"
                :stroke="focused === n.name ? '#7c5cfc' : 'rgba(0,0,0,.06)'"
                :stroke-width="focused === n.name ? 2.5 : 1" />
              <text v-if="showLabel(n)" :y="nodeR(n) + 14" class="kg-node-label" text-anchor="middle">{{ n.name }}</text>
              <text v-else :y="nodeR(n) + 14" class="kg-node-label kg-node-hover-label" text-anchor="middle">{{ n.name }}</text>
            </g>
          </svg>
        </div>

        <div v-if="!loading && currentEnts.length && !nodes.length" class="kg-empty">
          <p>该视图暂无可连接的实体</p>
        </div>
      </div>
    </div>

    <!-- 聚焦实体侧栏 -->
    <transition name="kg-slide">
      <div v-if="focusInfo" class="kg-panel">
        <div class="kg-panel-head">
          <div class="kg-panel-name" :title="focusInfo.name">{{ focusInfo.name }}</div>
          <button type="button" class="kg-panel-close" aria-label="关闭" @click="focused = ''">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
        <div class="kg-panel-type">
          <i :style="{ background: typeColor(focusInfo.type) }"></i>
          {{ typeLabel(focusInfo.type) }} · 关联 {{ focusInfo.degree }} 个实体
        </div>
        <div v-if="focusInfo.materials.length" class="kg-panel-sec">
          <div class="kg-panel-sec-title">来自材料</div>
          <div v-for="t in focusInfo.materials" :key="t" class="kg-panel-mat">{{ t }}</div>
        </div>
        <div v-if="focusInfo.neighbors.length" class="kg-panel-sec">
          <div class="kg-panel-sec-title">关联实体（{{ focusInfo.neighbors.length }}）</div>
          <button v-for="nb in focusInfo.neighbors" :key="nb.name" type="button" class="kg-panel-nb"
            @click="focusNode(nb.name)">
            <i :style="{ background: typeColor(nb.type) }"></i>
            <span>{{ nb.name }}</span>
            <em>{{ nb.rel }}</em>
          </button>
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { kbApi } from '../api'
import mascot from '../assets/mascot.png'

// 外部指定要聚焦的材料（知识库「按材料」tab 点「图谱 · N」跳转过来时传入）
const props = defineProps({
  focusMaterial: { type: [Number, String], default: null },
})

// 5 类实体配色（语义色固定，不随主题变化）
const TYPE_MAP = {
  person: { label: '人物', color: '#7c5cfc' },
  product: { label: '产品', color: '#0891b2' },
  concept: { label: '概念', color: '#d97706' },
  knowledge: { label: '知识点', color: '#0f9488' },
  method: { label: '方法', color: '#db2777' },
}
const TYPE_LIST = Object.entries(TYPE_MAP).map(([k, v]) => ({ key: k, ...v }))
const typeColor = (t) => (TYPE_MAP[t] || TYPE_MAP.concept).color
const typeLabel = (t) => (TYPE_MAP[t] || TYPE_MAP.concept).label

const W = 880
const H = 620
const loading = ref(false)
const allEntities = ref([])      // 全量实体（含所有材料）
const allRelations = ref([])     // 全量关系
const materials = ref({})        // {id: title}
const curMaterial = ref('all')   // 当前选中的材料（'all' 或材料 id）
const showAll = ref(false)       // 显示全部实体（关闭核心裁剪）
const search = ref('')
const searchHits = ref([])
const focused = ref('')

// 力导向的节点坐标（reactive，rAF 每帧改 x/y 触发 SVG 重渲染）
const nodes = reactive([])
const degree = ref({})           // 实体关联度（全库算）
let edgesArr = []                // 边数组（内部，渲染时取坐标）
const edges = computed(() => edgesArr)

// 当前视图（按材料隔离）
const currentEnts = ref([])
const currentRels = ref([])

onMounted(() => { load() })

async function load() {
  loading.value = true
  try {
    const { data } = await kbApi.entities()
    allEntities.value = data.entities || []
    allRelations.value = data.relations || []
    materials.value = data.materials || {}
  } catch {
    allEntities.value = []
    allRelations.value = []
    materials.value = {}
  } finally {
    loading.value = false
    computeDegree()
    if (allEntities.value.length) {
      // 外部指定了聚焦材料（从「按材料」tab 点「图谱 · N」跳入）→ 直接选中该材料子图
      const target = props.focusMaterial
      const hasTarget = target && allEntities.value.some(e => (e.material_ids || []).includes(Number(target)))
      selectMaterial(hasTarget ? String(target) : 'all')
    }
  }
}

// 数据已加载后，外部再改变聚焦材料（组件已挂载的二次跳转）→ 跟随切换
watch(() => props.focusMaterial, (id) => {
  if (!id || !allEntities.value.length) return
  const has = allEntities.value.some(e => (e.material_ids || []).includes(Number(id)))
  if (has) selectMaterial(String(id))
})

// 计算全库 degree（用于节点大小与裁剪阈值）
function computeDegree() {
  const d = {}
  allEntities.value.forEach(e => d[e.name] = 0)
  allRelations.value.forEach(r => { d[r.src] = (d[r.src] || 0) + 1; d[r.dst] = (d[r.dst] || 0) + 1 })
  degree.value = d
}

// 左侧材料列表（含实体数统计）
const matList = computed(() => {
  return Object.entries(materials.value).map(([id, title]) => {
    const mid = Number(id)
    const count = allEntities.value.filter(e => (e.material_ids || []).includes(mid)).length
    return { id, title, count }
  })
})

const scopeLabel = computed(() => (curMaterial.value === 'all' ? '全局' : '本材料'))

// 按材料隔离：选中材料 → 过滤该材料的实体与关系
function selectMaterial(id) {
  curMaterial.value = id
  focused.value = ''
  search.value = ''
  searchHits.value = []
  if (id === 'all') {
    currentEnts.value = allEntities.value
    currentRels.value = allRelations.value
  } else {
    const mid = Number(id)
    currentEnts.value = allEntities.value.filter(e => (e.material_ids || []).includes(mid))
    // ⚠️ 关系按 material_ids 精确隔离，不能靠「两端实体名都在当前集内」近似过滤：
    // 后端把跨材料同名实体合并了（material_ids 含多个材料），同名实体在 A、B 都出现，
    // 若按实体名过滤，选 A 会把 B 里这些同名实体参与的关系误归入 A（看 A 显 B）。
    currentRels.value = allRelations.value.filter(r => (r.material_ids || []).includes(mid))
  }
  layoutAndRender()
}

// 核心裁剪阈值：全局视图更严（degree≥5，只显示主干，防卡顿）；
// 材料视图宽松（degree≥3，材料内量级小）。
// 依据实测：当前库 degree≥5 ≈ 25 个核心实体，全量 backfill 后（15 份）量级仍可控。
const GLOBAL_CORE_DEGREE = 5
const MATERIAL_CORE_DEGREE = 3
function coreEntities() {
  if (showAll.value) return currentEnts.value
  const th = curMaterial.value === 'all' ? GLOBAL_CORE_DEGREE : MATERIAL_CORE_DEGREE
  return currentEnts.value.filter(e => (degree.value[e.name] || 0) >= th)
}

const collapsedCount = computed(() => currentEnts.value.length - nodes.length)

// ---------- 力导向模拟 ----------
let rafId = null

function layoutAndRender() {
  stopSimulation()
  const core = coreEntities()
  nodes.length = 0
  core.forEach(e => {
    nodes.push({
      name: e.name, type: e.type,
      x: W / 2 + (Math.random() - 0.5) * 240,
      y: H / 2 + (Math.random() - 0.5) * 240,
      vx: 0, vy: 0,
    })
  })
  const byName = Object.fromEntries(nodes.map(n => [n.name, n]))
  const visibleNames = new Set(core.map(e => e.name))
  edgesArr = currentRels.value
    .filter(r => visibleNames.has(r.src) && visibleNames.has(r.dst))
    .map(r => ({ key: `${r.src}|${r.rel}|${r.dst}`, rel: r.rel, src: r.src, dst: r.dst, a: byName[r.src], b: byName[r.dst] }))

  const total = nodes.length
  const maxIter = Math.min(400, 120 + total * 5)
  let iter = 0

  function step() {
    const ns = nodes
    // 斥力
    for (let i = 0; i < ns.length; i++) {
      for (let j = i + 1; j < ns.length; j++) {
        const a = ns[i], b = ns[j]
        let dx = a.x - b.x, dy = a.y - b.y
        let d2 = dx * dx + dy * dy; if (d2 < 1) d2 = 1
        const f = 1500 / d2
        const d = Math.sqrt(d2)
        const fx = f * dx / d, fy = f * dy / d
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy
      }
    }
    // 引力（沿边）
    for (const e of edgesArr) {
      const a = e.a, b = e.b
      if (!a || !b) continue
      const dx = b.x - a.x, dy = b.y - a.y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const f = 0.013 * d
      a.vx += dx / d * f; a.vy += dy / d * f
      b.vx -= dx / d * f; b.vy -= dy / d * f
    }
    // 中心引力 + 积分
    for (const n of ns) {
      n.vx += (W / 2 - n.x) * 0.004
      n.vy += (H / 2 - n.y) * 0.004
      n.vx *= 0.85; n.vy *= 0.85
      n.x += n.vx; n.y += n.vy
      if (n.x < 60) n.x = 60; else if (n.x > W - 110) n.x = W - 110
      if (n.y < 40) n.y = 40; else if (n.y > H - 34) n.y = H - 34
    }
    iter++
    if (iter < maxIter) rafId = requestAnimationFrame(step)
    else rafId = null
  }
  rafId = requestAnimationFrame(step)
}

function stopSimulation() {
  if (rafId) { cancelAnimationFrame(rafId); rafId = null }
}
onBeforeUnmount(stopSimulation)

// ---------- 渲染辅助 ----------
function nodeR(n) {
  const c = degree.value[n.name] || 0
  return Math.min(15, 6 + Math.sqrt(c) * 2.6)
}

// 标签分层：degree>=4 的大节点默认显示，小节点 hover/focus 才显示。
// 节点过多（>200，全局视图勾选「显示全部」时）一律不默认显示标签，避免文字挤成一团。
const LABEL_SHOW_DEGREE = 4
const LABEL_MAX_NODES = 200
function showLabel(n) {
  if (nodes.length > LABEL_MAX_NODES) return n.name === focused.value
  const c = degree.value[n.name] || 0
  return c >= LABEL_SHOW_DEGREE || n.name === focused.value
}

// 「显示全部」开关：全局视图勾选时给性能提示（节点多可能卡顿）
function onToggleShowAll() {
  if (showAll.value && curMaterial.value === 'all') {
    ElMessage({ message: `将显示全部 ${currentEnts.value.length} 个实体，节点较多时可能卡顿`, type: 'warning', duration: 2500 })
  }
  layoutAndRender()
}

const hasFocus = computed(() => !!focused.value)

function isHl(e) {
  if (!focused.value) return false
  return e.src === focused.value || e.dst === focused.value
}

function isNeighbor(name) {
  return edgesArr.some(e => (e.src === focused.value && e.dst === name) || (e.dst === focused.value && e.src === name))
}

function focusNode(name) {
  focused.value = focused.value === name ? '' : name
}

// ---------- 搜索定位 ----------
function onSearch() {
  const q = search.value.trim()
  if (!q) { searchHits.value = []; return }
  searchHits.value = currentEnts.value.filter(e => e.name.includes(q)).slice(0, 8)
}

function gotoEntity(name) {
  search.value = name
  searchHits.value = []
  // 若该实体被裁剪折叠，先取消裁剪重新布局
  const visible = nodes.some(n => n.name === name)
  if (!visible) {
    showAll.value = true
    layoutAndRender()
    setTimeout(() => { focused.value = name }, 300)
  } else {
    focused.value = name
  }
}

// ---------- 聚焦详情 ----------
const focusInfo = computed(() => {
  const name = focused.value
  if (!name) return null
  const e = allEntities.value.find(x => x.name === name)
  if (!e) return null
  const mats = (e.material_ids || []).map(id => materials.value[id] || `#${id}`)
  const neighbors = []
  for (const r of currentRels.value) {
    if (r.src === name) neighbors.push({ name: r.dst, rel: r.rel, type: typeOf(r.dst) })
    else if (r.dst === name) neighbors.push({ name: r.src, rel: '← ' + r.rel, type: typeOf(r.src) })
  }
  return { name: e.name, type: e.type, degree: degree.value[name] || 0, materials: mats, neighbors }
})

function typeOf(name) {
  const e = allEntities.value.find(x => x.name === name)
  return e ? e.type : 'concept'
}

// ---------- 删除指定材料的图谱 ----------
async function confirmDelete(m) {
  try {
    await ElMessageBox.confirm(
      `将删除「${m.title}」的知识图谱（${m.count} 个实体及其关系）。删除后可随时在「知识库 → 按材料」重新生成。`,
      '删除知识图谱',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return   // 用户取消
  }
  try {
    await kbApi.deleteEntities(m.id)
    ElMessage.success('已删除该材料的知识图谱')
    // 若当前正看着被删材料，切回全局
    if (curMaterial.value === m.id) curMaterial.value = 'all'
    focused.value = ''
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}
</script>

<style scoped>
.kg-wrap { position: relative; }
.kg-empty { text-align: center; padding: 56px 20px; }
.kg-empty-img { width: 108px; height: 108px; border-radius: 18px; margin-bottom: 16px; }
.kg-empty p { margin: 0; font-size: 15px; font-weight: 600; color: var(--asc-text); }
.kg-empty .kg-empty-sub { margin-top: 6px; font-size: 12.5px; color: var(--asc-text-3); line-height: 1.7; }

/* 左侧材料列表 + 右侧图谱布局 */
.kg-layout { display: flex; gap: 18px; align-items: stretch; }
.kg-mat-list {
  width: 240px; flex-shrink: 0; background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 16px; padding: 12px; align-self: flex-start; max-height: 78vh; overflow-y: auto;
  /* ⚠️ 吸顶：与右侧 .kg-head 同一套策略。不加这句的话页面一滚，材料列表就跟着上移，
     顶部（标题 + 靠前的材料项）会被吸顶的 tabs 导航条（z-index 6）压住 ——
     实测滚到底时标题被压掉 214×19、「全局视图」项被吃掉约 35px，点不到。
     top 48px = tabs 栏 40px + .kg-head 内边距 8px → 与右侧工具栏内容顶部对齐（未滚动时两者本来就对齐）；
     z-index 4 低于 .kg-head 的 5，避免两栏在极端情况下互相压住；
     max-height:78vh 已保证 48px + 列表高 ≤ 视口高（vh ≥ 218 即成立），不会顶出屏幕。 */
  position: sticky; top: 48px; z-index: 4;
}
.kg-mat-list-title { font-size: 12px; font-weight: 700; color: var(--asc-text-3); padding: 4px 8px 10px; letter-spacing: .4px; }
.kg-mat-item {
  display: flex; flex-direction: column; gap: 3px; width: 100%; text-align: left; font: inherit;
  padding: 10px 12px; border: none; background: none; border-radius: 10px; cursor: pointer;
  border: 1px solid transparent; transition: background .15s, border-color .15s;
}
.kg-mat-item:hover { background: var(--asc-surface-2); }
.kg-mat-item.active { background: var(--asc-primary-soft); border-color: rgba(124, 92, 252, .3); }
.kg-mat-item .t { font-size: 13px; font-weight: 600; color: var(--asc-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kg-mat-item.active .t { color: var(--asc-primary); }
.kg-mat-item .c { font-size: 11px; color: var(--asc-text-3); font-variant-numeric: tabular-nums; display: flex; align-items: center; justify-content: space-between; }
.kg-mat-del {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border-radius: 5px; color: var(--asc-text-3);
  opacity: 0; transition: opacity .14s, background .14s, color .14s; cursor: pointer;
}
.kg-mat-item:hover .kg-mat-del { opacity: 1; }
.kg-mat-del:hover { background: rgba(210, 78, 78, .12); color: #d24e4e; }
.kg-mat-item.kg-mat-global { border-bottom: 1px solid var(--asc-border); margin-bottom: 6px; padding-bottom: 12px; border-radius: 0; }
.kg-mat-item.kg-mat-global .t { color: var(--asc-text-2); }

/* 右侧图谱区 */
.kg-graph-col { flex: 1; min-width: 0; }
/* 吸顶头（工具栏 + 统计行）：滚动看大图时吸在知识库 tabs 栏下方 */
.kg-head {
  position: sticky; top: 40px; z-index: 5;
  background: var(--asc-bg);
  /* 上下各留一点内边距，吸顶时背景完整盖住滚动上来的内容，避免露缝 */
  margin: -8px -8px 12px; padding: 8px 8px 0;
}
.kg-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.kg-search { position: relative; flex: 1; min-width: 180px; max-width: 300px; }
.kg-search input {
  width: 100%; font: inherit; font-size: 13px; padding: 8px 12px 8px 32px;
  background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 999px; outline: none; color: var(--asc-text);
}
.kg-search input:focus { border-color: rgba(124, 92, 252, .5); box-shadow: 0 0 0 3px rgba(124, 92, 252, .12); }
.kg-search svg { position: absolute; left: 11px; top: 50%; transform: translateY(-50%); color: var(--asc-text-3); }
.kg-search-hit {
  position: absolute; top: 40px; left: 0; right: 0; background: var(--asc-card); border: 1px solid var(--asc-border);
  border-radius: 12px; box-shadow: 0 8px 28px rgba(31, 24, 68, .12); max-height: 240px; overflow-y: auto; z-index: 10;
}
.kg-search-hit button { display: flex; align-items: center; gap: 8px; width: 100%; font: inherit; font-size: 12.5px; padding: 8px 12px; border: none; background: none; text-align: left; cursor: pointer; color: var(--asc-text); }
.kg-search-hit button:hover { background: var(--asc-primary-soft); }
.kg-search-hit button i { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.kg-search-hit button em { font-style: normal; font-size: 11px; color: var(--asc-text-3); margin-left: auto; }
.kg-toggle { display: inline-flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--asc-text-2); cursor: pointer; user-select: none; }
.kg-toggle input { accent-color: var(--asc-primary); width: 15px; height: 15px; cursor: pointer; }
.kg-legend { display: flex; gap: 13px; flex-wrap: wrap; margin-left: auto; }
.kg-lg { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--asc-text-2); }
.kg-lg i { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }

.kg-meta-bar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 12px; color: var(--asc-text-3); }
.kg-meta-bar b { color: var(--asc-text-2); font-weight: 600; }

.kg-canvas { position: relative; background: var(--asc-card); border: 1px solid var(--asc-border); border-radius: 16px; overflow: hidden; box-shadow: 0 6px 24px rgba(31, 24, 68, .05); }
.kg-svg { display: block; width: 100%; height: auto; }
.kg-edge { stroke: var(--asc-border-strong, #ddd9ec); stroke-width: 1.2; transition: stroke .15s; }
.kg-edge.hl { stroke: #7c5cfc; stroke-width: 2; }
.kg-edge.dim { opacity: .18; }
.kg-edge-label { font-size: 9.5px; fill: var(--asc-text-3); paint-order: stroke; stroke: var(--asc-card); stroke-width: 3px; pointer-events: none; }
.kg-node { cursor: pointer; }
.kg-node circle { transition: stroke-width .12s, stroke .12s; }
.kg-node:hover circle, .kg-node.focused circle { stroke: #7c5cfc; stroke-width: 2.5; }
.kg-node.dim { opacity: .12; }
.kg-node-label { font-size: 11px; fill: var(--asc-text); font-weight: 500; paint-order: stroke; stroke: var(--asc-card); stroke-width: 4px; pointer-events: none; user-select: none; }
.kg-node-hover-label { opacity: 0; }
.kg-node:hover .kg-node-hover-label { opacity: 1; }
.kg-node.focused .kg-node-label { font-weight: 700; fill: var(--asc-primary); }

/* 聚焦侧栏 */
.kg-panel {
  position: fixed; right: 0; top: 0; width: 300px; height: 100vh;
  background: var(--asc-card); border-left: 1px solid var(--asc-border);
  box-shadow: -12px 0 32px rgba(31, 24, 68, .10); z-index: 30;
  padding: 20px 18px; box-sizing: border-box; overflow-y: auto;
}
.kg-panel-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.kg-panel-name { flex: 1; font-size: 16px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kg-panel-close {
  flex-shrink: 0; width: 28px; height: 28px; border-radius: 8px; border: none; background: transparent;
  color: var(--asc-text-2); cursor: pointer; display: inline-flex; align-items: center; justify-content: center;
}
.kg-panel-close:hover { background: var(--asc-surface-2); color: var(--asc-text); }
.kg-panel-type { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--asc-text-2); margin-bottom: 16px; }
.kg-panel-type i { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.kg-panel-sec { margin-bottom: 18px; }
.kg-panel-sec-title { font-size: 11px; font-weight: 600; color: var(--asc-text-3); margin-bottom: 8px; letter-spacing: .3px; }
.kg-panel-mat { font-size: 12.5px; color: var(--asc-text-2); line-height: 1.6; padding: 5px 0; border-bottom: 1px dashed var(--asc-divider); }
.kg-panel-mat:last-child { border-bottom: none; }
.kg-panel-nb {
  display: flex; align-items: center; gap: 7px; width: 100%;
  font: inherit; font-size: 12.5px; color: var(--asc-text); text-align: left;
  background: var(--asc-surface-2); border: 1px solid var(--asc-border);
  border-radius: 10px; padding: 8px 10px; margin-bottom: 6px; cursor: pointer;
  transition: border-color .14s ease;
}
.kg-panel-nb:hover { border-color: rgba(124, 92, 252, .4); }
.kg-panel-nb i { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.kg-panel-nb span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kg-panel-nb em { font-style: normal; font-size: 11px; color: var(--asc-text-3); flex-shrink: 0; }

.kg-slide-enter-active, .kg-slide-leave-active { transition: transform .22s ease, opacity .22s ease; }
.kg-slide-enter-from, .kg-slide-leave-to { transform: translateX(100%); opacity: 0; }

@media (prefers-reduced-motion: reduce) {
  .kg-node circle { transition: none; }
}
</style>
