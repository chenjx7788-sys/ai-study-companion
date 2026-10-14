<template>
  <div class="audio-picker">
    <div class="seg">
      <button
        type="button"
        class="seg-btn"
        :class="{ active: modelValue === 'podcast' }"
        @click="$emit('update:modelValue', 'podcast')"
      >
        复用播客音轨
      </button>
      <button
        type="button"
        class="seg-btn"
        :class="{ active: modelValue === 'self' }"
        @click="$emit('update:modelValue', 'self')"
      >
        本片单独生成
      </button>
    </div>

    <template v-if="modelValue === 'podcast'">
      <el-select
        :model-value="podcastId || null"
        filterable
        placeholder="选择一条已合成音频的播客"
        style="width: 100%"
        @update:model-value="(v) => $emit('update:podcastId', v || null)"
      >
        <el-option
          v-for="p in podcasts"
          :key="p.id"
          :value="p.id"
          :label="`${p.title}（${p.segment_count || 0} 句 · ${fmtDur(p.duration_sec)}）`"
        />
      </el-select>
      <p class="ap-hint">
        音轨与逐句时间轴直接引用这条播客，<b>不会重新合成</b>。
        画面的来源会跟随该播客（讲稿说什么、画面就指哪儿，两者必须同源）。
      </p>
      <p v-if="stalePodcast" class="ap-warn">
        这条播客的音轨与它当前文案不一致（音轨是上一版文案的产物）。先用它做视频，
        成片的字幕与画面会按<b>旧文案</b>走 —— 建议先去播客页重新合成。
      </p>
      <p v-if="!podcasts.length" class="ap-warn">
        还没有「已合成音频」的播客。可以去 AI 播客先生成音频，或改选「本片单独生成」。
      </p>
    </template>

    <!-- 自生成音轨的其余字段（来源 / 生成方式 / 时长 / 附加要求）由父组件提供 -->
    <slot v-else name="self" />
  </div>
</template>

<script setup>
import { computed } from 'vue'

// 音轨来源选择（PRD 2.1 硬性约束 2：复用播客时**绝不重新合成**）。
// ⚠️ 只列「已合成音频」的播客由**后端**保证（/videos/options 已过滤，含文件丢失的）——
//    前端不再自己过滤一遍：两处口径一定会漂移。
const props = defineProps({
  modelValue: { type: String, default: 'podcast' },
  podcastId: { type: [Number, String], default: null },
  podcasts: { type: Array, default: () => [] },
})
defineEmits(['update:modelValue', 'update:podcastId'])

const stalePodcast = computed(() => {
  const p = props.podcasts.find((x) => x.id === props.podcastId)
  return !!(p && p.audio_stale)
})

function fmtDur(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0))
  const m = Math.floor(s / 60)
  return m ? `${m} 分 ${s % 60} 秒` : `${s} 秒`
}
</script>

<style scoped>
.audio-picker { display: flex; flex-direction: column; gap: 10px; }
.seg {
  display: inline-flex; padding: 3px; gap: 2px;
  background: var(--asc-surface-2); border-radius: 9px; align-self: flex-start;
}
.seg-btn {
  border: 0; background: transparent; cursor: pointer;
  padding: 6px 14px; border-radius: 7px;
  font-size: 13px; color: #5f5f66;
  transition: all .15s ease;
}
.seg-btn:hover { color: var(--asc-text); }
.seg-btn.active { background: var(--asc-card); color: #6a48ee; font-weight: 600; box-shadow: 0 1px 3px rgba(16, 24, 40, .1); }
.seg-btn.active.blocked,
.seg-btn:disabled { color: var(--asc-text-3); cursor: not-allowed; }
.ap-hint { margin: 0; font-size: 12.5px; line-height: 1.65; color: var(--asc-text-2); }
.ap-hint b { color: var(--asc-text); font-weight: 600; }
.ap-warn {
  margin: 0; padding: 8px 10px; border-radius: 8px;
  font-size: 12.5px; line-height: 1.65;
  color: #8a5a00; background: #fff8e8; border: 1px solid #f2dfb4;
}
</style>
