<template>
  <div class="skin-picker">
    <div v-for="grp in groups" :key="grp.key" class="skin-group">
      <div class="skin-group-label">{{ grp.label }}</div>
      <div class="skin-grid">
        <button
          v-for="s in grp.skins"
          :key="s.key"
          type="button"
          class="skin-card"
          :class="{ active: modelValue === s.key, blocked: !okOf(s.key) }"
          :disabled="!okOf(s.key)"
          :title="reasonOf(s.key) || s.desc"
          @click="pickSkin(s.key)"
        >
          <!-- 效果示例图：后端现渲（跟模板走，改版自动重建），拿不到就整块不渲染。
               ⚠️ 不用占位图充数 —— 那等于告诉用户一个假的效果。 -->
          <img v-if="shotOf(s.key)" class="skin-shot" :src="shotOf(s.key)"
               :alt="`${s.label} 效果示例`" loading="lazy" @error="onShotError(s.key)" />
          <div class="skin-head">
            <span class="skin-name">{{ s.label }}</span>
            <span v-if="!okOf(s.key)" class="skin-tag blocked-tag">不可用</span>
          </div>
          <p class="skin-desc">{{ s.desc }}</p>
          <p class="skin-need" :class="{ bad: !okOf(s.key) }">
            {{ reasonOf(s.key) || s.need }}
          </p>
        </button>
      </div>
    </div>

    <!-- 电台浅色/深色的预设风格（各 3 套，空 key = 默认套）：选中这两个皮肤时出现 -->
    <!-- ⚠️⚠️ 必须带**独占**的 `variant-row`：下面那行（内容层）也用 `inner-row`，
         两者共用 class 会让「按 class 找行」的断言/探针张冠李戴
         （2026-10-13 实测：relax_ui 探针的「选电台后无内容层行」因此假红）。 -->
    <div v-if="variantOptions.length" class="inner-row variant-row">
      <span class="inner-label">风格</span>
      <div class="seg">
        <button
          v-for="vt in variantOptions"
          :key="vt.key"
          type="button"
          class="seg-btn variant-btn"
          :class="{ active: (skinVariant || '') === vt.key }"
          @click="$emit('update:skinVariant', vt.key)"
        >
          <span class="variant-dot" :style="{ background: vt.swatch, borderColor: vt.ink }"></span>
          {{ vt.label }}
        </button>
      </div>
      <span class="inner-hint">同一套排版，三套配色{{ modelValue === 'night' ? '与夜景插画' : '' }}，按需挑一个</span>
    </div>

    <div v-if="needsInner" class="inner-row">
      <span class="inner-label">{{ innerLabel }}</span>
      <div class="seg">
        <button
          v-for="i in innerOptions"
          :key="i.key"
          type="button"
          class="seg-btn"
          :class="{ active: innerSkin === i.key }"
          :disabled="!innerOk(i.key)"
          :title="innerReason(i.key)"
          @click="$emit('update:innerSkin', i.key)"
        >
          {{ i.label }}
        </button>
      </div>
      <span v-if="!innerSkin" class="inner-hint">{{ innerHint }}</span>
      <template v-else-if="!innerOk(innerSkin)">
        <span class="inner-hint bad">{{ innerReason(innerSkin) }}</span>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

// 视觉方案选择（PRD 3.2.5）。
//
// ⚠️ 可用性来自**后端预检**（`GET /videos/{id}/precheck`），不是前端凭格式猜的：
//    判据涉及「材料有没有文字层 / 有没有可识别的页图」，只有后端真打开文件才知道。
// ⚠️ 不可用的方案必须置灰**并写出原因** —— 给一个注定做不出来的选项，
//    用户要等到生成失败才被告知，那是更差的体验（PRD 3.2.7 的要求）。
// ⚠️ 预检结果**拿不到**时（作品还没创建 / 预检请求失败）一律放行：
//    「查不到」不等于「不可用」，锁死界面比放行更糟。
const props = defineProps({
  modelValue: { type: String, default: 'source' },
  innerSkin: { type: String, default: '' },
  // 皮肤预设变体（radio/night 各 3 套）：当前值（'' = 默认套）+ 全量元数据（后端下发）
  skinVariant: { type: String, default: '' },
  variants: { type: Object, default: () => ({}) },
  skins: { type: Array, default: () => [] },
  innerSkins: { type: Array, default: () => [] },
  // ambient 的「内容层」白名单（含 none 纯氛围；与口播的 innerSkins 是两套，绝不可混）
  ambientInnerSkins: { type: Array, default: () => [] },
  // 后端 /precheck 返回的 skins 数组（每项 {key, ok, reason, ...}）；null = 没有预检结果
  precheck: { type: Array, default: null },
  // 各方案的**效果示例图**（后端 /options 下发，每项 {skin, label, source, url}）。
  // ⚠️ 拿不到（后端还在渲染 / 环境没有 Chromium 且无兜底图）时整块不渲染 ——
  //    不留破图框，也不拿占位图充数。
  previews: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'update:innerSkin', 'update:skinVariant'])

