<template>
  <div class="audio-bar" :class="{ 'is-playing': playing, 'is-error': !!errorText }">
    <!-- 可选用途标签（讲稿页里一个裸进度条不说清在放什么） -->
    <span v-if="label" class="ab-label">
      <svg viewBox="0 0 14 14" aria-hidden="true">
        <path d="M1.6 5.4h1.9l2.6-2.2v7.6L3.5 8.6H1.6z" fill="currentColor"/>
        <path d="M8.4 4.6a3.4 3.4 0 0 1 0 4.8M10.3 3a6 6 0 0 1 0 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" fill="none"/>
      </svg>
      {{ label }}
    </span>

    <!-- 播放 / 暂停：圆形主色按钮，比原生控件的三角更醒目 -->
    <button type="button" class="ab-play" :disabled="!!errorText"
            :title="playing ? '暂停' : '播放'" :aria-label="playing ? '暂停' : '播放'"
            @click="toggle">
      <svg v-if="!playing" viewBox="0 0 16 16" aria-hidden="true"><path d="M4.6 2.6 12.8 8l-8.2 5.4z" fill="currentColor"/></svg>
      <svg v-else viewBox="0 0 16 16" aria-hidden="true"><rect x="3.4" y="2.6" width="3.4" height="10.8" rx="1.2" fill="currentColor"/><rect x="9.2" y="2.6" width="3.4" height="10.8" rx="1.2" fill="currentColor"/></svg>
    </button>

    <span class="ab-time ab-time-cur">{{ fmt(curTime) }}</span>

    <!-- 进度：用原生 range 白拿「键盘可达 + 拖拽 + 触屏」，外观全自绘 -->
    <input class="ab-range" type="range" min="0" :max="dur || 0" step="0.1"
           :value="curTime" :style="{ '--ab-pct': pct + '%' }"
           aria-label="音轨播放进度"
           @input="onSeekInput" @change="dragging = false"
           @pointerdown="dragging = true" @pointerup="dragging = false"
           @pointercancel="dragging = false" />

    <span class="ab-time ab-time-total">{{ errorText || fmt(dur) }}</span>

    <div class="ab-ops">
      <button type="button" class="ab-skip" title="后退 15 秒" aria-label="后退 15 秒" @click="skip(-15)">−15s</button>
      <button type="button" class="ab-skip" title="前进 15 秒" aria-label="前进 15 秒" @click="skip(15)">+15s</button>
      <el-select v-model="rate" size="small" class="ab-rate" title="播放速度" aria-label="播放速度" @change="onRate">
        <el-option label="0.75x" :value="0.75" />
        <el-option label="1.0x" :value="1" />
        <el-option label="1.25x" :value="1.25" />
        <el-option label="1.5x" :value="1.5" />
        <el-option label="2.0x" :value="2" />
      </el-select>
    </div>

    <audio ref="el" :src="src" preload="metadata"
           @loadedmetadata="onMeta" @durationchange="onMeta"
           @timeupdate="onTime" @play="playing = true" @pause="playing = false"
           @ended="playing = false" @error="onError"></audio>
  </div>
</template>

<script setup>
// 作品详情「讲稿」页签旁的音轨播放条（2026-10-22 美化）。
//
// 为什么不用 <audio controls>：原生控件由浏览器绘制，配色/尺寸完全不受控 —— 在这页
// 就是一条近白的系统灰长条（实测 894×54），和整站的紫色设计语言不搭。这里只接管
// 「外观」，播放能力仍交给一个隐藏的 <audio>。
//
// ⚠️ 进度条刻意用原生 <input type="range"> 而不是 div + 手写拖拽：键盘左右键、
//    拖拽、触屏、无障碍语义都是免费拿到的；自绘 div 要自己补一遍且容易漏键盘。
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  src: { type: String, default: '' },
  // 服务端已知的时长（毫秒）：元数据到达前先用它，避免进度条 max=0 看起来是坏的
  durationMs: { type: Number, default: 0 },
  // 用途标签（如「音轨」），不传则不显示
  label: { type: String, default: '' },
})