const checked = computed(() => Array.isArray(props.precheck) && props.precheck.length > 0)

// 皮肤分组（信息型 / 解压型）：按 options 里的 group 字段分，未标 group 的归 info。
const GROUP_DEFS = [
  { key: 'info', label: '信息型 · 要读画面' },
  { key: 'relax', label: '解压型 · 边听边看' },
]
const groups = computed(() => GROUP_DEFS
  .map((g) => ({ ...g, skins: (props.skins || []).filter((s) => (s.group || 'info') === g.key) }))
  .filter((g) => g.skins.length > 0))

// 示例图：skin → url。加载失败的那一张记进 failedShots，`shotOf` 直接返回空 →
// `<img>` 整块消失（与上传形象缩略图同一纪律：不留破图框，也不阻塞卡片本身）。
const failedShots = ref(new Set())
const shots = computed(() => {
  const m = {}
  for (const p of props.previews || []) if (p && p.skin && p.url) m[p.skin] = p.url
  return m
})
function shotOf(key) {
  if (failedShots.value.has(key)) return ''
  return shots.value[key] || ''
}
function onShotError(key) {
  const next = new Set(failedShots.value)
  next.add(key)
  failedShots.value = next
}

const isAmbient = computed(() => props.modelValue === 'ambient')
// 当前皮肤的预设变体（radio/night 才有；其余皮肤返回空数组 → 变体行不渲染）
const variantOptions = computed(() => props.variants[props.modelValue] || [])
// 需要内容层的皮肤：talking_head（必需）+ ambient（可选叠加）。
const needsInner = computed(() => props.modelValue === 'talking_head' || isAmbient.value)
const innerOptions = computed(() => (isAmbient.value ? props.ambientInnerSkins : props.innerSkins))
const innerLabel = computed(() => (isAmbient.value ? '内容层（可选叠加）' : '内容层'))
const innerHint = computed(() => (isAmbient.value
  ? '氛围默认纯光晕；叠加「材料原件」可低刺激看原文'
  : '数字人口播需要再选一个内容层'))

function item(key) {
  return (props.precheck || []).find((x) => x && x.key === key) || null
}
function okOf(key) {
  const it = item(key)
  return it && checked.value ? !!it.ok : true
}
function reasonOf(key) {
  const it = item(key)
  return it && !it.ok ? it.reason : ''
}
// ⚠️ inner_ok 读**指定皮肤**的（talking_head 与 ambient 各自的白名单），不写死 ——
//    ambient 的内容层是 none/source，与口播的 source/graph 不同。
function innerOkFor(skin, key) {
  const cur = item(skin)
  const m = cur && cur.inner_ok
  if (!checked.value || !m) return true
  return key in m ? !!m[key] : true
}
function innerOk(key) {
  return innerOkFor(props.modelValue, key)
}
function innerReason(key) {
  if (innerOk(key)) return ''
  return isAmbient.value
    ? '这份来源做不了「材料原件」（没有可定位的原件页面）'
    : '这份来源做不了「材料原件」（没有可定位的原件页面），请选「结构图」'
}

// 切到「数字人口播」时**立刻给内容层一个可用值**。
//
// ⚠️ 口播层是**叠加层**：没有内容层就做不出画面（后端会把它兜底成结构图并落库）。
//    这里同步选中，是为了让用户**看得见**当前绑定的是哪个内容层 ——
//    面板空着却不说绑的是谁，用户会以为「还没选」，而后端实际按结构图走，两边对不上
//    （本项目既有约定：同一面板绑定已有对象时必须显示当前绑定的是谁）。
// ⚠️ 优先选**可用**的那个：材料原件可能被预检判为不可用，那就落到结构图。
function pickSkin(key) {
  emit('update:modelValue', key)
  // 换皮肤即把变体复位到默认套：旧皮肤的变体值（如 radio 的 mist）在新皮肤
  // （night）的白名单里不存在，留着会让界面显示一个后端根本不认的绑定。
  if (key !== props.modelValue) emit('update:skinVariant', '')
  // ⚠️ emit 更新 modelValue 是异步的（下一轮 props 才变），这里判断要用 innerOkFor(key,…)
  //    读**目标皮肤**的 inner_ok，不是 props.modelValue（那还是旧值）。
  if (key === 'ambient') {
    // ambient 的内容层可空（none 纯氛围是默认）：给确定的默认值，让界面看得出当前绑的是谁。
    const okKeys = (props.ambientInnerSkins || []).map((i) => i.key)
    const keep = props.innerSkin && okKeys.includes(props.innerSkin)
      && innerOkFor('ambient', props.innerSkin)
    emit('update:innerSkin', keep ? props.innerSkin : 'none')
    return
  }
  if (key !== 'talking_head') return
  if (props.innerSkin && innerOkFor('talking_head', props.innerSkin)) return
  const list = props.innerSkins || []
  const first = list.find((i) => i && innerOkFor('talking_head', i.key))
  emit('update:innerSkin', (first || list[0] || { key: 'graph' }).key)
}
</script>