const el = ref(null)
const playing = ref(false)
const curTime = ref(0)
const dur = ref(props.durationMs > 0 ? props.durationMs / 1000 : 0)
const rate = ref(1)
const dragging = ref(false)
const errorText = ref('')

const pct = computed(() => (dur.value > 0 ? Math.min(100, (curTime.value / dur.value) * 100) : 0))

function fmt(sec) {
  const s = Math.max(0, Math.floor(sec || 0))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

function toggle() {
  const a = el.value
  if (!a) return
  if (a.paused) {
    // play() 返回 Promise：媒体被浏览器策略拒绝时会 reject，不接住就是未捕获告警
    const p = a.play()
    if (p && typeof p.catch === 'function') p.catch(() => { playing.value = false })
  } else {
    a.pause()
  }
}

function onMeta() {
  const a = el.value
  if (!a) return
  // 流式音频的 duration 偶发 Infinity（边下边播算不出总长）→ 保留服务端给的时长
  if (Number.isFinite(a.duration) && a.duration > 0) dur.value = a.duration
  errorText.value = ''
}

function onTime() {
  const a = el.value
  if (!a || dragging.value) return
  curTime.value = a.currentTime || 0
}

function onSeekInput(e) {
  const v = Number(e.target.value) || 0
  curTime.value = v
  const a = el.value
  if (a && dur.value > 0) a.currentTime = Math.min(v, dur.value)
}

function skip(delta) {
  const a = el.value
  if (!a) return
  const max = dur.value || a.duration || 0
  a.currentTime = Math.max(0, Math.min(max, (a.currentTime || 0) + delta))
  curTime.value = a.currentTime
}

function onRate(v) {
  const a = el.value
  if (a) a.playbackRate = v || 1
}

function onError() {
  playing.value = false
  errorText.value = '音轨不可用'
}

// 音轨换了（重新合成后 audio_bytes 变 → URL 变）：复位进度与播放态，
// 否则进度条会停在上一条音频的位置（看起来像「新音频从中间开始」）
watch(() => props.src, () => {
  playing.value = false
  curTime.value = 0
  dur.value = props.durationMs > 0 ? props.durationMs / 1000 : 0
  errorText.value = ''
  const a = el.value
  if (a) {
    a.playbackRate = rate.value
    // 换了 src 后必须显式 load()，否则部分浏览器继续用旧缓冲
    try { a.load() } catch { /* 忽略 */ }
  }
})

onBeforeUnmount(() => {
  const a = el.value
  if (a) { try { a.pause() } catch { /* 忽略 */ } }
})
</script>

<style scoped>
/* 底色用白卡而不是 --asc-surface-2：这一栏在白色面板上，灰面会让进度条的灰轨道
   与背景几乎同色（实测肉眼分不出）→ 白底 + 深一档的轨道才有对比度。 */
.audio-bar {
  display: flex; align-items: center; gap: 12px;
  padding: 9px 14px;
  background: var(--asc-card);
  border: 1px solid var(--asc-border);
  border-radius: 12px;
  margin: 10px 0 4px;
}

/* ---- 用途标签 ---- */
.ab-label {
  flex: none; display: inline-flex; align-items: center; gap: 5px;
  font-size: 12px; color: var(--asc-text-2); font-weight: 500;
  padding-right: 2px;
}
.ab-label svg { width: 14px; height: 14px; }

/* ---- 播放键 ---- */
.ab-play {
  flex: none; width: 34px; height: 34px; border: none; border-radius: 50%;
  background: var(--asc-primary); color: #fff; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background .18s ease, transform .12s ease, box-shadow .18s ease;
  box-shadow: 0 2px 8px rgba(124, 92, 252, .28);
}
.ab-play svg { width: 15px; height: 15px; }
.ab-play:hover:not(:disabled) { background: var(--asc-primary-hover); box-shadow: 0 3px 12px rgba(124, 92, 252, .38); }
.ab-play:active:not(:disabled) { transform: scale(.94); }
.ab-play:disabled { opacity: .45; cursor: not-allowed; box-shadow: none; }
.ab-play:focus-visible { outline: 2px solid var(--asc-primary); outline-offset: 2px; }

/* ---- 时间 ---- */
.ab-time {
  flex: none; font-size: 12px; color: var(--asc-text-2);
  font-variant-numeric: tabular-nums; min-width: 34px;
}
.ab-time-cur { color: var(--asc-text); font-weight: 500; text-align: right; }
.is-error .ab-time-total { color: #b6342c; }

/* ---- 进度条 ---- */
.ab-range {
  flex: 1; min-width: 80px; height: 5px; margin: 0;
  border-radius: 3px; cursor: pointer; outline: none;
  -webkit-appearance: none; appearance: none;
  background: linear-gradient(to right,
    var(--asc-primary) 0 var(--ab-pct),
    var(--asc-border) var(--ab-pct) 100%);
}
/* 轨道：清掉浏览器默认绘制，只留上面那条渐变 */
.ab-range::-webkit-slider-runnable-track { height: 5px; border-radius: 3px; background: transparent; }
.ab-range::-moz-range-track { height: 5px; border-radius: 3px; background: transparent; }

/* 拖拽圆点：静止时收起（进度条更干净），hover / 键盘聚焦 / 正在播 时弹出 */
.ab-range::-webkit-slider-thumb {
  -webkit-appearance: none; appearance: none;
  width: 12px; height: 12px; border-radius: 50%; margin-top: -3.5px;
  background: #fff; border: 2px solid var(--asc-primary);
  box-shadow: 0 1px 4px rgba(0, 0, 0, .18);
  transform: scale(0); transition: transform .16s ease;
}
.ab-range::-moz-range-thumb {
  width: 12px; height: 12px; border-radius: 50%;
  background: #fff; border: 2px solid var(--asc-primary);
  box-shadow: 0 1px 4px rgba(0, 0, 0, .18);
}
.ab-range:hover::-webkit-slider-thumb,
.ab-range:focus-visible::-webkit-slider-thumb,
.is-playing .ab-range::-webkit-slider-thumb { transform: scale(1); }
.ab-range:focus-visible { outline: 2px solid var(--asc-primary); outline-offset: 4px; border-radius: 3px; }

/* ---- 右侧操作 ---- */
.ab-ops { flex: none; display: flex; align-items: center; gap: 6px; }
.ab-skip {
  min-width: 40px; height: 25px; padding: 0 8px;
  border: 1px solid var(--asc-border); border-radius: 13px;
  background: transparent; color: var(--asc-text-2);
  font-size: 11px; cursor: pointer; font-variant-numeric: tabular-nums;
  transition: border-color .18s ease, color .18s ease, background .18s ease;
}
.ab-skip:hover { border-color: var(--asc-primary); color: var(--asc-primary); background: var(--asc-primary-soft); }
.ab-skip:focus-visible { outline: 2px solid var(--asc-primary); outline-offset: 2px; }
.ab-rate { width: 78px; }

/* 触屏没有 hover：圆点常显，否则「能不能拖」完全看不出来 */
@media (hover: none) {
  .ab-range::-webkit-slider-thumb { transform: scale(1); }
}

/* 窄屏：进度条独占一行，操作区换到上一行右端 */
@media (max-width: 900px) {
  .audio-bar { flex-wrap: wrap; row-gap: 8px; }
  .ab-range { order: 1; flex-basis: 100%; }
  .ab-ops { margin-left: auto; }
}

@media (prefers-reduced-motion: reduce) {
  .ab-play, .ab-skip, .ab-range::-webkit-slider-thumb { transition: none; }
}
</style>