<style scoped>
.skin-picker { display: flex; flex-direction: column; gap: 12px; }
.skin-group { display: flex; flex-direction: column; gap: 8px; }
.skin-group + .skin-group { margin-top: 8px; }
.skin-group-label { font-size: 12px; color: var(--asc-text-2); letter-spacing: .5px; }
/* ⚠️ 2 列是**量出来的**，不是拍的：卡片加了 16:9 示例图后，2 列（每列约 480px）下
   最相像的一对（结构图 ↔ 数字人口播）结构级差异 4.48/255 > 分辨阈值 3.0，目视也一眼分得开
   （实测见 `_fetch_probe/_skinprev_run5_20261012.txt`）。窄屏回落 1 列，避免图被压到看不清。 */
.skin-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 1180px) { .skin-grid { grid-template-columns: 1fr; } }
.skin-card {
  text-align: left; cursor: pointer;
  padding: 10px 12px; border-radius: 10px;
  background: var(--asc-card); border: 1px solid var(--asc-border);
  display: flex; flex-direction: column; gap: 5px;
  transition: all .15s ease;
}
.skin-card:hover:not(:disabled) { border-color: var(--asc-primary); box-shadow: var(--asc-shadow-hover); }
.skin-card.active { border-color: var(--asc-primary); background: var(--asc-primary-soft); box-shadow: 0 2px 10px -4px rgba(124, 92, 252, .35); }
.skin-card.blocked { cursor: not-allowed; opacity: .6; background: var(--asc-surface-2); }
/* 示例图：16:9 铺满卡片宽度，高度靠 aspect-ratio 撑（不需要额外容器） */
.skin-shot {
  width: 100%; aspect-ratio: 16 / 9; height: auto;
  display: block; object-fit: cover; border-radius: 8px;
  background: var(--asc-surface-2); border: 1px solid var(--asc-border);
}
.skin-head { display: flex; align-items: center; gap: 6px; }
.skin-name { font-size: 13.5px; font-weight: 600; color: var(--asc-text); }
.skin-tag {
  font-size: 11px; padding: 1px 6px; border-radius: 999px;
  background: var(--asc-surface-2); color: var(--asc-text-2);
}
.skin-tag.blocked-tag { background: #fdecec; color: #b6342c; }
.skin-desc { margin: 0; font-size: 12px; line-height: 1.5; color: var(--asc-text-2); }
/* ⚠️ 胶囊底色比卡片更暗，对比度要按**元素自身底色**算 → 用 #6e6e6e 而不是 #a0a0a0 */
.skin-need { margin: 0; font-size: 11.5px; line-height: 1.5; color: #6e6e6e; }
.skin-need.bad { color: #b6342c; }
.inner-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.inner-label { font-size: 12.5px; color: var(--asc-text-2); }
.seg { display: inline-flex; padding: 3px; gap: 2px; background: var(--asc-surface-2); border-radius: 9px; }
.seg-btn {
  border: 0; background: transparent; cursor: pointer;
  padding: 5px 12px; border-radius: 7px; font-size: 12.5px; color: #5f5f66;
  transition: all .15s ease;
}
.seg-btn:hover:not(:disabled) { color: var(--asc-text); }
.seg-btn.active { background: var(--asc-card); color: #6a48ee; font-weight: 600; box-shadow: 0 1px 3px rgba(16, 24, 40, .1); }
.seg-btn:disabled { color: var(--asc-text-3); cursor: not-allowed; text-decoration: line-through; }
.inner-hint { font-size: 12px; color: #6e6e6e; }
.inner-hint.bad { color: #b6342c; }
/* 变体胶囊里的色板点：swatch 是底色、ink 描边（深色底在浅界面上要描边才看得出形） */
.variant-btn { display: inline-flex; align-items: center; gap: 6px; }
.variant-dot {
  width: 12px; height: 12px; border-radius: 50%; flex: none;
  border: 1.5px solid var(--asc-border);
}
</style>
